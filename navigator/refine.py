"""Rule refinement pass (one call per jurisdiction, document bundle in context):
  - canonical code citation (accepted only if a verbatim quote containing it is found in the documents)
  - short key_value in the style 'one month's rent', 'lesser of CPI or 4%', '$50 cap'
  - effective date of the HEADLINE requirement (re-resolved by dates.py; accepted only with a grounded quote)
Every change is recorded on the rule as `refined: {...}`."""
from __future__ import annotations
import json, re
from navigator.extract import route_docs, bundle_text, level_of, SYSTEM, _DATE
from navigator.ground import locate, normalize_citation
from navigator.dates import resolve_effective, status_as_of
from navigator.llm import call_tool

REFINE_VERSION = "f1"
TOOL = {"name": "refine_rules", "description": "Refined fields for each rule record.",
        "input_schema": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"},
            "code_citation": {"type": ["string", "null"], "description": "Official code/statute citation with section or chapter "
                              "(e.g. 'S.F. Admin. Code ch. 37', 'L.A. Mun. Code § 151.06', 'N.J.S.A. 46:8-21.2'), or null if no document states one."},
            "citation_quote": {"type": ["string", "null"], "description": "Verbatim text from a document that contains that citation."},
            "citation_doc_id": {"type": ["string", "null"]},
            "key_value_short": {"type": ["string", "null"], "description": "Headline number or formula in <= 8 words, e.g. '1.5 months' rent', "
                                "'5% + CPI, max 10%', '$50 cap', 'lesser of 3% or 80% of CPI'. Null if the rule has no headline number."},
            "headline_effective": _DATE,
            "headline_effective_doc_id": {"type": ["string", "null"]}},
            "required": ["id", "code_citation", "key_value_short", "headline_effective"]}}}, "required": ["items"]}}


def refine(rules, corpus, *, as_of="2026-10-01", only=None):
    by = {}
    for r in rules:
        if only and r["jurisdiction"] not in only:
            continue
        by.setdefault(r["jurisdiction"], []).append(r)
    log = []
    for j, rs in sorted(by.items()):
        docs = route_docs(corpus, j)
        if not docs:
            continue
        cards = [{"id": r["team_rule_id"], "category": r["category"], "title": r["title"], "citation_now": r["citation"],
                  "key_value_now": r.get("key_value"), "effective_now": r.get("effective_date"),
                  "requirement": (r.get("requirement") or "")[:400]} for r in rs]
        bundle = f"TARGET JURISDICTION: {j} (level: {level_of(j)})\n\n" + bundle_text(docs)
        instr = ("REFINE these rule records using only the documents. For each: (1) the official code citation if any document states it "
                 "(copy the sentence that contains it into citation_quote); (2) a short key_value; (3) when the HEADLINE requirement first took "
                 "effect (not a later amendment), using the usual date rules.\nRECORDS:\n" + json.dumps(cards, ensure_ascii=False)
                 + f"\nVersion {REFINE_VERSION}.")
        out = call_tool(SYSTEM, bundle, instr, TOOL, tag=f"refine|{j}")
        idx = {r["team_rule_id"]: r for r in rs}
        for it in (out.get("input") or {}).get("items", []):
            r = idx.get(it.get("id"))
            if not r:
                continue
            ch = {}
            cc, cq, cd = it.get("code_citation"), it.get("citation_quote"), corpus.get(it.get("citation_doc_id") or "")
            if cc and cq and cd and locate(cq, cd.text, cd.body_start).found:
                nums = re.findall(r"\d+(?:[.:\-]\d+)*[A-Za-z]?", cc)
                if nums and any(n in cq for n in nums):
                    n_new, kind = normalize_citation(cc)
                    if n_new and n_new != r["citation"]:
                        ch["citation"] = [r["citation"], n_new]
                        r.setdefault("citation_aliases_extra", []).append(r["citation"])
                        r["citation"], r["citation_kind"] = n_new, kind
            kv = it.get("key_value_short")
            if kv and kv != r.get("key_value"):
                ch["key_value"] = [r.get("key_value"), kv]
                r["key_value_long"] = r.get("key_value")
                r["key_value"] = kv
            he = it.get("headline_effective") or {}
            hd = corpus.get(it.get("headline_effective_doc_id") or "")
            if he.get("kind") and he.get("kind") != "missing" and he.get("quote") and hd and locate(he["quote"], hd.text, hd.body_start).found:
                state = j if len(j) == 2 else j.split(", ")[-1]
                res = resolve_effective(he, level=r["level"], state=state, legal_stage=r.get("legal_stage") or "enacted")
                if res["date"] and res["date"] != r.get("effective_date"):
                    ch["effective_date"] = [r.get("effective_date"), res["date"]]
                    r["effective_date"], r["effective_date_provenance"], r["effective_date_note"] = res["date"], res["provenance"], res.get("note")
                    st, cert = status_as_of(r.get("legal_stage") or "enacted", res, r.get("sunset_date"), as_of)
                    r["status"], r["status_certainty"] = {"expired": "failed"}.get(st, st), cert
            if ch:
                r["refined"] = ch
                log.append({"id": r["team_rule_id"], **ch})
    return rules, log
