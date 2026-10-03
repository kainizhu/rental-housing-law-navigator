"""Deterministic effective-date resolution and status-as-of.

Provenance tags: explicit_quote | computed_from_quote | default_rule | missing.
Dates keep the precision the source gives: 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD'."""
from __future__ import annotations
import calendar, datetime as dt, json, pathlib, re
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]
_DEFAULTS = None


def defaults():
    global _DEFAULTS
    if _DEFAULTS is None:
        _DEFAULTS = json.load(open(ROOT / "config/default_rules.json"))
    return _DEFAULTS


def parse(s: Optional[str]):
    """Return (date_of_period_start, date_of_period_end, precision) or None."""
    if not s:
        return None
    s = s.strip()
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", s)
    if not m:
        return None
    y = int(m[1])
    if m[3]:
        d = dt.date(y, int(m[2]), int(m[3]))
        return d, d, "day"
    if m[2]:
        mo = int(m[2])
        return dt.date(y, mo, 1), dt.date(y, mo, calendar.monthrange(y, mo)[1]), "month"
    return dt.date(y, 1, 1), dt.date(y, 12, 31), "year"


def fmt(d: dt.date, precision="day"):
    return {"day": d.isoformat(), "month": d.strftime("%Y-%m"), "year": str(d.year)}[precision]


def add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


_ORD = {w: i for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split(), 1)}
_CARD = {w: i for i, w in enumerate("one two three four five six seven eight nine ten eleven twelve".split(), 1)}
_CARD.update({"thirty": 30, "sixty": 60, "ninety": 90, "twenty": 20, "fifteen": 15, "forty-five": 45, "one hundred eighty": 180})


def parse_relative(q: str):
    """Parse 'first day of the twelfth month next following ...' or 'thirty (30) days after ...'."""
    s = " ".join(q.lower().split())
    m = re.search(r"first day of the (?:(\d+)(?:st|nd|rd|th)|([a-z]+)) (?:calendar )?month (?:next )?following", s)
    if m:
        n = int(m[1]) if m[1] else _ORD.get(m[2])
        if n:
            return {"kind": "first_of_month_after_anchor", "nth_month": n}
    m = re.search(r"(?:([a-z\- ]+?)\s*)?\(?(\d+)?\)?\s*(days?|months?)\s+(?:after|following)", s)
    if m:
        n = int(m[2]) if m[2] else _CARD.get((m[1] or "").strip().split(" ")[-1])
        if n:
            return {"kind": "offset_after_anchor", "offset_value": n, "offset_unit": "days" if m[3].startswith("day") else "months"}
    return None


def resolve_effective(eff: dict, *, level: str, state: str, legal_stage: str) -> dict:
    """eff: the extractor's 'effective' object. Returns {date, precision, provenance, quote, note}."""
    eff = eff or {}
    kind = eff.get("kind", "missing")
    q = eff.get("quote")
    if kind == "explicit":
        p = parse(eff.get("explicit_date"))
        if p:
            return {"date": fmt(p[0], p[2]), "precision": p[2], "provenance": "explicit_quote", "quote": q}
        kind = "missing"
    anchor = parse(eff.get("anchor_date"))
    if kind == "offset_after_anchor" and anchor and anchor[2] == "day" and eff.get("offset_value") is not None:
        n, unit = int(eff["offset_value"]), eff.get("offset_unit") or "days"
        d = anchor[0] + dt.timedelta(days=n) if unit == "days" else \
            add_months(anchor[0], n if unit == "months" else 12 * n)
        return {"date": d.isoformat(), "precision": "day", "provenance": "computed_from_quote", "quote": q,
                "anchor_quote": eff.get("anchor_quote"), "note": f"{n} {unit} after {eff.get('anchor_event')} {anchor[0]}"}
    if kind == "first_of_month_after_anchor" and anchor and eff.get("nth_month"):
        d = add_months(anchor[0].replace(day=1), int(eff["nth_month"]))
        return {"date": d.isoformat(), "precision": "day", "provenance": "computed_from_quote", "quote": q,
                "anchor_quote": eff.get("anchor_quote"),
                "note": f"first day of month {int(eff['nth_month'])} following {eff.get('anchor_event')} {anchor[0]}"}
    # deterministic fallback: read the relative formula from the quote if fields were left empty
    if kind in ("offset_after_anchor", "first_of_month_after_anchor") and anchor and anchor[2] == "day" and q:
        parsed = parse_relative(q)
        if parsed:
            return resolve_effective({**eff, **parsed}, level=level, state=state, legal_stage=legal_stage)
    if kind in ("offset_after_anchor", "first_of_month_after_anchor"):
        return {"date": None, "precision": None, "provenance": "missing", "quote": q,
                "note": "relative effective date but anchor date not stated in documents"}
    # missing -> state default rule, only for enacted state statutes with a known enactment date
    rule = defaults().get(state, {}).get("statute_effective")
    if level == "state" and legal_stage == "enacted" and rule and anchor and anchor[2] == "day":
        if rule["rule"] == "january_1_following_enactment_year":
            d = dt.date(anchor[0].year + 1, 1, 1)
        elif rule["rule"] == "days_after_enactment":
            d = anchor[0] + dt.timedelta(days=rule["days"])
        else:
            d = None
        if d:
            return {"date": d.isoformat(), "precision": "day", "provenance": "default_rule", "quote": eff.get("anchor_quote"),
                    "note": rule["source"]}
    return {"date": None, "precision": None, "provenance": "missing", "quote": q,
            "note": "effective date not stated in the documents"}


def status_as_of(legal_stage: str, effective: dict, sunset: Optional[str], as_of: str):
    """Return (status, certainty) with status in in_force|not_yet_effective|pending|failed|expired."""
    a = dt.date.fromisoformat(as_of)
    if legal_stage == "pending":
        return "pending", "certain"
    if legal_stage in ("failed", "repealed"):
        return "failed", "certain"
    s = parse(sunset)
    if s and a >= s[0]:
        return "expired", "certain"
    p = parse(effective.get("date")) if effective else None
    if not p:
        return "in_force", "date_unknown"          # enacted, date not stated: assume in force, flag it
    start, end, _ = p
    if a < start:
        return "not_yet_effective", "certain"
    if a > end:
        return "in_force", "certain"
    return "in_force", "within_stated_period"      # as_of inside a month/year-precision effective date
