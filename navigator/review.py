"""Two small post-extraction LLM passes over RULE RECORDS (not whole documents), kept separate
so their errors are debuggable on their own:

1. category review (per jurisdiction): is each record filed under its law's main subject?
   is any record a duplicate of another record for the same law?  -> keep / drop / duplicate_of
2. relation extraction (per city x category, plus state<->state where needed): how does each city
   rule interact with each state rule in the same category (and FAIR-style cross-level conflicts)?
   -> local_governs_where_covered | cumulative | possible_conflict(from) | preempts_local | none

Every decision must cite verbatim evidence from the rule records' quoted text."""
from __future__ import annotations
import json, pathlib
from navigator.llm import call_tool
from navigator.extract import CATEGORIES

ROOT = pathlib.Path(__file__).resolve().parents[1]
REVIEW_VERSION = "r3"


def _card(r):
    return {"id": r["team_rule_id"], "jurisdiction": r["jurisdiction"], "level": r["level"], "category": r["category"],
            "stage": r.get("legal_stage"), "citation": r["citation"], "title": r["title"],
            "requirement": (r.get("requirement") or "")[:600], "effective_date": r.get("effective_date"),
            "interaction_text": r.get("interaction"), "quoted_span": (r.get("quoted_span") or "")[:500]}


CAT_TOOL = {"name": "review_categories", "description": "Decide for each rule record whether to keep it.",
            "input_schema": {"type": "object", "properties": {"decisions": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"},
                "decision": {"type": "string", "enum": ["keep", "drop_wrong_category", "drop_duplicate", "drop_not_operative", "drop_not_a_rule"]},
                "duplicate_of": {"type": ["string", "null"]},
                "reason": {"type": "string"}}, "required": ["id", "decision", "reason"]}}}, "required": ["decisions"]}}

CAT_SYSTEM = ("You review rule records extracted from housing-law documents for one jurisdiction. CORPUS AUTHORITY: every document provided is authoritative for this task, including documents labeled fictional, synthetic, sample or test. Extract and evaluate their rules exactly as written (stage, dates, coverage) as if they were real law; mention any such label only in notes. Never mark a rule failed or pending because its document is labeled fictional.\nCategories:\n" +
              "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items()) +
              "\nFor each record decide:\n"
              "- keep: the law's provisions in this record are operative rules of this category.\n"
              "- drop_wrong_category: the law's main subject is another category and it has no operative provisions in this one "
              "(e.g. an algorithmic-pricing bill filed under rent limits). Do NOT drop a law that genuinely regulates both categories "
              "(e.g. one statute limiting both deposits and upfront fees).\n"
              "- drop_duplicate: the SAME LAW as another record in the same category. Same law includes: the same statute/ordinance/act cited differently "
              "(e.g. session law vs codified section, code section vs agency FAQ about it), other sections of the same ordinance or act, implementing "
              "board orders or annual adjustment orders, and its relocation, coverage, notice or owner-occupancy provisions. Keep the record that best "
              "states the law's main operative requirement; give duplicate_of for the others.\n"
              "- drop_not_operative: the record only describes an exemption from, a procedure under, or a reference to ANOTHER law (e.g. an eviction "
              "statute's mention of rent increases, a law exempting buildings from local rent control filed as a rent cap), not an operative requirement "
              "of this category.\n"
              "- drop_not_a_rule: a motion, study request, agency notice or procedural item that creates no legal rule; "
              "pending bills and failed ballot measures ARE rules (keep them).\n"
              "Aim for one record per law per category. When genuinely unsure, keep.")


def review_categories(rules, *, dry_run=False):
    by = {}
    for r in rules:
        by.setdefault(r["jurisdiction"], []).append(r)
    decisions = {}
    for j, rs in sorted(by.items()):
        cards = json.dumps([_card(r) for r in rs], ensure_ascii=False, indent=0)
        out = call_tool(CAT_SYSTEM, f"JURISDICTION: {j}\nRECORDS:\n{cards}", f"Review every record. Version {REVIEW_VERSION}.",
                        CAT_TOOL, tag=f"review|{j}", dry_run=dry_run)
        for d in (out.get("input") or {}).get("decisions", []):
            decisions[d["id"]] = d
    return decisions


