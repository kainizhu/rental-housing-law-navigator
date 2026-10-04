"""Address fact table with provenance.

Every building fact is a Fact(lo, hi, provenance, note):
  provenance = "observed"       value read from a data column
             | "inferred_hint"  derived from use_description/use_code; never treated as observed
             | "unknown"        nothing usable
Intervals let the evaluator do three-valued logic: a predicate is true/false only when the
whole interval agrees, otherwise unknown.

Hints carry `support`: "calibrated" when the hint's pattern family agrees with observed
values on enough rows where both exist (computed from the data, not hard-coded),
"contradicted" when a row's own observed value disagrees, else "uncalibrated".
"""
from __future__ import annotations
import csv, json, re, pathlib
from dataclasses import dataclass, asdict, field
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[1]


@dataclass
class Fact:
    lo: Optional[float] = None
    hi: Optional[float] = None
    provenance: str = "unknown"
    support: Optional[str] = None
    source: Optional[str] = None
    note: Optional[str] = None

    @property
    def known(self) -> bool:
        return self.provenance != "unknown"


# --- unit-count hint patterns: (family, regex, fn(match)->(lo,hi)) -------------------------
_UNIT_PATTERNS = [
    ("nUnits_suffix", re.compile(r"(\d+)U\b"), None),                       # NJ MOD-IV "6B-20U-G"
    ("range_to", re.compile(r"(\d+)\s*to\s*(\d+)\s*units", re.I), lambda m: (int(m[1]), int(m[2]))),
    ("range_dash_units", re.compile(r"(\d+)\s*-\s*(\d+)\s*-?\s*UNIT", re.I), lambda m: (int(m[1]), int(m[2]))),
    ("gt_n_unit", re.compile(r">\s*(\d+)\s*-?\s*UNIT", re.I), lambda m: (int(m[1]) + 1, None)),
    ("n_plus", re.compile(r"(\d+)\s*\+\s*units?", re.I), lambda m: (int(m[1]), None)),
    ("n_or_more", re.compile(r"(\d+)\s*units?\s*or\s*more", re.I), lambda m: (int(m[1]), None)),
    ("word_or_more", re.compile(r"\b(two|three|four|five|six)\s+or\s+more\b", re.I),
     lambda m: ({"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}[m[1].lower()], None)),
]
# use_code families (class codes); interpretation is a hint and gets calibrated like the rest
_USE_CODE_HINTS = {("NJ", "4C"): (5, None, "NJ MOD-IV property class 4C (apartment) conventionally 5+ units")}


def _unit_hint(desc: str):
    for fam, rx, fn in _UNIT_PATTERNS:
        if fam == "nUnits_suffix":
            ms = rx.findall(desc)
            if ms:
                n = sum(int(x) for x in ms)
                return fam, (n, n)
            continue
        m = rx.search(desc)
        if m:
            return fam, fn(m)
    return None, None


def _contains(lo, hi, v):
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def load_jurisdictions():
    return json.load(open(ROOT / "config/jurisdictions.json"))


def resolve_city(postal_city: str, state: str, cfg=None):
    cfg = cfg or load_jurisdictions()
    pc = postal_city.strip().lower()
    for city, spec in cfg["cities"].items():
        if city.endswith(", " + state) and pc in (a.lower() for a in spec["aliases"]):
            return city, "observed_alias"
    return None, "unresolved"


def build_fact_table(path=None, min_calibration_rows: int = 10, min_agreement: float = 0.95):
    import os
    path = path or ROOT / "data/sample_addresses.csv"
    rows = list(csv.DictReader(open(path)))
    for extra in filter(None, os.environ.get("NAV_EXTRA_ADDRESSES", "").split(",")):
        rows += [{**{k: "" for k in rows[0]}, **x} for x in csv.DictReader(open(extra))]
    cfg = load_jurisdictions()

    # calibrate hint families against observed units
    fam_stats = {}
    for r in rows:
        fam, iv = _unit_hint(r["use_description"])
        u = r["units"].strip()
        if fam and u:
            ok = _contains(iv[0], iv[1], int(u))
            s = fam_stats.setdefault((r["source_dataset"], fam), [0, 0])
            s[0] += ok
            s[1] += 1
    calibrated = {f for f, (ok, n) in fam_stats.items() if n >= min_calibration_rows and ok / n >= min_agreement}

    table = {}
    for r in rows:
        aid = r["address_id"]
        city, city_prov = resolve_city(r["postal_city"], r["state"], cfg)
        facts = {}
        # year built
        yb = r["year_built"].strip()
        facts["year_built"] = Fact(int(yb), int(yb), "observed", source="year_built") if yb else \
            Fact(note="year_built missing in assessor data")
        # units
        u = r["units"].strip()
        fam, iv = _unit_hint(r["use_description"])
        code_hint = _USE_CODE_HINTS.get((r["state"], r["use_code"].strip()))
        if u:
            f = Fact(int(u), int(u), "observed", source="units")
            conflicts = []
            if fam and not _contains(iv[0], iv[1], int(u)):
                conflicts.append(f"use_description '{r['use_description']}' suggests {iv}")
            if code_hint and not _contains(code_hint[0], code_hint[1], int(u)):
                conflicts.append(f"use_code {r['use_code']} suggests >= {code_hint[0]}")
            if conflicts:
                f.support = "contradicted"
                f.note = "; ".join(conflicts)
            facts["units"] = f
        elif fam:
            facts["units"] = Fact(iv[0], iv[1], "inferred_hint",
                                  support="calibrated" if (r["source_dataset"], fam) in calibrated else "uncalibrated",
                                  source=f"use_description:{fam}",
                                  note=f"'{r['use_description']}' suggests units in [{iv[0]}, {iv[1] or '∞'}]")
        elif code_hint:
            facts["units"] = Fact(code_hint[0], code_hint[1], "inferred_hint", support="uncalibrated",
                                  source=f"use_code:{r['use_code']}", note=code_hint[2])
        else:
            facts["units"] = Fact(note="units missing; no usable use code")
        # unobservable facts
        facts["owner_type"] = Fact(note="owner identity excluded from sample data")
        facts["owner_occupied"] = Fact(note="occupancy not in sample data")
        table[aid] = {
            "address_id": aid,
            "street_address": r["street_address"],
            "postal_city": r["postal_city"],
            "state": r["state"],
            "city": city,
            "city_provenance": city_prov,
            "stack": [r["state"]] + ([city] if city else []),
            "use_code": r["use_code"],
            "use_description": r["use_description"],
            "facts": facts,
        }
    return table, {"hint_family_stats": {f"{d}|{f}": v for (d, f), v in fam_stats.items()},
                   "calibrated_families": sorted(f"{d}|{f}" for d, f in calibrated)}


def fact_table_json(table):
    return {k: {**v, "facts": {fk: asdict(fv) for fk, fv in v["facts"].items()}} for k, v in table.items()}


if __name__ == "__main__":
    import collections
    t, cal = build_fact_table()
    print("calibration:", cal)
    print("unresolved cities:", [v["postal_city"] for v in t.values() if not v["city"]])
    c = collections.Counter((v["state"], v["facts"]["units"].provenance, v["facts"]["units"].support) for v in t.values())
    for k, n in sorted(c.items(), key=str):
        print(k, n)
    print("A0227", asdict(t["A0227"]["facts"]["units"]))
