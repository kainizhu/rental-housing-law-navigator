"""Compare normalized rules with the brief-derived silver key (spike/expectations.yaml).
Strict match: same jurisdiction + category + normalized citation key.
Loose match: same jurisdiction + category + shared section number / bill number token.
Reports recall, extra rules per cell, and field agreement (status, effective_date)."""
import json, re, sys, pathlib
import yaml
from navigator.ground import normalize_citation, citation_key
from navigator.normalize import normalize

ROOT = pathlib.Path(__file__).resolve().parents[1]
exp = yaml.safe_load(open(ROOT / "spike/expectations.yaml"))
silver = exp["silver_rules"]


def tokens(c):
    return set(re.findall(r"\d+[a-z]?(?:[.:\-]\d+[a-z]?)*|½", (c or "").lower()))


def main(path):
    cells = [json.loads(l) for l in open(path)]
    rules, audit = normalize(cells)
    by_cell = {}
    for r in rules:
        by_cell.setdefault((r["jurisdiction"], r["category"]), []).append(r)
    hit_s = hit_l = 0
    rows = []
    for s in silver:
        cand = by_cell.get((s["jur"], s["cat"]), [])
        sk = citation_key(normalize_citation(s["cite"])[0])
        strict = [r for r in cand if citation_key(r["citation"]) == sk]
        loose = strict or [r for r in cand if tokens(s["cite"]) & tokens(r["citation"] + " " + (r.get("raw_citation") or ""))]
        m = (strict or loose or [None])[0]
        hit_s += bool(strict); hit_l += bool(loose)
        st_ok = m is not None and m["status"] == s["status"]
        ed = s.get("effective_date")
        ed_ok = m is not None and ("effective_date" not in s or (m["effective_date"] or None) == ed)
        rows.append((s["id"], "S" if strict else "L" if loose else "-", m["citation"] if m else "", m["status"] if m else "",
                     f"{m['effective_date']}" if m else "", "ok" if st_ok else "STATUS", "ok" if ed_ok else f"DATE(exp {ed})"))
    for r in rows:
        print(f"{r[0]:13s} {r[1]} {r[2][:40]:40s} {r[3]:18s} {r[4]:11s} {r[5]:6s} {r[6]}")
    n = len(silver)
    print(f"\nrecall strict {hit_s}/{n}  loose {hit_l}/{n}")
    print(f"total rules {len(rules)}; cells with >2 rules:",
          [(k, len(v)) for k, v in by_cell.items() if len(v) > 2])
    nq = sum(1 for r in rules if not r["quoted_span"])
    print(f"ungrounded quotes {nq}/{len(rules)}; audit actions:", {a['action'] for a in audit}, len(audit))
    for z in exp["silver_no_rule_cells"]:
        bad = [r["citation"] for r in by_cell.get((z["jur"], z["cat"]), []) if r["status"] == "in_force"]
        print("no-rule cell", z["jur"], z["cat"], "OK" if not bad else f"VIOLATION {bad}")
    return rules


if __name__ == "__main__":
    main(sys.argv[1])
