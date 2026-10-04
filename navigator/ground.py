"""Exact quote grounding, citation normalization, level guard.

locate(quote, doc_text) never fuzzy-matches. It tries, in order:
  raw_exact      quote occurs verbatim in the raw file
  ws_normalized  equal after collapsing runs of whitespace (line wraps, double spaces)
  typo_normalized  additionally folds curly quotes/dashes/nbsp
and always returns the RAW substring of the source (so the emitted span is verbatim),
plus whether that raw span sits on a single line.
"""
from __future__ import annotations
import csv, re, pathlib, unicodedata
from dataclasses import dataclass
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
HEADER_RX = re.compile(r"^(SOURCE|RETRIEVED|CAPTURE):.*$", re.M)


# ------------------------------------------------------------------ corpus
@dataclass
class Doc:
    doc_id: str
    jurisdictions: list
    url: str
    source_type: str
    retrieved_at: str
    path: pathlib.Path
    origin: str          # official_corpus | supplemental | synthetic | new

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def body_start(self) -> int:
        """Offset after the SOURCE/RETRIEVED/CAPTURE header; quotes must start at or after it."""
        t = self.text
        end = 0
        for m in HEADER_RX.finditer(t):
            if m.start() > end + 2:
                break
            end = m.end()
        return end


def load_corpus(include_supplemental=True, extra_manifests=()):
    docs = {}

    def add(manifest, base, origin):
        for r in csv.DictReader(open(manifest)):
            tf = r.get("text_file") or ""
            if not tf or r.get("status", "ok") != "ok":
                continue
            docs[r["doc_id"]] = Doc(r["doc_id"], [j.strip() for j in r["jurisdictions"].split(";")],
                                    r["url"], r["source_type"], r.get("retrieved_at", ""),
                                    base / tf, origin)

    add(ROOT / "corpus/corpus_manifest.csv", ROOT / "corpus", "official_corpus")
    if include_supplemental and (ROOT / "corpus/supplemental/supplemental_manifest.csv").exists():
        add(ROOT / "corpus/supplemental/supplemental_manifest.csv", ROOT / "corpus", "supplemental")
    import os
    env = [(m, "new") for m in os.environ.get("NAV_EXTRA_MANIFESTS", "").split(",") if m.strip()]
    for m, origin in list(extra_manifests) + env:
        add(pathlib.Path(m), pathlib.Path(m).parent, origin)
    return docs


# ------------------------------------------------------------------ quote location
_TYPO = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
         " ": " ", " ": " ", " ": " ", "­": ""}


