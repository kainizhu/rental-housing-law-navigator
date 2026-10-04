"""Cell-routed rule extraction: one structured call per (jurisdiction, category) cell,
returning 0..k rule records with verbatim evidence quotes. No law-specific code here:
everything jurisdiction-specific comes from the manifest and the documents."""
from __future__ import annotations
import argparse, json, pathlib, sys
from navigator.ground import load_corpus
from navigator.llm import call_tool, MODEL

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPT_VERSION = "x5"

CATEGORIES = {  # from the challenge brief, "What to capture"
    "rent_increase_limits": "Rent increase limits: cap formula, covered buildings, exemptions, local vs. state precedence. Also laws that bar or preempt local rent control, and failed/struck rent-control measures.",
    "just_cause_eviction": "Just-cause eviction: allowed causes, notice, relocation assistance, coverage.",
    "security_deposits": "Security deposits: maximum amount, exceptions, effective date.",
    "application_screening_fees": "Application & screening fees: fee caps, allowed upfront charges, receipts and refunds, broker/finder fee rules charged to tenants.",
    "screening_restrictions": "Screening restrictions: limits on criminal-history and income-source screening; timing rules. Includes fair-housing / anti-discrimination laws that make source of income (e.g. Section 8 vouchers, public benefits) or criminal history a protected basis in rental decisions.",
    "algorithmic_rent_setting": "Algorithmic rent-setting: definition of covered software, prohibited conduct, penalties, effective date.",
}

FACTS = ["year_built", "certificate_of_occupancy_date", "units", "building_age_years",
         "owner_type", "owner_occupied", "properties_owned_by_landlord", "building_type",
         "tenancy_start_date", "subsidized_or_affordable", "other"]

_COND = {"type": "object", "properties": {
    "fact": {"type": "string", "enum": FACTS},
    "op": {"type": "string", "enum": ["lt", "le", "gt", "ge", "eq", "ne", "in", "is_true", "is_false", "describes"]},
    "value": {"type": ["string", "number", "null"], "description": "Threshold or value, e.g. 6, '1979-06-13', 15. Null for is_true/is_false."},
    "text": {"type": "string", "description": "Plain-language statement of the condition."},
    "quote": {"type": "string", "description": "Verbatim supporting text copied from the document."},
    "group": {"type": "integer", "description": "Conditions sharing a group number must ALL hold together (AND). Different groups are alternatives (OR) for exemptions. Default: each condition its own group."}},
    "required": ["fact", "op", "text"]}

_DATE = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["explicit", "offset_after_anchor", "first_of_month_after_anchor", "missing"],
             "description": "How the document states when the rule takes effect. 'missing' if the text does not say. Never guess."},
    "explicit_date": {"type": ["string", "null"], "description": "YYYY, YYYY-MM or YYYY-MM-DD exactly as precise as the text."},
    "anchor_event": {"type": ["string", "null"], "description": "e.g. enactment, adoption, approval, final passage"},
    "anchor_date": {"type": ["string", "null"], "description": "Date of the anchor event if stated anywhere in the documents (YYYY-MM-DD)."},
    "anchor_quote": {"type": ["string", "null"]},
    "offset_value": {"type": ["number", "null"]},
    "offset_unit": {"type": ["string", "null"], "enum": ["days", "months", "years", None]},
    "nth_month": {"type": ["number", "null"], "description": "For 'first day of the Nth month next following X'."},
    "quote": {"type": ["string", "null"], "description": "Verbatim text stating the effective date / rule."},
    "sunset_date": {"type": ["string", "null"]},
    "sunset_quote": {"type": ["string", "null"]},
    "other_dates": {"type": "array", "items": {"type": "object", "properties": {
        "date": {"type": "string"}, "meaning": {"type": "string"}, "quote": {"type": "string"}, "doc_id": {"type": "string"}}},
        "description": "Any conflicting or additional effective/operative dates from other documents."}},
    "required": ["kind"]}

