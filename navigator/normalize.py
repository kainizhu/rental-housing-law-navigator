"""Conservative normalization: extraction cells -> schema rule records + evaluator IR.

Steps per extracted rule: level guard -> exact grounding -> citation normalization ->
value/fact normalization -> date resolution + status -> merge within cell by citation root.
Nothing is merged across categories. Every drop/merge is recorded in `audit`."""
from __future__ import annotations
import json, re, pathlib
from navigator.ground import load_corpus, locate, normalize_citation, citation_key, level_guard, single_line_variant
from navigator.dates import resolve_effective, status_as_of

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_AS_OF = "2026-10-01"
CAT_CODE = {"rent_increase_limits": "RENT", "just_cause_eviction": "JC", "security_deposits": "DEP",
            "application_screening_fees": "FEE", "screening_restrictions": "SCR", "algorithmic_rent_setting": "ALG"}


def jur_code(j: str) -> str:
    if len(j) == 2:
        return j
    city = j.split(",")[0]
    return "".join(w[0] for w in city.split()).upper() if " " in city else city[:3].upper()


def citation_root(norm: str) -> str:
    """Root used to merge sections of the same law inside one cell.
    'BMC 13.78.010' -> 'BMC 13.78'; 'Cal. Civ. Code § 1947.12' stays; bills/session laws stay."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", norm or "")
    if m:
        return norm[:m.start()] + f"{m[1]}.{m[2]}"
    return norm or ""


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _norm_condition(c: dict) -> list:
    c = dict(c)
    f, op, v = c.get("fact"), c.get("op"), c.get("value")
    if f == "year_built" and isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
        c["fact"] = "certificate_of_occupancy_date"
        return [c]
    if f in ("units", "year_built", "building_age_years") and isinstance(v, str):
        nums = [float(x) for x in _NUM.findall(v)]
        if op == "in" and nums:
            g = c.get("group", f"in_{id(c)}")
            return [{**c, "op": "ge", "value": min(nums), "group": g}, {**c, "op": "le", "value": max(nums), "group": g}]
        if nums:
            c["value"] = nums[0]
    return [c]


def _norm_conditions(conds, exemptions=False):
    out = []
    for c in conds or []:
        out.extend(_norm_condition(c))
    if exemptions:
        # 1) ungrouped conditions copied from the same sentence belong together
        by_quote = {}
        for c in out:
            if c.get("group") is None and c.get("quote"):
                by_quote.setdefault(c["quote"].strip(), []).append(c)
        for i, grp in enumerate(v for v in by_quote.values() if len(v) > 1):
            for c in grp:
                c["group"] = f"q{i}"
        # 2) small-building owner-occupancy pattern: an ungrouped small unit-count exemption is only
        #    meaningful together with owner occupancy -> group it with the owner-occupied condition
        small = [c for c in out if c.get("group") is None and c.get("fact") == "units"
                 and c.get("op") in ("eq", "le", "lt") and isinstance(c.get("value"), (int, float)) and c["value"] <= 4]
        occ = [c for c in out if c.get("group") is None and c.get("fact") == "owner_occupied"]
        if small and occ:
            for c in small + occ[:1]:
                c["group"] = "owner_small"
    return out


def _summary(conds):
    parts = [c.get("text") or f"{c.get('fact')} {c.get('op')} {c.get('value')}" for c in conds or []]
    return "; ".join(p for p in parts if p) or None


def normalize(cells: list, corpus=None, as_of: str = DEFAULT_AS_OF):
    corpus = corpus or load_corpus()
    audit, rules = [], []
    for cell in cells:
        j, cat, level = cell["jurisdiction"], cell["category"], cell.get("level") or ("state" if len(cell["jurisdiction"]) == 2 else "city")
        state = j if level == "state" else j.split(", ")[-1]
        recs = []
        for r in cell.get("rules", []):
            raw = r.get("raw_citation", "")
            g = level_guard(level, raw)
            if g or r.get("enacted_by_this_jurisdiction") is False:
                audit.append({"action": "drop_wrong_level", "cell": [j, cat], "citation": raw, "why": g or "not enacted by this jurisdiction"})
                continue
            doc = corpus.get(r.get("source_doc_id"))
            loc = locate(r.get("rule_quote", ""), doc.text, doc.body_start) if doc else None
            norm, kind = normalize_citation(raw)
            eff = resolve_effective(r.get("effective") or {}, level=level, state=state, legal_stage=r.get("legal_stage", "enacted"))
            sunset = (r.get("effective") or {}).get("sunset_date")
            st, certainty = status_as_of(r.get("legal_stage", "enacted"), eff, sunset, as_of)
            recs.append({
                "jurisdiction": j, "level": level, "category": cat,
                "status": {"expired": "failed"}.get(st, st), "status_certainty": certainty,
                "legal_stage": r.get("legal_stage"),
                "title": r.get("title"), "requirement": r.get("requirement"), "key_value": r.get("key_value"),
                "coverage_conditions_ir": _norm_conditions(r.get("coverage_conditions")),
                "exemptions_ir": _norm_conditions(r.get("exemptions"), exemptions=True),
                "coverage_conditions": _summary(r.get("coverage_conditions")),
                "exemptions": _summary(r.get("exemptions")),
                "overrides": [], "interaction": r.get("interaction_text"),
                "effective_date": eff["date"], "effective_date_provenance": eff["provenance"],
                "effective_date_note": eff.get("note"), "sunset_date": sunset,
                "citation": norm, "raw_citation": raw, "citation_kind": kind,
                "source_doc_id": r.get("source_doc_id"),
                "source_url": doc.url if doc else None, "retrieved_at": doc.retrieved_at if doc else None,
                "source_origin": doc.origin if doc else None,
                "quoted_span": loc.span if loc and loc.found else None,
                "quoted_span_single_line": (single_line_variant(loc.span) if loc and loc.found else None),
                "quote_match": loc.mode if loc else "no_doc",
                "penalty": r.get("penalty"), "confidence": r.get("confidence"),
                "other_dates": (r.get("effective") or {}).get("other_dates") or [],
                "conflict_flag": False, "conflict_note": None,
                "_extraction": {"llm_key": cell.get("llm_key"), "model": cell.get("model"), "prompt_version": cell.get("prompt_version")},
            })
        # merge within cell by citation root (never across categories)
        merged = {}
        for x in sorted(recs, key=lambda x: (x["quoted_span"] is None, -(x["confidence"] or 0))):
            k = citation_key(citation_root(x["citation"])) or citation_key(x["title"])
            if k in merged:
                p = merged[k]
                p.setdefault("merged_from", []).append({"citation": x["citation"], "quoted_span": x["quoted_span"]})
                p["requirement"] = (p["requirement"] or "") + (" " + x["requirement"] if x["requirement"] else "")
                audit.append({"action": "merge_same_root", "cell": [j, cat], "into": p["citation"], "from": x["citation"]})
            else:
                merged[k] = x
        rules.extend(merged.values())
    # date conflicts across sources -> conflict flag (surfaced, not resolved)
    for x in rules:
        others = [o for o in x["other_dates"] if o.get("date") and o.get("date") != x["effective_date"]]
        if others:
            x["conflict_flag"] = True
            x["conflict_note"] = "Other published dates: " + "; ".join(f"{o['date']} ({o.get('meaning', '')}, {o.get('doc_id', '')})" for o in others)
    # stable ids
    counters = {}
    for x in sorted(rules, key=lambda x: (x["jurisdiction"], x["category"], x["legal_stage"] != "enacted", x["citation"] or "")):
        base = f"{jur_code(x['jurisdiction'])}-{CAT_CODE[x['category']]}"
        counters[base] = counters.get(base, 0) + 1
        x["team_rule_id"] = f"{base}-{'P' if x['legal_stage'] == 'pending' else ''}{counters[base]:02d}"
    return rules, audit


SCHEMA_FIELDS = ["team_rule_id", "jurisdiction", "level", "category", "status", "title", "requirement", "key_value",
                 "coverage_conditions", "exemptions", "overrides", "interaction", "effective_date", "citation",
                 "source_doc_id", "source_url", "quoted_span", "confidence", "conflict_flag", "conflict_note"]


def to_schema(rule: dict) -> dict:
    out = {k: rule.get(k) for k in SCHEMA_FIELDS}
    out["overrides"] = out["overrides"] or []
    out["confidence"] = None if out["confidence"] is None else max(0.0, min(1.0, float(out["confidence"])))
    out["conflict_flag"] = bool(out["conflict_flag"])
    out["citation"] = out["citation"] or rule.get("raw_citation") or ""
    import re as _re
    raw = rule.get("raw_citation") or ""
    aliases = [out["citation"], raw] + _re.findall(r"P\.\s?L\.\s?\d{4},?\s*c(?:hapter|\.)\s*\d+|\b(?:AB|SB|S|H)\.?\s?\d{2,5}\b", raw + " " + (rule.get("title") or ""))
    out["citation_aliases"] = list(dict.fromkeys(a.strip() for a in aliases if a and a.strip()))
    # extras (allowed: schema does not forbid additional properties)
    for k in ("raw_citation", "effective_date_provenance", "effective_date_note", "sunset_date", "retrieved_at",
              "source_origin", "quote_match", "status_certainty", "legal_stage", "penalty"):
        out[k] = rule.get(k)
    return out


if __name__ == "__main__":
    import sys
    cells = [json.loads(l) for l in open(sys.argv[1])]
    rules, audit = normalize(cells)
    for r in rules:
        print(f"{r['team_rule_id']:12s} {r['status']:18s} {r['citation']!s:45.45s} eff={r['effective_date']}({r['effective_date_provenance']}) quote={r['quote_match']}")
    for a in audit:
        print("AUDIT", a)