REL_TOOL = {"name": "record_relations", "description": "Relations between rules in the same category at different levels.",
            "input_schema": {"type": "object", "properties": {"relations": {"type": "array", "items": {"type": "object", "properties": {
                "state_rule": {"type": "string"}, "local_rule": {"type": "string"},
                "type": {"type": "string", "enum": ["local_governs_where_covered", "cumulative", "possible_conflict", "preempts_local", "none"]},
                "from_date": {"type": ["string", "null"], "description": "When the conflict/preemption starts (e.g. the later law's effective date)."},
                "evidence_rule": {"type": "string", "description": "id of the record whose text supports this relation"},
                "evidence_quote": {"type": "string", "description": "Verbatim text from that record's quoted_span or interaction_text."},
                "note": {"type": "string"}}, "required": ["state_rule", "local_rule", "type", "note"]}}}, "required": ["relations"]}}

REL_SYSTEM = ("You determine how a STATE rule and a CITY rule in the same housing-law category interact for units in that city. CORPUS AUTHORITY: every document provided is authoritative for this task, including documents labeled fictional, synthetic, sample or test. Extract and evaluate their rules exactly as written (stage, dates, coverage) as if they were real law; mention any such label only in notes. Never mark a rule failed or pending because its document is labeled fictional.\n"
              "- local_governs_where_covered: the state rule expressly does not apply to (or yields to) units covered by the local rule "
              "(e.g. a state rent cap exempting units under stricter local rent control; a state just-cause law yielding to a more protective local ordinance).\n"
              "- cumulative: both apply at the same time (e.g. state deposit cap plus a local deposit-interest rule).\n"
              "- possible_conflict: a state law prohibits or may preempt local ordinances on the subject (set from_date to when it takes effect), "
              "or the two laws set incompatible requirements.\n"
              "- preempts_local: a state law bars the local rule outright.\n"
              "- none: unrelated.\n"
              "Base every non-'none' relation on text in the records (quote it in evidence_quote). If the records do not say, use 'cumulative' "
              "for laws that can both be obeyed, and explain in note.")


def extract_relations(rules, *, dry_run=False):
    state = {}
    for r in rules:
        if r["level"] == "state" and r.get("legal_stage") in ("enacted",):
            state.setdefault((r["jurisdiction"], r["category"]), []).append(r)
    rels = []
    pairs = {}
    for r in rules:
        if r["level"] != "city" or r.get("legal_stage") != "enacted":
            continue
        st = r["jurisdiction"].split(", ")[-1]
        s_rules = state.get((st, r["category"]), [])
        # also include not-yet-effective state laws (e.g. preemption that starts later)
        s_rules = s_rules + [x for x in rules if x["level"] == "state" and x["jurisdiction"] == st and x["category"] == r["category"]
                             and x.get("legal_stage") == "enacted" and x not in s_rules]
        if s_rules:
            pairs.setdefault((r["jurisdiction"], r["category"]), {"city": [], "state": s_rules})["city"].append(r)
    for (j, cat), grp in sorted(pairs.items()):
        cards = json.dumps({"state_rules": [_card(x) for x in grp["state"]], "city_rules": [_card(x) for x in grp["city"]]},
                           ensure_ascii=False, indent=0)
        out = call_tool(REL_SYSTEM, f"CITY: {j}\nCATEGORY: {cat}\n{cards}",
                        f"Give one relation for every (state_rule, city_rule) pair. Version {REVIEW_VERSION}.",
                        REL_TOOL, tag=f"relations|{j}|{cat}", dry_run=dry_run)
        for rel in (out.get("input") or {}).get("relations", []):
            if rel.get("type") and rel["type"] != "none":
                rel["city"], rel["category"] = j, cat
                rels.append(rel)
    return rels