TOOL = {"name": "record_rules", "description": "Record the rules found for this jurisdiction and category.",
        "input_schema": {"type": "object", "properties": {
            "cell_finding": {"type": "string", "enum": ["rules_found", "no_rule_at_this_level", "insufficient_text"]},
            "finding_note": {"type": "string", "description": "Why no rule / what is missing, if applicable."},
            "rules": {"type": "array", "items": {"type": "object", "properties": {
                "title": {"type": "string"},
                "requirement": {"type": "string", "description": "One or two plain-language sentences."},
                "key_value": {"type": ["string", "null"], "description": "Headline number or formula, e.g. '1.5 months rent', 'lesser of CPI or 4%'."},
                "raw_citation": {"type": "string", "description": "Official cite as written for THIS provision, e.g. 'Cal. Civ. Code § 1947.12', 'N.J.S.A. 46:8-21.2', 'G.L. c. 186 § 15B', 'S.F. Admin. Code § 37.10C', 'P.L.2026, c.43', 'S.2983'."},
                "enacted_by": {"type": "string", "description": "The government that enacted/proposed this provision."},
                "enacted_by_this_jurisdiction": {"type": "boolean"},
                "legal_stage": {"type": "string", "enum": ["enacted", "pending", "failed", "repealed"],
                                "description": "enacted = law (even if not yet effective); pending = bill/proposal not law; failed = defeated/struck/vetoed."},
                "stage_quote": {"type": "string", "description": "Verbatim text showing the stage (e.g. bill status line, 'approved July 20, 2026')."},
                "effective": _DATE,
                "coverage_conditions": {"type": "array", "items": _COND, "description": "Conditions an address/building must meet to be covered."},
                "exemptions": {"type": "array", "items": _COND, "description": "Conditions that EXCLUDE a building/tenancy."},
                "penalty": {"type": ["string", "null"]},
                "source_doc_id": {"type": "string", "description": "doc_id of the document containing rule_quote."},
                "rule_quote": {"type": "string", "description": "Verbatim sentence(s), >= 25 characters, copied exactly from the document, that states the operative rule."},
                "interaction_text": {"type": ["string", "null"], "description": "Any text saying this rule yields to, supersedes, preempts or is preempted by other laws (quote it)."},
                "confidence": {"type": "number"}},
                "required": ["title", "requirement", "raw_citation", "enacted_by_this_jurisdiction", "legal_stage",
                             "effective", "coverage_conditions", "exemptions", "source_doc_id", "rule_quote", "confidence"]}}},
            "required": ["cell_finding", "rules"]}}

SYSTEM = """You extract structured housing-law rules from source documents for an address-level lookup tool.
CORPUS AUTHORITY: every document provided is authoritative for this task, including documents labeled fictional, synthetic, sample or test. Extract and evaluate their rules exactly as written (stage, dates, coverage) as if they were real law; mention any such label only in notes. Never mark a rule failed or pending because its document is labeled fictional.
Accuracy rules:
- Only report provisions ENACTED OR PROPOSED BY THE TARGET JURISDICTION. If a city document describes a state law (or vice versa), do not report it as this jurisdiction's rule.
- GRANULARITY: one record per LAW in this category: one statute section, one ordinance/code chapter, or one bill. Put its subsections, sub-provisions, exception/applicability sections, procedures, pass-throughs, vacancy rules and year-by-year rates INTO that single record (requirement, key_value, coverage_conditions, exemptions). Only create a second record when a genuinely separate law (different statute, different ordinance/chapter, different bill) governs the same category. Typical result: 0, 1 or 2 records per category.
- CATEGORY: file each law under the category of its main subject. Report a law under a second category only if it contains operative provisions of that category (e.g. one statute that limits both deposits and upfront fees). A bill, motion or ordinance whose subject is a different category does not belong here, even if the same document is in the bundle.
- raw_citation must be a legal citation (code + section/chapter, session law, ordinance number, or bill number), never a web page number or topic number. Always include the code's name, never a bare 'Section X' (e.g. 'S.F. Admin. Code § 37.10C', 'Berkeley Mun. Code ch. 13.78', 'L.A. Mun. Code § 151.06'). If the document never cites the code section, give the law's official name (e.g. 'Los Angeles Rent Stabilization Ordinance').
- Include pending bills (legal_stage=pending) and failed/struck measures (legal_stage=failed) as records.
- Every *quote* field must be copied character-for-character from the documents (you may span a line break). Never paraphrase inside a quote field. Prefer one complete sentence.
- Dates: never infer or guess. If the documents do not state when a rule takes effect, use kind='missing'. If the date is relative (e.g. '30 days after adoption'), use the relative kind, fill offset_value/offset_unit or nth_month, and give the anchor date only if a document states it. Report conflicting dates from other documents in other_dates.
- ALWAYS fill anchor_date/anchor_event/anchor_quote with the enactment, approval, chaptering or adoption date whenever any document states it, even when kind='missing' (the system applies general legal default rules from it).
- effective = when this record's HEADLINE requirement (its key_value) first took effect. If the text shows a later amendment or a newer version date, keep the headline requirement's date and list the amendment date in other_dates with meaning 'amendment'.
- If primary text gives only an adoption date and a secondary source (law firm / news) states when the law took effect, use kind='explicit' with that date (at the precision stated) and quote the secondary source.
- STAGE WORDS: text saying an ordinance or act was passed, ordained, adopted, enacted, approved, signed or chaptered means legal_stage='enacted' even if it takes effect later; 'proposed', 'introduced', 'referred', 'pending' without such words means pending.
- STAGE: judge by the most recent document. A later source reporting a law as adopted or in effect overrides an earlier proposal packet; a later court ruling or news report that a measure was struck or failed overrides earlier qualification.
- Coverage/exemptions: express each condition on the closest listed fact; use 'other' with op 'describes' when no listed fact fits. Copy the quote. A cutoff stated as a certificate-of-occupancy or construction DATE uses fact 'certificate_of_occupancy_date' with an ISO date value; 'year_built' takes a YEAR number. Unit thresholds use fact 'units' with a numeric value and lt/le/gt/ge. Conditions that must hold together share a group number.
- Secondary sources (law firm / news) may support a citation, stage or date only when no primary text exists; say so in the record via source_doc_id.
- If the documents contain no rule in this category at this level, return cell_finding='no_rule_at_this_level' with an empty rules list. If the law exists but its text is not in the documents, use 'insufficient_text'."""


