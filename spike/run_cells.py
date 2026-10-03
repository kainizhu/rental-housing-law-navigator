"""Run a named list of cells, grouped by jurisdiction (sequential within a jurisdiction so the
shared document prefix is cached), and print a compact review."""
import json, sys, os
from concurrent.futures import ThreadPoolExecutor
from navigator.ground import load_corpus, locate, normalize_citation, level_guard
from navigator.extract import extract_cell
CELLS9 = [("MA","application_screening_fees"),("MA","security_deposits"),("MA","algorithmic_rent_setting"),
          ("NJ","security_deposits"),("Cambridge, MA","rent_increase_limits"),("Los Angeles, CA","rent_increase_limits"),
          ("San Francisco, CA","security_deposits"),("San Francisco, CA","algorithmic_rent_setting"),
          ("Berkeley, CA","application_screening_fees")]
def run(cells, out):
    C = load_corpus()
    by = {}
    for j, c in cells: by.setdefault(j, []).append(c)
    def job(j): return [extract_cell(C, j, c) for c in by[j]]
    with ThreadPoolExecutor(4) as ex: res = [r for rs in ex.map(job, by) for r in rs]
    with open(out, "w") as f:
        for r in res: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r in res:
        print(f"\n## {r['jurisdiction']} | {r['category']} -> {r.get('cell_finding')} n={len(r['rules'])} usage={r.get('usage')}")
        for x in r["rules"]:
            d = C.get(x["source_doc_id"]); loc = locate(x["rule_quote"], d.text, d.body_start) if d else None
            n,k = normalize_citation(x["raw_citation"]); g = level_guard(r["level"], x["raw_citation"]); e = x["effective"]
            print(f"  - {n} [{k}] {x['legal_stage']} | kv={str(x.get('key_value'))[:70]} | quote={loc.mode if loc else 'NO_DOC'} | eff={e.get('kind')}:{e.get('explicit_date') or e.get('anchor_date')}" + (f" | GUARD {g['flag']}" if g else ""))
            print(f"    cov={[(c['fact'],c['op'],c.get('value')) for c in x['coverage_conditions'] if c['fact']!='other']} exm={[(c['fact'],c['op'],c.get('value'),c.get('group')) for c in x['exemptions'] if c['fact']!='other']}")
if __name__ == "__main__":
    run(CELLS9, sys.argv[1])
