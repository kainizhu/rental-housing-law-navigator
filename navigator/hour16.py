"""One command for a new document (hour-16 ordinance or any new law):

  python -m navigator.hour16 --manifest NEW/manifest.csv [--tests NEW/tests.json] --base out/extract_x4d_full.jsonl --out out/h16

1. load the new manifest (docs + jurisdictions); 2. re-extract ONLY the jurisdictions it touches
(all 6 categories, same prompt, same code); 3. merge with the base extraction; 4. gap pass, stage check,
category review, relation extraction; 5. write rules/lookups/changes; 6. verify that lookups for every
address outside the touched jurisdictions are identical to the baseline (no collateral changes)."""
from __future__ import annotations
import argparse, csv, json, os, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--tests", help="extra change tests JSON (list), e.g. the T6 spec")
    ap.add_argument("--addresses", help="extra addresses CSV (same columns as data/sample_addresses.csv)")
    ap.add_argument("--base", default=str(ROOT / "out/extract_x4d_full.jsonl"))
    ap.add_argument("--baseline-lookups", default=str(ROOT / "out/baseline/lookups.json"))
    ap.add_argument("--out", default=str(ROOT / "out/h16"))
    ap.add_argument("--frozen", default=str(ROOT / "out/baseline"), help="baseline pipeline output dir to reuse outside touched jurisdictions")
    a = ap.parse_args()
    if a.addresses:
        os.environ["NAV_EXTRA_ADDRESSES"] = a.addresses
    os.environ["NAV_EXTRA_MANIFESTS"] = ",".join(filter(None, [os.environ.get("NAV_EXTRA_MANIFESTS", ""), a.manifest]))

    from navigator.ground import load_corpus
    from navigator.extract import extract_cell, CATEGORIES
    from navigator.pipeline import build
    corpus = load_corpus()
    touched = sorted({j.strip() for r in csv.DictReader(open(a.manifest)) for j in r["jurisdictions"].split(";") if j.strip()})
    print("new documents touch:", touched, file=sys.stderr)
    out_dir = pathlib.Path(a.out); out_dir.mkdir(parents=True, exist_ok=True)
    new_cells = {}
    for j in touched:
        for c in CATEGORIES:
            r = extract_cell(corpus, j, c)
            new_cells[(j, c)] = r
            print(f"  {j} | {c}: {r.get('cell_finding')} rules={len(r.get('rules', []))}", file=sys.stderr)
    merged = []
    for l in open(a.base):
        c = json.loads(l)
        merged.append(new_cells.pop((c["jurisdiction"], c["category"]), c))
    merged += list(new_cells.values())
    ext = out_dir / "extractions_merged.jsonl"
    with open(ext, "w") as f:
        for c in merged:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    extra = json.load(open(a.tests)) if a.tests else None
    rules, lookups, changes = build([str(ext)], out_dir, review=True, extra_tests=extra, frozen_from=a.frozen, touched=touched, refine_rules=True)

    # collateral-change check against the baseline submission
    base = json.load(open(a.baseline_lookups))["lookups"]
    from navigator.facts import build_fact_table
    addrs, _ = build_fact_table()
    norm = lambda v: sorted((x["team_rule_id"], x["result"], x["conflict_flag"]) for x in v)
    changed_outside = [aid for aid, ad in addrs.items()
                       if ad["city"] not in touched and ad["state"] not in touched
                       and norm(base.get(aid, [])) != norm(lookups["lookups"].get(aid, []))]
    print(f"\nrules {len(rules)}; addresses changed OUTSIDE touched jurisdictions: {len(changed_outside)} {changed_outside[:5]}")
    for k, v in changes.items():
        print(k, "affected", len(v["affected_address_ids"]), "conflicts", len(v["conflict_flag_address_ids"]), "|", v["notes"][:140])
    new_rules = [r for r in rules if r["jurisdiction"] in touched and r.get("source_doc_id", "").startswith(tuple(
        x["doc_id"] for x in csv.DictReader(open(a.manifest))))]
    for r in new_rules:
        print("NEW RULE", r["team_rule_id"], r["status"], r["citation"], "| eff", r["effective_date"], r["effective_date_provenance"],
              "| cov", [(c["fact"], c["op"], c.get("value")) for c in r["coverage_conditions_ir"] if c["fact"] not in ("other", "building_type")],
              "| conflict", r["conflict_flag"])


if __name__ == "__main__":
    main()
