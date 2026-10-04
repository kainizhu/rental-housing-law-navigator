"""Deterministic three-valued evaluator: (address facts, normalized rules, relations, as_of) -> results.

Truth values: True / False / None(unknown).  Kleene AND/OR.
Policy (all switchable, reported in outputs):
  hints_decide:        "calibrated" | "none" | "all"   which inferred_hint facts may decide a predicate
  unobservable_in_exemption: "assume_not_exempt"         unobservable exemption facts -> False + note
  unobservable_in_coverage:  "unknown"                   unobservable coverage facts -> None
"""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field
from navigator.dates import status_as_of

UNOBSERVABLE = {"owner_type", "owner_occupied", "properties_owned_by_landlord", "subsidized_or_affordable"}
# Facts about a particular tenancy, not the building. Lookups answer "which rules apply to this
# address", so tenancy-level conditions become notes and never make a building-level result unknown.
TENANCY_LEVEL = {"tenancy_start_date"}
IGNORED = {"building_type", "other"}


@dataclass
class Policy:
    hints_decide: str = "calibrated"
    unknown_credit_note: str = "unknown means a coverage-relevant fact is missing from the data"


def k_and(vals):
    vals = list(vals)
    if any(v is False for v in vals):
        return False
    if any(v is None for v in vals):
        return None
    return True


def k_or(vals):
    vals = list(vals)
    if any(v is True for v in vals):
        return True
    if any(v is None for v in vals):
        return None
    return False


def _cmp_interval(lo, hi, op, x):
    """Compare a value known to lie in [lo, hi] (None = unbounded) with x."""
    INF = float("inf")
    lo_ = -INF if lo is None else lo
    hi_ = INF if hi is None else hi
    if op == "lt":
        return True if hi_ < x else False if lo_ >= x else None
    if op == "le":
        return True if hi_ <= x else False if lo_ > x else None
    if op == "gt":
        return True if lo_ > x else False if hi_ <= x else None
    if op == "ge":
        return True if lo_ >= x else False if hi_ < x else None
    if op == "eq":
        return True if lo_ == hi_ == x else False if (x < lo_ or x > hi_) else None
    if op == "ne":
        r = _cmp_interval(lo, hi, "eq", x)
        return None if r is None else not r
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _usable(fact, policy: Policy):
    if fact is None or not fact.known:
        return False
    if fact.provenance == "observed":
        return fact.support != "contradicted"
    if fact.provenance == "inferred_hint":
        return policy.hints_decide == "all" or (policy.hints_decide == "calibrated" and fact.support == "calibrated")
    return False


def eval_condition(cond, facts, as_of: dt.date, policy: Policy, notes: list, in_exemption: bool):
    f, op, val = cond.get("fact"), cond.get("op"), cond.get("value")
    if (not in_exemption and f == "subsidized_or_affordable" and op == "describes" and cond.get("role") == "scope"):
        # a classified SCOPE condition limiting the rule to subsidized/affordable housing ('housing providers receiving
        # city funding') restricts coverage to a population the data cannot identify -> unknown, never applies.
        # (Generic owner descriptions such as 'owners of one or more rental units' stay non-decisive.)
        notes.append(f"coverage limited to '{cond.get('text', f)}' ({f}), which is not in the data")
        return None
    if f in IGNORED or op == "describes":
        # descriptive conditions ("residential rental units") are not decidable from address data and
        # every sample address is a multifamily rental: non-decisive in both directions
        return True if not in_exemption else False
    if f in TENANCY_LEVEL:
        notes.append(f"applies subject to tenancy-specific condition: {cond.get('text', f)}")
        return False if in_exemption else True
    if f in UNOBSERVABLE:
        if in_exemption:
            notes.append(f"assumed not exempt: '{cond.get('text', f)}' depends on {f}, which is not in the data")
            return False
        if op in ("is_false", "ne"):
            # a coverage condition that only excludes a special case (e.g. "not owner-occupied") is an
            # exemption in disguise: same policy as exemptions -> assume the typical (non-exempt) case
            notes.append(f"assumed covered: '{cond.get('text', f)}' depends on {f}, which is not in the data")
            return True
        notes.append(f"coverage depends on {f}, which is not in the data")
        return None
    if f == "units":
        fact = facts.get("units")
        x = _num(val)
        if x is None:
            return None
        if not _usable(fact, policy):
            if fact is not None and fact.known and fact.note:
                notes.append(f"units: {fact.note} ({fact.provenance}, {fact.support}) - not used as a fact")
            else:
                notes.append("unit count missing")
            return None
        return _cmp_interval(fact.lo, fact.hi, op, x)
    if f == "year_built":
        fact = facts.get("year_built")
        x = _num(val)
        if x is None or not _usable(fact, policy):
            notes.append("year built missing")
            return None
        return _cmp_interval(fact.lo, fact.hi, op, x)
    if f == "certificate_of_occupancy_date":
        fact = facts.get("year_built")
        if not _usable(fact, policy):
            notes.append("year built missing (certificate-of-occupancy test)")
            return None
        try:
            cut = dt.date.fromisoformat(str(val)[:10])
        except ValueError:
            return None
        y = fact.lo
        # year built approximates CO year; a building in the cutoff year is undecidable
        frac_cut = cut.year + (cut.timetuple().tm_yday - 1) / 366.0
        r = _cmp_interval(y, y + 0.9999, op, frac_cut)
        if r is None:
            notes.append(f"built {int(y)}, same year as the {cut.isoformat()} certificate-of-occupancy cutoff")
        return r
    if f == "building_age_years":
        fact = facts.get("year_built")
        x = _num(val)
        if x is None or not _usable(fact, policy):
            notes.append("year built missing (building-age test)")
            return None
        lo_age, hi_age = as_of.year - fact.hi - 1, as_of.year - fact.lo
        r = _cmp_interval(lo_age, hi_age, op, x)
        if r is None:
            notes.append(f"building age straddles {x:g} years as of {as_of.isoformat()}")
        return r
    return None


