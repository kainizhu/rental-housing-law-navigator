# Rental Housing Law Navigator

**We don't search housing law. We compile it, diff it, and prove every answer.**

RealPage × Hack-Nation, challenge 02. Solo build, 24 hours.

- **Live demo:** https://kainizhu.github.io/rental-housing-law-navigator/
- **Submission files:** [`submission/rules.json`](submission/rules.json), [`submission/lookups.json`](submission/lookups.json), [`submission/changes.json`](submission/changes.json)

> **Not legal advice.** This is a research prototype built on a fixed document corpus and public sample property data. Results can be wrong or incomplete. Read the linked source text and consult a lawyer before acting on anything here.

---

## What it does

For any of the 500 sample apartment addresses and any date, the system answers: *which rental-housing rules apply here, and why?*

- **Compile.** An LLM reads each jurisdiction × category of the corpus and fills a fixed rule schema. Every field that matters (citation, coverage conditions, exemptions, effective date) is tied to a verbatim quote that must occur in the source text.
- **Diff.** A deterministic evaluator replays the same rules at any two dates. Change reports (T1–T5, and the hour-16 ordinance) are computed this way, never written by hand.
- **Prove.** Each answer comes with a proof chain: the jurisdiction that reaches the address, the in-force check on the as-of date, each coverage condition compared with the building's facts (and where each fact came from), any interaction with other rules, and the quoted source span with document ID, URL and retrieval date.

The language model reads documents. **It never decides whether a rule applies to an address.** That step is plain, testable code.

## Results on this build

| | |
|---|---|
| Rules extracted | 49 (23 state, 26 city) across 13 jurisdictions and 6 categories |
| Jurisdiction × category cells | 78 read: 45 with rules, 31 "no rule at this level", 2 "corpus text insufficient" |
| Address results | 4,979: 3,316 applies · 890 unknown · 413 superseded by local law · 220 pending · 140 not yet effective |
| Conflict flags | 180 address-level flags (NJ FAIR Act vs. Hoboken / Jersey City ordinances) |
| Quotes | 100% verbatim in the source document |
| Change tests | T1 = 250, T2 = 90, T3 = 140 (90 conflict-flagged), T4 = 110, T5 = 0, each equal to the expected set |
| Regression suite | 19 / 19 checks pass (`python3 -m navigator.check out/v8 --golden out/golden`) |
| Unit tests | 25 / 25 pass |
| Hour-16 rehearsal | Synthetic Cambridge ordinance in two variants: 45 and 21 addresses affected, conflict with G.L. c. 40P flagged, **0 addresses changed outside Cambridge** |

## How it works

```
corpus ──► route cells ──► extract (LLM, forced tool call) ──► ground quotes verbatim
       ──► normalize citations / dates / facts (with provenance)
       ──► review: 3-vote majority (duplicates, wrong category, non-operative text)
       ──► relations: state ↔ city (conflict needs verbatim evidence)
       ──► condition roles: scope / exemption / expansion / variant / not a condition
       ──► evaluate (deterministic three-valued logic) ──► lookups, changes
       ──► regression checks + golden snapshot
```

| Step | Module | What it guarantees |
|---|---|---|
| Route | `extract.py` | Each jurisdiction × category is its own unit of work and may return 0..k rules. "No rule" is recorded as a finding. |
| Extract | `extract.py`, `llm.py` | Fixed JSON schema through a forced tool call. Every call is content-hashed, cached and logged (`logs/audit.jsonl`), so rebuilds are reproducible. |
| Ground | `ground.py` | Quotes must match the document exactly (raw, or after whitespace/typography normalization only). No fuzzy matching. A failed quote is retried once, then dropped. |
| Normalize | `normalize.py`, `dates.py`, `facts.py` | Citations keep both raw and normalized forms. Every effective date carries its provenance: `explicit_quote`, `computed_from_quote`, `default_rule` (e.g. CA January 1, MA 90 days) or `missing`. Every building fact is `observed`, `inferred_hint` or `unknown`. |
| Review | `review.py` | A 3-vote majority keeps or drops each record; the reason and vote count are kept for human review. |
| Relations | `review.py` | State ↔ city relations: cumulative, local governs where it covers, possible conflict. A conflict without verbatim evidence is downgraded. |
| Roles | `roles.py` | Separates conditions that narrow coverage (scope, exemption) from ones that never should (an "also includes" expansion, a variant that only picks an amount). A deterministic guard keeps any state law that limits local law (e.g. G.L. c. 40P). |
| Evaluate | `evaluate.py` | Kleene three-valued logic. A coverage fact that is not in the data gives **unknown**, never a guess. An exemption that depends on a fact the data never has (owner type, occupancy) is treated as not exempt, and the note says so. |
| Diff | `pipeline.py` | One generic change engine handles `as_of`, `boundary`, `pending` and `negative` tests. |
| Prove | `check.py` | 19 invariants: schema, grounding, provenance, level guard, identity, date provenance, status, relation evidence, T1–T5, plus golden checks so untouched jurisdictions cannot drift. |