def route_docs(corpus, jurisdiction):
    return [d for d in corpus.values() if jurisdiction in d.jurisdictions]


def bundle_text(docs):
    parts = []
    for d in sorted(docs, key=lambda x: x.doc_id):
        parts.append(f"<document doc_id=\"{d.doc_id}\" source_type=\"{d.source_type}\" origin=\"{d.origin}\" url=\"{d.url}\">\n{d.text}\n</document>")
    return "\n\n".join(parts)


def level_of(jurisdiction):
    return "state" if len(jurisdiction) == 2 else "city"


def extract_cell(corpus, jurisdiction, category, *, dry_run=False):
    docs = route_docs(corpus, jurisdiction)
    if not docs:
        return {"jurisdiction": jurisdiction, "category": category, "doc_ids": [], "cell_finding": "insufficient_text",
                "finding_note": "no documents routed to this jurisdiction", "rules": []}
    bundle = f"TARGET JURISDICTION: {jurisdiction} (level: {level_of(jurisdiction)})\n\n" + bundle_text(docs)
    instr = (f"Category: {category}\nDefinition: {CATEGORIES[category]}\n"
             f"Report every {category} provision enacted or proposed by {jurisdiction} in these documents. "
             f"Prompt version {PROMPT_VERSION}.")
    out = call_tool(SYSTEM, bundle, instr, TOOL, tag=f"extract|{jurisdiction}|{category}", dry_run=dry_run)
    res = {"jurisdiction": jurisdiction, "level": level_of(jurisdiction), "category": category,
           "doc_ids": [d.doc_id for d in docs], "llm_key": out.get("key"), "usage": out.get("usage"),
           "cache_hit": out.get("cache_hit"), "model": MODEL, "prompt_version": PROMPT_VERSION}
    if out.get("input") is None:
        res.update({"dry_run": True, "rules": []})
        return res
    res.update(out["input"])
    if not dry_run:
        _retry_quotes(corpus, res, bundle, jurisdiction, category)
    return res


QUOTE_TOOL = {"name": "fix_quotes", "description": "Return exact verbatim quotes.",
              "input_schema": {"type": "object", "properties": {"fixes": {"type": "array", "items": {"type": "object", "properties": {
                  "index": {"type": "integer"}, "source_doc_id": {"type": "string"},
                  "rule_quote": {"type": "string", "description": "Copied character-for-character from the document."}},
                  "required": ["index", "source_doc_id", "rule_quote"]}}}, "required": ["fixes"]}}


