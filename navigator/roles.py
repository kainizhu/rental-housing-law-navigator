"""Condition-role pass (runs on rule records, no documents; majority vote).

Extraction often files every condition it sees under coverage. For address-level applicability only two
roles matter: SCOPE (a building outside it is not covered at all) and EXEMPTION (a covered building is
excluded). Other conditions must not gate coverage:
  expansion      "also includes ..."            (widens scope; never restricts)
  variant        "the higher cap applies only if ..." (selects an amount / branch of the rule)
  not_condition  procedure, timing of notices, remedies
The pass can also re-map a descriptive condition to a listed fact (e.g. 'income-restricted housing' ->
subsidized_or_affordable is_true), and flag records that are only an exemption from ANOTHER law."""
from __future__ import annotations
import json, re
from collections import Counter
from navigator.llm import call_tool
from navigator.extract import FACTS

ROLES_VERSION = "o1"
TOOL = {"name": "classify_conditions", "description": "Role of each condition of each rule record.",
        "input_schema": {"type": "object", "properties": {"rules": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"},
            "only_exempts_another_law": {"type": "boolean", "description": "True if the record's whole content is an exemption from, or a limit on, a DIFFERENT law (e.g. 'new buildings are exempt from local rent control'), not an operative requirement of its own."},
            "conditions": {"type": "array", "items": {"type": "object", "properties": {
                "n": {"type": "integer"},
                "role": {"type": "string", "enum": ["scope", "expansion", "variant", "exemption", "not_condition"]},
                "fact": {"type": ["string", "null"], "enum": FACTS + [None], "description": "Corrected fact if the listed one is wrong or too vague; else null."},
                "op": {"type": ["string", "null"], "enum": ["lt", "le", "gt", "ge", "eq", "ne", "in", "is_true", "is_false", "describes", None]},
                "value": {"type": ["string", "number", "null"]}},
                "required": ["n", "role"]}}},
            "required": ["id", "only_exempts_another_law", "conditions"]}}}, "required": ["rules"]}}

SYSTEM = ("You classify the conditions attached to housing-law rule records so a program can decide, for a given BUILDING, "
          "whether the rule applies. Roles:\n"
          "- scope: the rule does not apply at all to buildings outside this condition (e.g. 'units with certificate of occupancy before "
          "1979-06-13', 'buildings with 5+ units', 'income-restricted housing funded by the city').\n"
          "- expansion: text says the rule ALSO covers something ('includes newly constructed units...'); it never narrows coverage.\n"
          "- variant: decides WHICH amount/branch applies (e.g. 'the two-month cap applies only to small landlords owning <= 2 properties'); "
          "the rule itself still applies to other buildings.\n"
          "- exemption: excludes otherwise-covered buildings or tenancies.\n"
          "- not_condition: procedure, notice timing, remedies, or anything that is not about which buildings are covered.\n"
          "If a condition is written as fact 'other'/'building_type' with op 'describes' but clearly concerns a listed fact "
          "(subsidized_or_affordable, owner_occupied, units, year_built, certificate_of_occupancy_date, building_age_years), give the "
          f"corrected fact/op/value. Listed facts: {', '.join(FACTS)}. When unsure, keep role scope for coverage conditions and exemption "
          "for exemptions.")


def _cards(rules):
    out = []
    for r in rules:
        conds = []
        for kind in ("coverage_conditions_ir", "exemptions_ir"):
            for c in r.get(kind) or []:
                conds.append({"n": len(conds), "listed_as": "coverage" if kind.startswith("coverage") else "exemption",
                              "fact": c.get("fact"), "op": c.get("op"), "value": c.get("value"), "text": c.get("text")})
        out.append({"id": r["team_rule_id"], "jurisdiction": r["jurisdiction"], "category": r["category"], "title": r["title"],
                    "requirement": (r.get("requirement") or "")[:500], "key_value": r.get("key_value"), "conditions": conds})
    return out