### A new law (hour 16)

```bash
python3 -m navigator.hour16 --manifest NEW/manifest.csv --tests NEW/tests.json --out out/h16
```

There is no law-specific code. The command re-extracts only the jurisdictions the new documents touch, reuses the frozen baseline everywhere else, rebuilds lookups and changes, and reports every address whose result changed outside the touched jurisdictions. In both rehearsals that number was 0.

## Responsible design

- **Unknown is an answer.** 890 results are unknown, and each one says why: year built missing, unit count only guessed from an uncalibrated assessor code, coverage turning on a fact the data never has (subsidy, owner type), or a state rule that yields to a local rule whose coverage is unknown.
- **Inferred facts are not observed facts.** A unit count guessed from an assessor code is used only when that code pattern agrees with observed counts in the same dataset (≥ 95% on ≥ 10 rows). Trusting every guess would flip 517 results; the UI shows this sensitivity.
- **Legal and factual uncertainty are reported separately.** Conflicts between levels, pending measures and dates that sources disagree on are legal uncertainty. Missing building facts are factual uncertainty.
- **Conflicts are flagged, never silently resolved.** A human review queue lists conflicts, records dropped by vote, split votes, low-confidence extractions and dates taken from statutory defaults.
- **Pending and failed measures are shown, never applied.** The struck MA ballot question (T5) produces no rent cap anywhere.
- **Every interface says "not legal advice".** The UI has English and Spanish interface text; legal text stays in its source language.

## Scalability

- Work is split into independent jurisdiction × category cells, so adding a city means adding documents and an alias entry in `config/jurisdictions.json`.
- Extraction is cached by content hash: a rebuild with unchanged documents makes no model calls, and a new document re-runs only its cells.
- The evaluator is pure code over a fact table, so 500 or 500,000 addresses is a batch job, not more model calls.
- The frozen-baseline mode plus golden checks makes each new law a reviewable diff instead of a re-run of everything.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env                        # OPENAI_API_KEY=..., NAV_MODEL=gpt-5.4

# full extraction (paid; cached afterwards)
python3 -m navigator.extract --cells all --workers 4 --out out/extract.jsonl
# build: gap pass, normalize, refine, review, relations, roles, evaluate, changes
NAV_VOTES=3 python3 -m navigator.pipeline --extractions out/extract.jsonl --out out/run --review --refine
# prove
python3 -m navigator.check out/run --golden out/golden
python3 -m pytest -q tests
# UI data + GitHub Pages copy
python3 -m navigator.ui_data --run out/run --out web/data.js && web/build_pages.sh
```

Checks and tests need no API key: `out/v8` (this build) and `out/golden` are committed.

## Repository map

| Path | Contents |
|---|---|
| `navigator/` | Pipeline code (extract, ground, normalize, dates, facts, review, roles, refine, evaluate, pipeline, hour16, check, ui_data) |
| `submission/` | The three graded files |
| `out/v8/` | Current build with every intermediate: review decisions, dropped records, relations, roles, refine log |
| `corpus/supplemental/` | 12 public supplemental documents with source URLs and retrieval dates |
| `spike/synthetic/` | Synthetic hour-16 ordinance used for rehearsal (fictional, labeled as such) |
| `web/`, `docs/` | Static UI; `docs/` is served by GitHub Pages |
| `logs/audit.jsonl` | Every model call: tag, model, token usage, latency |

## Known limits and open questions

- Jurisdiction is resolved from postal city through an alias table, not a geocoder. Mailing cities that straddle a city line could be misassigned.
- Year built is not the certificate-of-occupancy date. Buildings in a cutoff year are reported as unknown.
- 23 rules have no effective date in the corpus (mostly long-standing statutes); they are treated as in force and marked `missing`.
- New Jersey, Boston and Berkeley unit counts are mostly guesses from assessor codes that do not calibrate, so many NJ results are unknown by design.
- Relation types come from a model vote with verbatim evidence. They are inputs for a reviewer, not legal conclusions.