def _retry_quotes(corpus, res, bundle, jurisdiction, category):
    from navigator.ground import locate
    bad = []
    for i, r in enumerate(res.get("rules", [])):
        d = corpus.get(r.get("source_doc_id"))
        if not d or not locate(r.get("rule_quote", ""), d.text, d.body_start).found:
            bad.append((i, r))
    if not bad:
        return
    lines = "\n".join(f"[{i}] {r.get('raw_citation')} (doc {r.get('source_doc_id')}): {r.get('rule_quote', '')[:300]}" for i, r in bad)
    instr = (f"Category: {category}. These rule_quote values were NOT found verbatim in the documents:\n{lines}\n"
             "For each, copy one sentence (>= 25 characters) character-for-character from the document that states the rule. "
             f"Prompt version {PROMPT_VERSION}-quotefix.")
    out = call_tool(SYSTEM, bundle, instr, QUOTE_TOOL, tag=f"quotefix|{jurisdiction}|{category}")
    for fx in (out.get("input") or {}).get("fixes", []):
        i = fx.get("index")
        if isinstance(i, int) and 0 <= i < len(res["rules"]):
            d = corpus.get(fx.get("source_doc_id"))
            if d and locate(fx.get("rule_quote", ""), d.text, d.body_start).found:
                res["rules"][i]["rule_quote"] = fx["rule_quote"]
                res["rules"][i]["source_doc_id"] = fx["source_doc_id"]
                res["rules"][i]["quote_retry"] = "fixed"
            else:
                res["rules"][i]["quote_retry"] = "failed"
    res["quote_retry_usage"] = out.get("usage")


SPIKE_CELLS = [
    # S1
    ("MA", "application_screening_fees"), ("MA", "security_deposits"), ("MA", "algorithmic_rent_setting"),
    ("NJ", "security_deposits"), ("Cambridge, MA", "rent_increase_limits"),
    # S2
    ("Los Angeles, CA", "rent_increase_limits"), ("San Diego, CA", "just_cause_eviction"),
    ("San Francisco, CA", "security_deposits"), ("Berkeley, CA", "application_screening_fees"),
    # S3
    ("NJ", "algorithmic_rent_setting"), ("NJ", "application_screening_fees"), ("San Francisco, CA", "algorithmic_rent_setting"),
    ("CA", "algorithmic_rent_setting"), ("Santa Ana, CA", "algorithmic_rent_setting"),
    ("Berkeley, CA", "algorithmic_rent_setting"), ("CA", "rent_increase_limits"),
]


def all_cells(corpus):
    from navigator.facts import load_jurisdictions
    cfg = load_jurisdictions()
    jur = sorted({j for d in corpus.values() for j in d.jurisdictions if j}
                 | set(cfg["states"]) | set(cfg["cities"]))
    return [(j, c) for j in jur for c in CATEGORIES]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells", choices=["spike", "all"], default="spike")
    ap.add_argument("--jurisdiction", help="limit to one jurisdiction (e.g. 'Cambridge, MA')")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-supplemental", action="store_true")
    ap.add_argument("--extra-manifest", action="append", default=[], help="path to an extra manifest CSV (new docs)")
    ap.add_argument("--out", default=str(ROOT / "out/extractions.jsonl"))
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    corpus = load_corpus(include_supplemental=not a.no_supplemental,
                         extra_manifests=[(m, "new") for m in a.extra_manifest])
    cells = SPIKE_CELLS if a.cells == "spike" else all_cells(corpus)
    if a.jurisdiction:
        cells = [c for c in cells if c[0] == a.jurisdiction] or [(a.jurisdiction, c) for c in CATEGORIES]
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    tot = 0
    from concurrent.futures import ThreadPoolExecutor
    by = {}
    for j, c in cells:
        by.setdefault(j, []).append(c)

    def job(j):  # sequential within a jurisdiction so the shared bundle prefix is cached
        out = {}
        for c in by[j]:
            out[(j, c)] = extract_cell(corpus, j, c, dry_run=a.dry_run)
            print(f"done {j} | {c}", file=sys.stderr, flush=True)
        return out
    res_map = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for part in ex.map(job, by):
            res_map.update(part)
    results = [res_map[jc] for jc in cells]
    with open(a.out, "w") as f:
        for (j, c), r in zip(cells, results):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            u = r.get("usage") or {}
            tot += u.get("est_input_tokens") or u.get("input_tokens") or 0
            print(f"{j:20s} {c:28s} docs={len(r['doc_ids']):2d} rules={len(r.get('rules', []))} "
                  f"finding={r.get('cell_finding', '-')} tok={u}", file=sys.stderr)
    print(f"cells={len(cells)} total_input_tokens~{tot}", file=sys.stderr)