def classify(rules, n_votes=3, only=None):
    """Returns {rule_id: {"only_exempts_another_law": bool, "conditions": {n: {role, fact, op, value}}}} by majority vote."""
    by = {}
    for r in rules:
        if only and r["jurisdiction"] not in only:
            continue
        by.setdefault(r["jurisdiction"], []).append(r)
    votes = {}
    for j, rs in sorted(by.items()):
        cards = json.dumps(_cards(rs), ensure_ascii=False)
        for v in range(n_votes):
            out = call_tool(SYSTEM, f"JURISDICTION: {j}\nRULES:\n{cards}", f"Classify every condition of every rule. Version {ROLES_VERSION}-v{v}.",
                            TOOL, tag=f"roles|{j}")
            for item in (out.get("input") or {}).get("rules", []):
                vv = votes.setdefault(item["id"], {"flag": [], "conds": {}})
                vv["flag"].append(bool(item.get("only_exempts_another_law")))
                for c in item.get("conditions", []):
                    vv["conds"].setdefault(c["n"], []).append(c)
    final = {}
    for rid, vv in votes.items():
        flag = sum(vv["flag"]) * 2 > n_votes
        conds = {}
        for n, cs in vv["conds"].items():
            role, k = Counter(c["role"] for c in cs).most_common(1)[0]
            if k * 2 <= n_votes:
                continue  # no majority: leave the condition as extracted
            agree = [c for c in cs if c["role"] == role]
            fix = Counter((c.get("fact"), c.get("op"), json.dumps(c.get("value"))) for c in agree if c.get("fact")).most_common(1)
            entry = {"role": role}
            if fix and fix[0][1] * 2 > n_votes:
                f, o, v = fix[0][0]
                entry.update({"fact": f, "op": o, "value": json.loads(v)})
            conds[n] = entry
        final[rid] = {"only_exempts_another_law": flag, "conditions": conds}
    return final


_LIMITS_LOCAL = re.compile(
    r"\b(cit(?:y|ies)|towns?|municipal\w*|local\w*|counties|county)\b[^.]{0,120}\b(prohibit\w*|bar(?:s|red)?|preempt\w*|may not|shall not|cannot)\b"
    r"|\b(prohibit\w*|bar(?:s|red)?|preempt\w*)\b[^.]{0,80}\b(cit(?:y|ies)|towns?|municipal\w*|local\w*)\b", re.I)


def limits_local_law(r) -> bool:
    """Deterministic guard: a STATE law that bars or preempts local ordinances is an operative rule of its own
    (it drives local-vs-state conflicts), never 'only an exemption from another law'."""
    if r.get("level") != "state":
        return False
    text = " ".join(str(r.get(k) or "") for k in ("title", "requirement"))
    return bool(_LIMITS_LOCAL.search(text))


def apply_roles(rules, roles):
    """Attach roles (and fact fixes) to IR conditions; drop records that are only exemptions from another law."""
    kept, dropped = [], []
    for r in rules:
        info = roles.get(r["team_rule_id"])
        if not info:
            kept.append(r); continue
        if info["only_exempts_another_law"] and not limits_local_law(r):
            dropped.append({"id": r["team_rule_id"], "citation": r["citation"], "decision": "drop_only_exempts_another_law",
                            "reason": "record only exempts buildings from a different law (3-vote majority)"})
            continue
        n = 0
        for kind in ("coverage_conditions_ir", "exemptions_ir"):
            for c in r.get(kind) or []:
                e = info["conditions"].get(n) or info["conditions"].get(str(n))
                n += 1
                if not e:
                    continue
                c["role"] = e["role"]
                if e.get("fact") and e["fact"] != c.get("fact") and c.get("fact") in ("other", "building_type", None):
                    c["original"] = {k: c.get(k) for k in ("fact", "op", "value")}
                    c["fact"], c["op"], c["value"] = e["fact"], e.get("op") or c.get("op"), e.get("value")
        kept.append(r)
    return kept, dropped
