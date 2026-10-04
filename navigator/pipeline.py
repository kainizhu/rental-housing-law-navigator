"""extractions.jsonl -> rules.json, lookups.json, changes.json (+ internal rules_ir.json).

Change engine is generic over test types in dev/change_tests.json:
  as_of     affected = addresses whose result for the test's rules differs between as_of_before and as_of_after
  boundary  affected = addresses where the test's rules apply (applies/unknown/superseded) at as_of
  pending   affected = addresses that WOULD be covered if the pending rules were in force
  negative  affected = addresses where the (failed) rules still produce a result (should be empty)
Test rule ids from the organizers (e.g. 'HOB-ALG-01', 'MA-ALG-P1') are mapped to our rules by
jurisdiction code + category code + stage class (P = not enacted)."""
from __future__ import annotations
import argparse, json, os, pathlib, re
from navigator.facts import build_fact_table
from navigator.normalize import normalize, to_schema, jur_code, CAT_CODE
from navigator.evaluate import evaluate, Policy

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_AS_OF = "2026-10-01"
PRESENT = {"applies", "unknown", "superseded", "not_yet_effective", "pending"}


UNKNOWN_REASONS = [  # (tag, substring in evaluator notes) - first match wins
    ("boundary_year", "same year as the"),
    ("building_age_boundary", "building age straddles"),
    ("depends_on_local_coverage", "whether local rule"),
    ("missing_building_fact", "year built missing"),
    ("missing_building_fact", "unit count missing"),
    ("inferred_fact_not_used", "not used as a fact"),
    ("unobservable_fact", "which is not in the data"),
]


def unknown_reason(explanation: str) -> str:
    for tag, needle in UNKNOWN_REASONS:
        if needle in (explanation or ""):
            return tag
    return "other"


def finalize(rules):
    """Deterministic guards applied to refined records:
    - a refined citation is kept only if it UPGRADES a law-name citation (kind 'raw') to a recognized code citation;
      it never replaces an existing code citation;
    - unknown-reason taxonomy is attached at lookup time (see results())."""
    from navigator.ground import normalize_citation
    for r in rules:
        ch = (r.get("refined") or {}).get("citation")
        if ch:
            old, new = ch
            old_kind = normalize_citation(old)[1]
            new_kind = normalize_citation(new)[1]
            if not (old_kind == "raw" and new_kind not in (None, "raw")):
                r["citation"] = old
                r["citation_kind"] = old_kind
                r["refined"]["citation_reverted"] = True
    return rules


def ir_view(r):
    return {**r, "coverage_conditions": r["coverage_conditions_ir"], "exemptions": r["exemptions_ir"]}


def _jur_matches(code: str, jurisdiction: str) -> bool:
    """Accept the organizers' jurisdiction prefix in any common abbreviation style:
    'CA'/'NJ'/'MA' for states; for cities our code, initials, or any 3-5 letter prefix
    ('CAM', 'CAMB', 'CMB' for Cambridge; 'NWK'/'NEW' for Newark; 'SF'; 'LA'; 'BRK'/'BER')."""
    code = code.upper()
    if len(jurisdiction) == 2:
        return code == jurisdiction
    city = jurisdiction.split(",")[0].upper()
    letters = "".join(ch for ch in city if ch.isalpha())
    cands = {jur_code(jurisdiction), "".join(w[0] for w in city.split()), letters[:3], letters[:4], letters[:5]}
    if code in cands:
        return True
    # consonant-skeleton abbreviations (CMB, NWK, BRK, HBK): letters of code appear in order in the name
    it = iter(letters)
    return len(code) >= 3 and code[0] == letters[0] and all(ch in it for ch in code)


def _fuzzy_cat(cc: str):
    cc = cc.upper()
    table = {"RENT": "rent_increase_limits", "RC": "rent_increase_limits", "JC": "just_cause_eviction", "EVICT": "just_cause_eviction",
             "DEP": "security_deposits", "SD": "security_deposits", "FEE": "application_screening_fees", "FEES": "application_screening_fees",
             "SCR": "screening_restrictions", "SCREEN": "screening_restrictions", "ALG": "algorithmic_rent_setting", "ALGO": "algorithmic_rent_setting"}
    return table.get(cc)


def map_test_rule(tid: str, rules: list):
    """'HOB-ALG-01' -> our rules in (Hoboken, algorithmic_rent_setting) that are enacted;
    'MA-ALG-P1' -> not-enacted rules in that cell (pending or failed)."""
    m = re.fullmatch(r"([A-Z]+)-([A-Z]+)-(P?)(\d+)", tid)
    if not m:
        return []
    jc, cc, p, _ = m.groups()
    cat = {v: k for k, v in CAT_CODE.items()}.get(cc) or _fuzzy_cat(cc)
    out = []
    for r in rules:
        if r["category"] != cat or not _jur_matches(jc, r["jurisdiction"]):
            continue
        if bool(p) == (r.get("legal_stage") != "enacted"):
            out.append(r["team_rule_id"])
    return out