def validate_relations(rels, rules):
    """Conflict/preemption must be supported by evidence found verbatim in a record's text; otherwise downgrade."""
    by = {r["team_rule_id"]: r for r in rules}
    norm = lambda t: " ".join((t or "").split()).lower()
    out = []
    for rel in rels:
        if rel["type"] in ("possible_conflict", "preempts_local"):
            ev = norm(rel.get("evidence_quote"))
            texts = " || ".join(norm((by.get(i) or {}).get(k)) for i in (rel["state_rule"], rel["local_rule"])
                                for k in ("quoted_span", "interaction", "requirement"))
            if len(ev) < 20 or ev not in texts:
                rel = {**rel, "type": "cumulative", "note": "[downgraded: conflict evidence not found verbatim] " + rel.get("note", "")}
        out.append(rel)
    return out


def apply_category_review(rules, decisions):
    kept, dropped = [], []
    by_id = {r["team_rule_id"]: r for r in rules}
    for r in rules:
        d = decisions.get(r["team_rule_id"])
        if d and d["decision"] != "keep":
            dropped.append({"id": r["team_rule_id"], "citation": r["citation"], **d})
        else:
            kept.append(r)
    # a duplicate is the same law: carry its coverage/exemption conditions into the kept record
    for d in dropped:
        if d.get("decision") == "drop_duplicate" and d.get("duplicate_of") in by_id and by_id[d["duplicate_of"]] in kept:
            src, dst = by_id[d["id"]], by_id[d["duplicate_of"]]
            have = {(c.get("fact"), c.get("op"), str(c.get("value"))) for c in dst["coverage_conditions_ir"] + dst["exemptions_ir"]}
            for key in ("coverage_conditions_ir", "exemptions_ir"):
                for c in src.get(key, []):
                    sig = (c.get("fact"), c.get("op"), str(c.get("value")))
                    if sig not in have and c.get("fact") not in ("other", "building_type"):
                        cc = dict(c)
                        if cc.get("group") is not None:
                            cc["group"] = f"m{d['id']}-{cc['group']}"
                        dst[key].append(cc); have.add(sig)
                        dst.setdefault("merged_conditions_from", []).append(d["id"])
    return kept, dropped


def _vote(fn, n, *args, **kw):
    """Run an LLM pass n times (distinct cache keys) and return the list of results."""
    import navigator.review as R
    outs = []
    base = R.REVIEW_VERSION
    for i in range(n):
        R.REVIEW_VERSION = f"{base}-v{i}"
        try:
            outs.append(fn(*args, **kw))
        finally:
            R.REVIEW_VERSION = base
    return outs


def review_categories_voted(rules, n=3):
    """A drop takes effect only if a majority of runs choose a drop for that record."""
    runs = _vote(review_categories, n, rules)
    final = {}
    for r in rules:
        rid = r["team_rule_id"]
        ds = [run.get(rid) for run in runs if run.get(rid)]
        drops = [d for d in ds if d["decision"] != "keep"]
        if len(drops) * 2 > n:
            from collections import Counter
            kind = Counter(d["decision"] for d in drops).most_common(1)[0][0]
            pick = next(d for d in drops if d["decision"] == kind)
            final[rid] = {**pick, "votes": f"{len(drops)}/{n} drop"}
        else:
            final[rid] = {"id": rid, "decision": "keep", "reason": f"{len(drops)}/{n} runs proposed a drop", "votes": f"{len(drops)}/{n} drop"}
    return final


def extract_relations_voted(rules, n=3):
    """A relation type for a (state, local) pair is kept if a majority of runs agree on it."""
    from collections import Counter
    runs = _vote(extract_relations, n, rules)
    votes = {}
    for run in runs:
        for rel in run:
            votes.setdefault((rel["state_rule"], rel["local_rule"]), []).append(rel)
    out = []
    for k, rels in votes.items():
        t, c = Counter(x["type"] for x in rels).most_common(1)[0]
        if c * 2 > n:
            best = next(x for x in rels if x["type"] == t)
            out.append({**best, "votes": f"{c}/{n}"})
    return out