def _norm_with_map(s: str, typo: bool):
    """Return (normalized string, list mapping each normalized char index -> raw index)."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(s):
        if typo:
            ch = _TYPO.get(ch, ch)
            if ch == "":
                continue
            ch = unicodedata.normalize("NFKC", ch)
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            for c in ch:
                out.append(c)
                idx.append(i)
            prev_space = False
    return "".join(out), idx


@dataclass
class Located:
    found: bool
    mode: str                 # raw_exact | ws_normalized | typo_normalized | not_found | in_header
    start: int = -1
    end: int = -1
    span: str = ""            # verbatim raw text
    single_line: bool = False
    occurrences: int = 0


def locate(quote: str, text: str, body_start: int = 0) -> Located:
    q = quote.strip()
    if len(q) < 20:
        return Located(False, "too_short")
    i = text.find(q, body_start)
    if i >= 0:
        span = text[i:i + len(q)]
        return Located(True, "raw_exact", i, i + len(q), span, "\n" not in span, text.count(q))
    if text.find(q) >= 0:
        return Located(False, "in_header")
    for mode, typo in (("ws_normalized", False), ("typo_normalized", True)):
        nt, mp = _norm_with_map(text, typo)
        nq, _ = _norm_with_map(q, typo)
        nq = nq.strip()
        j = nt.find(nq)
        while j >= 0 and mp[j] < body_start:
            j = nt.find(nq, j + 1)
        if j >= 0:
            s, e = mp[j], mp[j + len(nq) - 1] + 1
            span = text[s:e]
            return Located(True, mode, s, e, span, "\n" not in span, nt.count(nq))
    return Located(False, "not_found")


def single_line_variant(span: str, min_len: int = 40) -> Optional[str]:
    """Longest single line inside a multi-line span (verbatim), if long enough to stand alone."""
    if "\n" not in span:
        return span
    best = max((ln.strip() for ln in span.split("\n")), key=len)
    return best if len(best) >= min_len else None


# ------------------------------------------------------------------ citations
_SEC = r"(?:§+|sec(?:tion)?s?\.?|s\.)\s*"
_CITE_RULES = [
    # (regex, formatter, kind)
    (re.compile(r"(?:Cal(?:ifornia)?\.?\s*)?(?:Civ(?:il)?\.?\s*Code|\bCIV\b)[\s,]*" + "(?:" + _SEC + r")?(\d+(?:\.\d+)*)", re.I),
     lambda m: f"Cal. Civ. Code § {m[1]}", "state_code"),
    (re.compile(r"(?:Cal(?:ifornia)?\.?\s*)?(?:Gov(?:ernment|'t)?\.?\s*Code|\bGOV\b)[\s,]*" + "(?:" + _SEC + r")?(\d+(?:\.\d+)*)", re.I),
     lambda m: f"Cal. Gov. Code § {m[1]}", "state_code"),
    (re.compile(r"(?:Cal(?:ifornia)?\.?\s*)?(?:Bus(?:iness)?\.?\s*(?:&|and)\s*Prof(?:essions)?\.?\s*Code)[\s,]*" + "(?:" + _SEC + r")?(\d+(?:\.\d+)*)", re.I),
     lambda m: f"Cal. Bus. & Prof. Code § {m[1]}", "state_code"),
    (re.compile(r"N\.?\s*J\.?\s*S\.?\s*A\.?\s*(\d+[A-Z]?:\d+[A-Z]?(?:-\d+(?:\.\d+)*)?)", re.I),
     lambda m: f"N.J.S.A. {m[1].upper()}", "state_code"),
    (re.compile(r"\bC\.\s*(\d+[A-Z]?:\d+[A-Z]?-\d+(?:\.\d+)*)", re.I),
     lambda m: f"N.J.S.A. {m[1].upper()}", "state_code"),
    (re.compile(r"P\.?\s*L\.?\s*(\d{4})\s*,?\s*c(?:hapter|\.)?\s*0*(\d+)", re.I),
     lambda m: f"P.L.{m[1]}, c.{int(m[2])}", "session_law"),
    (re.compile(r"(?:(?:M\.?\s*)?G\.?\s*L\.?|Mass(?:achusetts)?\.?\s*Gen(?:eral)?\.?\s*Laws?)\s*(?:c(?:h(?:apter)?)?\.?)\s*(\d+[A-Z]?)\s*,?\s*(?:" + _SEC + r"(\d+[A-Z]*(?:\s*(?:1/2|½))?))?", re.I),
     lambda m: f"G.L. c. {m[1].upper()}" + (f" § {re.sub(r'\s*1/2', '½', m[2]).upper()}" if m[2] else ""), "state_code"),
    (re.compile(r"Chapter\s*(\d+[A-Z]?)\s*,?\s*Section\s*([0-9A-Z½]+)", re.I),
     lambda m: f"G.L. c. {m[1].upper()} § {m[2].upper()}", "state_code_ambiguous"),
    (re.compile(r"S\.?\s*F\.?\s*Admin(?:istrative)?\.?\s*Code\s*(?:" + _SEC + r")?(\d+(?:\.\d+[A-Z]?)*)", re.I),
     lambda m: f"S.F. Admin. Code § {m[1].upper()}", "city_code"),
    (re.compile(r"\b(AB|SB|A|S|H)\.?\s*(\d{2,5})\b"),
     lambda m: f"{m[1].upper()}{'.' if m[1] in ('S', 'H') else ' '}{m[2]}", "bill"),
]


def normalize_citation(raw: Optional[str]):
    """Return (normalized, kind). Unrecognized citations keep their raw text (kind='raw')."""
    if not raw:
        return None, None
    for rx, fmt, kind in _CITE_RULES:
        m = rx.search(raw)
        if m:
            return fmt(m), kind
    return re.sub(r"\s+", " ", raw).strip(), "raw"


def citation_key(normalized: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9½]", "", (normalized or "").lower())


STATE_CODE_KINDS = {"state_code", "state_code_ambiguous", "session_law"}


def level_guard(level: str, raw_citation: str):
    """Flag a city-level record whose citation is a state code / state session law."""
    norm, kind = normalize_citation(raw_citation)
    state_bill = kind == "bill" and norm.split(" ")[0].split(".")[0] in ("AB", "SB", "A", "S", "H")
    if level == "city" and (kind in STATE_CODE_KINDS or state_bill):
        return {"flag": "state_citation_at_city_level", "normalized": norm}
    if level == "state" and kind == "city_code":
        return {"flag": "city_citation_at_state_level", "normalized": norm}
    return None


def reground_official(rules, corpus=None):
    """Citation policy: only supplied corpus text counts as verifiable. For a record grounded in a
    supplemental source, look for the SAME verbatim span in an official corpus document of the same
    jurisdiction; if found, cite that document and keep the supplemental one as corroboration.
    Records that stay supplemental are marked source_in_supplied_corpus=False (shown for review)."""
    corpus = corpus or load_corpus()
    log = []
    for r in rules:
        if r.get("source_origin") != "supplemental":
            r["source_in_supplied_corpus"] = r.get("source_origin") == "official_corpus"
            continue
        r["source_in_supplied_corpus"] = False
        span = r.get("quoted_span")
        if not span:
            continue
        for d in corpus.values():
            if d.origin != "official_corpus" or r["jurisdiction"] not in d.jurisdictions:
                continue
            loc = locate(span, d.text, d.body_start)
            if loc.found:
                r.setdefault("corroborating_sources", []).append(
                    {"doc_id": r["source_doc_id"], "url": r.get("source_url"), "retrieved_at": r.get("retrieved_at")})
                log.append({"id": r["team_rule_id"], "from": r["source_doc_id"], "to": d.doc_id, "match": loc.mode})
                r.update({"source_doc_id": d.doc_id, "source_url": d.url, "retrieved_at": d.retrieved_at,
                          "source_origin": d.origin, "quoted_span": loc.span, "quote_match": loc.mode,
                          "source_in_supplied_corpus": True})
                break
    return log