def results(addresses, rules, relations, as_of, force=None):
    out = evaluate(addresses, [ir_view(r) for r in rules], relations, as_of, Policy(), force_status=force)
    return {aid: {x["team_rule_id"]: x for x in v} for aid, v in out.items()}


def run_change_tests(addresses, rules, relations, tests):
    changes = {}
    for t in tests:
        ids = sorted({i for tid in t["rule_ids"] for i in map_test_rule(tid, rules)})
        conflict_ids = sorted({i for tid in t.get("conflict_with", []) for i in map_test_rule(tid, rules)})
        typ = t.get("type") or ("as_of" if "as_of_before" in t else "boundary")
        note = {"mapped_rules": ids, "mapped_conflict_rules": conflict_ids}
        if not ids:
            changes[t["test_id"]] = {"affected_address_ids": [], "conflict_flag_address_ids": [],
                                     "notes": f"No extracted rule maps to {t['rule_ids']}", **note}
            continue
        if typ == "as_of":
            b = results(addresses, rules, relations, t["as_of_before"])
            a = results(addresses, rules, relations, t["as_of_after"])
            aff = sorted(aid for aid in addresses
                         if any((b[aid].get(i) or {}).get("result") != (a[aid].get(i) or {}).get("result") for i in ids))
            cur = a
            detail = {aid: {i: [(b[aid].get(i) or {}).get("result"), (a[aid].get(i) or {}).get("result")] for i in ids}
                      for aid in aff}
        elif typ == "pending":
            cur = results(addresses, rules, relations, t["as_of"], force={i: "in_force" for i in ids})
            aff = sorted(aid for aid in addresses if any((cur[aid].get(i) or {}).get("result") in ("applies", "unknown", "superseded") for i in ids))
            detail = None
        else:  # boundary / negative / anything else: evaluate as of the test date
            cur = results(addresses, rules, relations, t.get("as_of", DEFAULT_AS_OF))
            aff = sorted(aid for aid in addresses if any((cur[aid].get(i) or {}).get("result") in PRESENT for i in ids))
            detail = None
        cf = sorted(aid for aid in aff if any((cur[aid].get(c) or {}).get("result") in PRESENT for c in conflict_ids)) if conflict_ids else []
        changes[t["test_id"]] = {"affected_address_ids": aff, "conflict_flag_address_ids": cf,
                                 "notes": f"{typ}: rules {ids}" + (f"; conflicts with {conflict_ids}" if conflict_ids else ""),
                                 **note, **({"before_after": detail} if detail else {})}
    return changes