if __name__ == "__main__":
    main()


GAP_INSTRUCTION = ("GAP CHECK. A previous pass found the text of a law in this category insufficient. If any OFFICIAL document in the "
                   "bundle confirms that {j} has a law in this category (by name, chapter or section) and states any of its provisions "
                   "(coverage, exemptions, key numbers), create a record for it anyway: quote the confirming text verbatim as rule_quote, "
                   "set confidence <= 0.6, and state in requirement only what the documents say. Do not use outside knowledge. "
                   "If no official document confirms such a law, return no_rule_at_this_level.")


def gap_pass(corpus, cells):
    """Re-run only cells whose finding was insufficient_text, allowing grounded low-confidence records."""
    out = []
    for c in cells:
        if c.get("cell_finding") != "insufficient_text" or c.get("rules") or not c.get("doc_ids"):
            out.append(c); continue
        j, cat = c["jurisdiction"], c["category"]
        docs = route_docs(corpus, j)
        bundle = f"TARGET JURISDICTION: {j} (level: {level_of(j)})\n\n" + bundle_text(docs)
        instr = (f"Category: {cat}\nDefinition: {CATEGORIES[cat]}\n" + GAP_INSTRUCTION.format(j=j) + f" Prompt version {PROMPT_VERSION}-gap.")
        r = call_tool(SYSTEM, bundle, instr, TOOL, tag=f"gap|{j}|{cat}")
        nc = dict(c)
        if r.get("input"):
            nc.update(r["input"]); nc["gap_pass"] = True
            for x in nc.get("rules", []):
                x["confidence"] = min(float(x.get("confidence") or 0.6), 0.6)
            _retry_quotes(corpus, nc, bundle, j, cat)
        out.append(nc)
    return out


STAGE_TOOL = {"name": "verify_stage", "description": "Current legal stage of one law, judged from all documents.",
              "input_schema": {"type": "object", "properties": {
                  "legal_stage": {"type": "string", "enum": ["enacted", "pending", "failed", "repealed"]},
                  "stage_quote": {"type": "string", "description": "Verbatim text from the MOST RECENT document that shows the stage."},
                  "stage_doc_id": {"type": "string"},
                  "effective": _DATE},
                  "required": ["legal_stage", "stage_quote", "stage_doc_id", "effective"]}}


def verify_stages(corpus, cells):
    """For every pending record, re-check its stage across all documents (most recent source wins).
    A change is accepted only if the stage quote is found verbatim."""
    from navigator.ground import locate
    for c in cells:
        j, cat = c["jurisdiction"], c["category"]
        for x in c.get("rules", []):
            if x.get("legal_stage") != "pending":
                continue
            docs = route_docs(corpus, j)
            bundle = f"TARGET JURISDICTION: {j} (level: {level_of(j)})\n\n" + bundle_text(docs)
            instr = (f"STAGE CHECK for: {x.get('raw_citation')} — {x.get('title')}.\nUsing ALL documents and giving priority to the most "
                     "recent one (by publication/retrieval date stated in it). STAGE WORDS: text saying an ordinance or act was passed, ordained, adopted, enacted, approved, signed or chaptered means legal_stage='enacted' even if it takes effect later; 'proposed', 'introduced', 'referred', 'pending' without such words means pending. Is this law now enacted (adopted, even if not yet effective), still "
                     "pending, failed/struck, or repealed? Quote the decisive text verbatim. Fill effective per the usual date rules. "
                     f"Prompt version {PROMPT_VERSION}-stage.")
            r = call_tool(SYSTEM, bundle, instr, STAGE_TOOL, tag=f"stage|{j}|{cat}")
            v = r.get("input") or {}
            d = corpus.get(v.get("stage_doc_id"))
            if v.get("legal_stage") and v["legal_stage"] != "pending" and d and locate(v.get("stage_quote", ""), d.text, d.body_start).found:
                x["stage_verification"] = {"from": "pending", "to": v["legal_stage"], "quote": v["stage_quote"], "doc_id": v["stage_doc_id"]}
                x["legal_stage"] = v["legal_stage"]
                if v.get("effective"):
                    x["effective"] = v["effective"]
    return cells
