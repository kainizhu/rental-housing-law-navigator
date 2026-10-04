"""Precompute everything the static UI shows (no LLM calls, deterministic):

  python3 -m navigator.ui_data --run out/v8 --out web/data.js [--rehearsal out/h16_A]

The UI never re-implements the evaluator: every result it shows was produced by navigator.evaluate.
The legal state only changes on dates where some rule's effective/sunset date falls, so results are
snapshotted on exactly those dates (the as-of slider snaps to them)."""
from __future__ import annotations
import argparse, collections, datetime as dt, json, pathlib, re, subprocess, sys
from navigator.evaluate import evaluate, Policy
from navigator.facts import build_fact_table, fact_table_json
from navigator.pipeline import ir_view
from navigator.ground import load_corpus

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_AS_OF = "2026-10-01"
RES = ["applies", "unknown", "superseded", "pending", "not_yet_effective"]
REASONS = [  # (code, regex on a note) -> why a result is unknown; first match wins
    ("year_built_missing", r"year built missing"),
    ("units_missing", r"unit count missing"),
    ("units_hint_not_used", r"units: .*not used as a fact"),
    ("unobservable_coverage", r"coverage (depends on|limited to)"),
    ("local_coverage_unknown", r"whether local rule .* covers this building is unknown"),
    ("relative_date", r"effective date is relative"),
]


def _load(p):
    d = json.load(open(p))
    return d["rules"] if isinstance(d, dict) and "rules" in d else d


def snapshot_dates(rules):
    ds = {DEFAULT_AS_OF}
    for r in rules:
        for k in ("effective_date", "sunset_date"):
            v = r.get(k)
            if v and "2023-01-01" <= v <= "2028-12-31":
                ds.add((v + "-01-01")[:10] if len(v) == 4 else (v + "-01")[:10])  # month precision -> first day
    ds = sorted(ds)
    first = (dt.date.fromisoformat(ds[0]) - dt.timedelta(days=1)).isoformat()
    return [first] + ds