def build(extractions, out_dir, as_of=DEFAULT_AS_OF, relations_path=None, tests_path=None, extra_tests=None, review=False,
          frozen_from=None, touched=None, refine_rules=False):
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    cells = [json.loads(l) for p in extractions for l in open(p)]
    if review:
        from navigator.ground import load_corpus
        from navigator.extract import gap_pass, verify_stages
        corpus = load_corpus()
        if frozen_from and touched:
            t_cells = [c for c in cells if c["jurisdiction"] in touched]
            o_cells = [json.loads(l) for l in open(pathlib.Path(frozen_from) / "cells_after_gap_stage.jsonl")
                       if json.loads(l)["jurisdiction"] not in touched]
            cells = o_cells + verify_stages(corpus, gap_pass(corpus, t_cells))
        else:
            cells = verify_stages(corpus, gap_pass(corpus, cells))
        with open(out_dir / "cells_after_gap_stage.jsonl", "w") as f:
            for c in cells: f.write(json.dumps(c, ensure_ascii=False) + "\n")
    rules, audit = normalize(cells, as_of=as_of)
    if refine_rules:
        from navigator.refine import refine
        from navigator.ground import load_corpus
        only = set(touched) if (frozen_from and touched) else None
        rules, rlog = refine(rules, load_corpus(), as_of=as_of, only=only)
        json.dump(rlog, open(out_dir / "refine_log.json", "w"), indent=1, ensure_ascii=False)
    dropped = []
    if review:
        from navigator.review import review_categories, apply_category_review, extract_relations, validate_relations
        if frozen_from:
            # incremental mode: reuse baseline review decisions/relations outside the touched jurisdictions
            fz = pathlib.Path(frozen_from)
            base_dec = json.load(open(fz / "review_decisions.json"))
            base_rel = json.load(open(fz / "relations.json"))
            touched_set = set(touched or [])
            in_t = lambda r: r["jurisdiction"] in touched_set
            decisions = {k: v for k, v in base_dec.items()}
            decisions.update(review_categories([r for r in rules if in_t(r)]))
            rules, dropped = apply_category_review(rules, decisions)
            new_rel = validate_relations(extract_relations([r for r in rules if in_t(r) or r["level"] == "state"]), rules)
            new_rel = [x for x in new_rel if x.get("city") in touched_set]
            relations = [x for x in base_rel if x.get("city") not in touched_set] + new_rel
        else:
            from navigator.review import review_categories_voted, extract_relations_voted
            n = int(os.environ.get("NAV_VOTES", "1"))
            decisions = review_categories_voted(rules, n) if n > 1 else review_categories(rules)
            rules, dropped = apply_category_review(rules, decisions)
            relations = validate_relations(extract_relations_voted(rules, n) if n > 1 else extract_relations(rules), rules)
        # condition roles (scope / expansion / variant / exemption) - majority vote; frozen outside touched
        from navigator.roles import classify, apply_roles
        n_votes = max(3, int(os.environ.get("NAV_VOTES", "3")))
        if frozen_from and (pathlib.Path(frozen_from) / "roles.json").exists():
            roles = json.load(open(pathlib.Path(frozen_from) / "roles.json"))
            roles.update(classify(rules, n_votes, only=set(touched or [])))
        else:
            roles = classify(rules, n_votes)
        json.dump(roles, open(out_dir / "roles.json", "w"), indent=1, ensure_ascii=False)
        before = {r["team_rule_id"] for r in rules}
        rules, role_dropped = apply_roles(rules, roles)
        dropped += role_dropped
        relations = [x for x in relations if x["state_rule"] in {r["team_rule_id"] for r in rules} and x["local_rule"] in {r["team_rule_id"] for r in rules}]
        json.dump(decisions, open(out_dir / "review_decisions.json", "w"), indent=1, ensure_ascii=False)
        json.dump(dropped, open(out_dir / "review_dropped.json", "w"), indent=1, ensure_ascii=False)
        json.dump(relations, open(out_dir / "relations.json", "w"), indent=1, ensure_ascii=False)
    else:
        relations = json.load(open(relations_path)) if relations_path and pathlib.Path(relations_path).exists() else []
    addresses, calib = build_fact_table()
    res = results(addresses, rules, relations, as_of)
    # rule-level conflict notes from relations
    for rel in relations:
        for r in rules:
            if r["team_rule_id"] in (rel["state_rule"], rel["local_rule"]) and rel["type"] in ("possible_conflict", "preempts_local"):
                r["conflict_flag"] = True
                r["conflict_note"] = (r["conflict_note"] + " | " if r["conflict_note"] else "") + (rel.get("note") or rel["type"])
            if r["team_rule_id"] == rel["state_rule"] and rel["type"] == "local_governs_where_covered":
                r["overrides"].append(rel["local_rule"]); r["interaction"] = f"yields to {rel['local_rule']} where that rule covers the unit"
            if r["team_rule_id"] == rel["local_rule"] and rel["type"] == "local_governs_where_covered":
                r["overrides"].append(rel["state_rule"]); r["interaction"] = f"supersedes {rel['state_rule']} where it covers the unit"
    finalize(rules)
    json.dump({"rules": [to_schema(r) for r in rules]}, open(out_dir / "rules.json", "w"), indent=1, ensure_ascii=False)
    json.dump(rules, open(out_dir / "rules_ir.json", "w"), indent=1, ensure_ascii=False, default=str)
    def expl(x):
        e = x["explanation"]
        if x["result"] == "unknown":
            e = f"[unknown: {unknown_reason(e)}] " + e
        if x["conflict_flag"] and x.get("conflict_notes"):
            e = (e + " | " if e else "") + "CONFLICT: " + "; ".join(x["conflict_notes"])
        return e
    lookups = {"as_of": as_of, "lookups": {aid: [{"team_rule_id": x["team_rule_id"], "result": x["result"],
                                                    "explanation": expl(x), "conflict_flag": x["conflict_flag"]}
                                                   for x in v.values()] for aid, v in res.items()}}
    json.dump(lookups, open(out_dir / "lookups.json", "w"), indent=1, ensure_ascii=False)
    tests = json.load(open(tests_path or ROOT / "dev/change_tests.json")) + (extra_tests or [])
    changes = run_change_tests(addresses, rules, relations, tests)
    json.dump(changes, open(out_dir / "changes.json", "w"), indent=1, ensure_ascii=False)
    json.dump(audit, open(out_dir / "normalize_audit.json", "w"), indent=1, ensure_ascii=False)
    return rules, lookups, changes


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractions", nargs="+", required=True)
    ap.add_argument("--out", default=str(ROOT / "submission"))
    ap.add_argument("--as-of", default=DEFAULT_AS_OF)
    ap.add_argument("--relations")
    ap.add_argument("--review", action="store_true", help="run LLM category review + relation extraction")
    ap.add_argument("--refine", action="store_true", help="run rule refinement (code citation, short key value, headline date)")
    a = ap.parse_args()
    rules, lookups, changes = build(a.extractions, a.out, a.as_of, a.relations, review=a.review, refine_rules=a.refine)
    import collections
    print("rules", len(rules), "addresses", len(lookups["lookups"]))
    print("results", collections.Counter(x["result"] for v in lookups["lookups"].values() for x in v))
    for k, v in changes.items():
        print(k, "affected", len(v["affected_address_ids"]), "conflicts", len(v["conflict_flag_address_ids"]), "|", v["notes"][:120])
