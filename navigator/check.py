"""Invariant + golden regression suite. Run after ANY change:

  python -m navigator.check out/v7                       # invariants only
  python -m navigator.check out/v7 --golden out/golden   # + no regression vs golden snapshot
  python -m navigator.check out/h16 --golden out/golden --touched "Cambridge, MA"

Exit code 1 if any check fails. Every check is generic (no law-specific code)."""
from __future__ import annotations
import argparse, json, pathlib, re, sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(d):
    d = pathlib.Path(d)
    return {"rules": json.load(open(d / "rules.json"))["rules"], "ir": json.load(open(d / "rules_ir.json")),
            "lookups": json.load(open(d / "lookups.json")), "changes": json.load(open(d / "changes.json")),
            "relations": json.load(open(d / "relations.json")) if (d / "relations.json").exists() else []}


def run(d, golden=None, touched=()):
    import jsonschema
    from navigator.ground import citation_key, level_guard
    from navigator.normalize import citation_root
    from navigator.facts import build_fact_table
    o = load(d)
    res = []
    ok = lambda name, cond, detail="": res.append((name, bool(cond), detail))

    sch = json.load(open(ROOT / "schema/rule_record.schema.json"))
    errs = [(r["team_rule_id"], e.message[:80]) for r in o["rules"] for e in jsonschema.Draft202012Validator(sch).iter_errors(r)]
    ok("schema: 0 errors", not errs, errs[:3])
    addrs, _ = build_fact_table()
    ok("lookups: all 500 addresses", set(o["lookups"]["lookups"]) == set(addrs), len(o["lookups"]["lookups"]))
    ids = {r["team_rule_id"] for r in o["rules"]}
    dangling = {x["team_rule_id"] for v in o["lookups"]["lookups"].values() for x in v} - ids
    ok("lookups: no dangling rule ids", not dangling, sorted(dangling)[:5])
    bad_q = [r["team_rule_id"] for r in o["ir"] if not r.get("quoted_span")]
    ok("grounding: 100% verbatim quotes", not bad_q, bad_q[:5])
    prov = [r["team_rule_id"] for r in o["ir"] if not (r.get("source_doc_id") and r.get("source_url") and r.get("retrieved_at"))]
    ok("provenance: doc id + url + retrieval date on every rule", not prov, prov[:5])
    lvl = [r["team_rule_id"] for r in o["ir"] if level_guard(r["level"], r.get("raw_citation") or "")]
    ok("level: no state law reported as a city rule", not lvl, lvl[:5])
    dup = [k for k, n in Counter((r["jurisdiction"], r["category"], citation_key(citation_root(r["citation"]))) for r in o["ir"]).items() if n > 1]
    ok("identity: no duplicate law within a cell", not dup, dup[:3])
    dates = [r["team_rule_id"] for r in o["ir"] if r.get("effective_date") and r.get("effective_date_provenance") in (None, "missing")]
    ok("dates: every date has provenance", not dates, dates[:5])
    st = [r["team_rule_id"] for r in o["ir"] if r["status"] == "in_force" and r.get("legal_stage") not in ("enacted", None)]
    ok("status: in_force only for enacted laws", not st, st[:5])
    conf = [(x["state_rule"], x["local_rule"]) for x in o["relations"] if x["type"] in ("possible_conflict", "preempts_local") and not x.get("evidence_quote")]
    ok("relations: every conflict has evidence", not conf, conf[:3])
    failed_in_lookups = [x["team_rule_id"] for v in o["lookups"]["lookups"].values() for x in v
                         if next((r for r in o["rules"] if r["team_rule_id"] == x["team_rule_id"]), {}).get("status") == "failed"]
    ok("lookups: failed measures never reported", not failed_in_lookups, failed_in_lookups[:3])
    exp = json.load(open(ROOT / "spike/expected_tsets.json"))
    for t in ["T1", "T2", "T3", "T4", "T5"]:
        if t in o["changes"]:
            a, e = set(o["changes"][t]["affected_address_ids"]), set(exp[t]["affected"])
            j = 1.0 if not a and not e else len(a & e) / len(a | e)
            cf, ce = set(o["changes"][t].get("conflict_flag_address_ids", [])), set(exp[t].get("conflict", []))
            jc = 1.0 if not cf and not ce else len(cf & ce) / len(cf | ce)
            ok(f"changes: {t} matches public spec (Jaccard)", j == 1.0 and jc == 1.0, f"affected {j:.3f} conflict {jc:.3f}")

    if golden:
        g = load(golden)
        norm = lambda v: sorted((x["team_rule_id"], x["result"], x["conflict_flag"]) for x in v)
        touched = set(touched)
        drift = [aid for aid, ad in addrs.items() if ad["city"] not in touched and ad["state"] not in touched
                 and norm(g["lookups"]["lookups"].get(aid, [])) != norm(o["lookups"]["lookups"].get(aid, []))]
        ok("golden: untouched jurisdictions unchanged", not drift, f"{len(drift)} drifted, e.g. {drift[:4]}")
        gc = Counter(r["jurisdiction"] for r in g["rules"])
        oc = Counter(r["jurisdiction"] for r in o["rules"])
        lost = {j: (gc[j], oc[j]) for j in gc if oc[j] < gc[j]}
        ok("golden: no jurisdiction lost rules", not lost, lost)
        cell = lambda rs: {(r["jurisdiction"], r["category"]): set() for r in rs}
        gcit, ocit = cell(g["rules"]), cell(o["rules"])
        for r in g["rules"]: gcit[(r["jurisdiction"], r["category"])].add(r["citation"])
        for r in o["rules"]: ocit[(r["jurisdiction"], r["category"])].add(r["citation"])
        worse = [(k, sorted(gcit[k]), sorted(ocit.get(k, set()))) for k in gcit
                 if k[0] not in touched and gcit[k] != ocit.get(k, set())]
        ok("golden: citations unchanged outside touched", not worse, worse[:3])

    width = max(len(n) for n, _, _ in res)
    for n, passed, detail in res:
        print(f"{'PASS' if passed else 'FAIL'}  {n:{width}s}  {'' if passed else detail}")
    nf = sum(1 for _, p, _ in res if not p)
    print(f"\n{len(res) - nf}/{len(res)} checks passed")
    return nf == 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--golden")
    ap.add_argument("--touched", action="append", default=[])
    a = ap.parse_args()
    sys.exit(0 if run(a.out_dir, a.golden, a.touched) else 1)