def reason_of(notes):
    for code, rx in REASONS:
        if any(re.search(rx, n) for n in notes):
            return code
    return "other"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(ROOT / "out/v8"))
    ap.add_argument("--out", default=str(ROOT / "web/data.js"))
    ap.add_argument("--rehearsal", default=str(ROOT / "out/h16_A"))
    a = ap.parse_args()
    run = pathlib.Path(a.run)
    ir = _load(run / "rules_ir.json")
    schema = {r["team_rule_id"]: r for r in _load(run / "rules.json")}
    relations = json.load(open(run / "relations.json"))
    addrs, cal = build_fact_table()
    corpus = load_corpus()

    notes_tab, notes_ix = [], {}

    def nid(s):
        if s not in notes_ix:
            notes_ix[s] = len(notes_tab); notes_tab.append(s)
        return notes_ix[s]

    def compact(res):
        out = {}
        for aid, v in res.items():
            out[aid] = [[x["team_rule_id"], RES.index(x["result"]), int(x["conflict_flag"]),
                         [nid(n) for n in x["notes"]], [nid(n) for n in x.get("conflict_notes", [])]] for x in v]
        return out

    rules_v = [ir_view(r) for r in ir]
    dates = snapshot_dates(ir)
    snaps = {d: compact(evaluate(addrs, rules_v, relations, d, Policy())) for d in dates}
    print("snapshots:", dates, file=sys.stderr)

    # policy sensitivity at the default date: which results move if inferred facts may (or may never) decide
    base = snaps[DEFAULT_AS_OF]
    sens = {}
    for pol in ("none", "all"):
        alt = compact(evaluate(addrs, rules_v, relations, DEFAULT_AS_OF, Policy(hints_decide=pol)))
        diff = []
        for aid in addrs:
            b = {x[0]: x[1] for x in base.get(aid, [])}
            c = {x[0]: x[1] for x in alt.get(aid, [])}
            for rid in sorted(set(b) | set(c)):
                if b.get(rid) != c.get(rid):
                    diff.append([aid, rid, b.get(rid, -1), c.get(rid, -1)])
        sens[pol] = diff

    # unknown reasons at default date
    reasons = collections.Counter()
    for aid, v in base.items():
        for x in v:
            if RES[x[1]] == "unknown":
                reasons[reason_of([notes_tab[i] for i in x[3]])] += 1

    rules_out = []
    for r in ir:
        s = schema.get(r["team_rule_id"], {})
        doc = corpus.get(r.get("source_doc_id"))
        conds = []
        for kind in ("coverage_conditions_ir", "exemptions_ir"):
            for c in r.get(kind) or []:
                conds.append({"k": "cov" if kind.startswith("cov") else "ex", "fact": c.get("fact"), "op": c.get("op"),
                              "value": c.get("value"), "text": c.get("text"), "quote": c.get("quote"), "role": c.get("role"),
                              "group": c.get("group"), "original": c.get("original")})
        rules_out.append({
            "id": r["team_rule_id"], "j": r["jurisdiction"], "level": r["level"], "cat": r["category"],
            "status": r["status"], "stage": r.get("legal_stage"), "title": r.get("title"), "req": r.get("requirement"),
            "kv": r.get("key_value"), "eff": r.get("effective_date"), "eff_prov": r.get("effective_date_provenance"),
            "eff_note": r.get("effective_date_note"), "sunset": r.get("sunset_date"),
            "cit": s.get("citation") or r.get("citation"), "raw_cit": r.get("raw_citation"), "aliases": s.get("citation_aliases"),
            "doc": r.get("source_doc_id"), "url": r.get("source_url"), "retrieved": r.get("retrieved_at"),
            "origin": r.get("source_origin"), "doc_type": doc.source_type if doc else None,
            "quote": r.get("quoted_span"), "qmatch": r.get("quote_match"), "conf": r.get("confidence"),
            "conflict": r.get("conflict_flag"), "conflict_note": r.get("conflict_note"), "interaction": r.get("interaction"),
            "penalty": r.get("penalty"), "conds": conds, "other_dates": r.get("other_dates") or []})

    cells = [json.loads(l) for l in open(run / "cells_after_gap_stage.jsonl")]
    grid = [{"j": c["jurisdiction"], "cat": c["category"], "finding": c.get("cell_finding"), "docs": c.get("doc_ids")} for c in cells]

    addr_out = {}
    for aid, ad in fact_table_json(addrs).items():
        addr_out[aid] = {"street": ad["street_address"], "postal": ad["postal_city"], "state": ad["state"], "city": ad["city"],
                         "use_code": ad["use_code"], "use": ad["use_description"],
                         "facts": {k: v for k, v in ad["facts"].items()}}

    changes = json.load(open(run / "changes.json"))
    tests = json.load(open(ROOT / "dev/change_tests.json")) if (ROOT / "dev/change_tests.json").exists() else None
    reh = None
    rp = pathlib.Path(a.rehearsal)
    if (rp / "changes.json").exists():
        rc = json.load(open(rp / "changes.json"))
        rr = [x for x in _load(rp / "rules.json") if x["jurisdiction"] == "Cambridge, MA"]
        reh = {"changes": {k: v for k, v in rc.items() if k not in changes},
               "new_rules": [{"id": x["team_rule_id"], "cit": x["citation"], "status": x["status"], "eff": x["effective_date"],
                              "conflict": x["conflict_flag"], "quote": x["quoted_span"], "kv": x.get("key_value")}
                             for x in rr if (x.get("source_doc_id") or "").startswith("SYN")]}

    chk = subprocess.run([sys.executable, "-m", "navigator.check", str(run), "--golden", str(ROOT / "out/golden")],
                         capture_output=True, text=True, cwd=ROOT).stdout
    checks = [{"ok": l.startswith("PASS"), "name": l[5:].strip()} for l in chk.splitlines() if l[:4] in ("PASS", "FAIL")]

    review = {"dropped": json.load(open(run / "review_dropped.json")),
              "decisions": json.load(open(run / "review_decisions.json"))}
    audit = [json.loads(l) for l in open(ROOT / "logs/audit.jsonl")]
    stats = {"llm_calls": len(audit), "input_tokens": sum((x.get("usage") or {}).get("input_tokens", 0) for x in audit),
             "output_tokens": sum((x.get("usage") or {}).get("output_tokens", 0) for x in audit),
             "docs": len(corpus), "docs_by_origin": dict(collections.Counter(d.origin for d in corpus.values())),
             "calibration": cal}

    data = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "as_of": DEFAULT_AS_OF, "res": RES,
            "dates": dates, "snaps": snaps, "notes": notes_tab, "sens": sens, "unknown_reasons": dict(reasons),
            "rules": rules_out, "relations": relations, "grid": grid, "addresses": addr_out, "changes": changes,
            "tests": tests, "rehearsal": reh, "checks": checks, "review": review, "stats": stats}
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("window.NAV=" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"wrote {out} {out.stat().st_size/1e6:.2f} MB; {len(dates)} snapshots; {len(notes_tab)} notes", file=sys.stderr)


if __name__ == "__main__":
    main()