def _groups(conds):
    g = {}
    for i, c in enumerate(conds or []):
        g.setdefault(c.get("group", f"_{i}"), []).append(c)
    return list(g.values())


def coverage(rule, facts, as_of, policy):
    notes = []
    # roles (navigator/roles.py): only 'scope' coverage conditions and 'exemption' exemptions decide coverage
    for c in (rule.get("coverage_conditions") or []) + (rule.get("exemptions") or []):
        if c.get("role") in ("expansion", "variant", "not_condition"):
            notes.append(f"not a coverage test ({c['role']}): {c.get('text') or c.get('fact')}")
    covs = [c for c in rule.get("coverage_conditions") or [] if c.get("role") in (None, "scope")]
    exms = [c for c in rule.get("exemptions") or [] if c.get("role") in (None, "exemption")]
    cov = k_and(eval_condition(c, facts, as_of, policy, notes, False) for c in covs)
    ex = k_or(k_and(eval_condition(c, facts, as_of, policy, notes, True) for c in grp)
              for grp in _groups(exms))
    return k_and([cov, None if ex is None else (not ex)]), notes


def evaluate(addresses: dict, rules: list, relations: list, as_of: str, policy: Policy = None, force_status: dict = None):
    """addresses: facts.build_fact_table()[0]; rules: normalized records (see normalize.py).
    Returns {address_id: [ {team_rule_id, result, explanation, conflict_flag, notes} ]}."""
    policy = policy or Policy()
    a = dt.date.fromisoformat(as_of)
    out = {}
    for aid, ad in addresses.items():
        res = {}
        for r in rules:
            if r["jurisdiction"] not in ad["stack"]:
                continue
            if force_status and r["team_rule_id"] in force_status:
                st = force_status[r["team_rule_id"]]
            elif "legal_stage" in r:
                st = status_as_of(r.get("legal_stage") or "enacted", {"date": r.get("effective_date")}, r.get("sunset_date"), as_of)[0]
            else:
                st = r["status"]
            if st in ("failed", "expired"):
                continue
            cov, notes = coverage(r, ad["facts"], a, policy)
            if cov is False:
                continue
            if st == "pending":
                result = "pending"
            elif st == "not_yet_effective":
                result = "not_yet_effective"
            else:
                result = "applies" if cov else "unknown"
                if result == "applies" and r.get("effective_relative_unresolved"):
                    result = "unknown"
                    notes.append("effective date is relative to an event whose date is not stated; may not be in force yet")
            res[r["team_rule_id"]] = {"team_rule_id": r["team_rule_id"], "result": result, "notes": notes,
                                      "conflict_flag": False, "conflict_notes": []}
        # relations
        for rel in relations:
            s, l, kind = rel["state_rule"], rel["local_rule"], rel["type"]
            if kind == "local_governs_where_covered" and s in res and res[s]["result"] in ("applies", "unknown"):
                lr = res.get(l, {}).get("result")
                if lr == "applies":
                    res[s]["result"] = "superseded"
                    res[s]["notes"].append(f"local rule {l} covers this building and governs")
                elif lr == "unknown":
                    res[s]["result"] = "unknown"
                    res[s]["notes"].append(f"whether local rule {l} (which would govern) covers this building is unknown")
            elif kind in ("possible_conflict", "preempts_local") and s in res and l in res:
                for x in (s, l):
                    res[x]["conflict_flag"] = True
                    res[x]["conflict_notes"].append(rel.get("note") or f"{kind} between {s} and {l}")
        for v in res.values():
            v["explanation"] = "; ".join(v["notes"]) if v["notes"] else ""
        out[aid] = list(res.values())
    return out
