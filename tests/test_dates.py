from navigator.dates import resolve_effective, status_as_of

def r(eff, level="city", state="NJ", stage="enacted"):
    return resolve_effective(eff, level=level, state=state, legal_stage=stage)

def test_fair_act():
    e = r({"kind": "first_of_month_after_anchor", "anchor_event": "enactment", "anchor_date": "2026-07-20", "nth_month": 12}, level="state")
    assert e["date"] == "2027-07-01" and e["provenance"] == "computed_from_quote"
    assert status_as_of("enacted", e, None, "2026-10-01")[0] == "not_yet_effective"
    assert status_as_of("enacted", e, None, "2027-07-02")[0] == "in_force"

def test_cambridge_synthetic():
    assert r({"kind": "first_of_month_after_anchor", "anchor_date": "2026-09-14", "nth_month": 6})["date"] == "2027-03-01"

def test_santa_ana_30_days():
    assert r({"kind": "offset_after_anchor", "anchor_date": "2026-03-03", "offset_value": 30, "offset_unit": "days"}, state="CA")["date"] == "2026-04-02"

def test_explicit_month_precision():
    e = r({"kind": "explicit", "explicit_date": "2025-06"})
    assert e["date"] == "2025-06" and e["precision"] == "month"
    assert status_as_of("enacted", e, None, "2025-06-15") == ("in_force", "within_stated_period")

def test_missing_no_fabrication():
    e = r({"kind": "missing"}, state="CA")
    assert e["date"] is None and e["provenance"] == "missing"

def test_ca_default_rule_only_state_level():
    e = r({"kind": "missing", "anchor_date": "2025-10-06", "anchor_event": "chaptered"}, level="state", state="CA")
    assert e["date"] == "2026-01-01" and e["provenance"] == "default_rule"
    assert status_as_of("enacted", e, None, "2025-12-31")[0] == "not_yet_effective"
    assert status_as_of("enacted", e, None, "2026-01-02")[0] == "in_force"
    assert r({"kind": "missing", "anchor_date": "2025-10-06"}, level="city", state="CA")["date"] is None

def test_relative_without_anchor_is_missing():
    assert r({"kind": "offset_after_anchor", "offset_value": 30, "offset_unit": "days"})["date"] is None

def test_sunset_and_pending():
    assert status_as_of("enacted", {"date": "2020-01-01"}, "2030-01-01", "2030-01-02")[0] == "expired"
    assert status_as_of("pending", {}, None, "2026-10-01")[0] == "pending"

def test_quote_fallback_nth_month():
    e = r({"kind": "first_of_month_after_anchor", "anchor_date": "2026-01-20",
           "quote": "This act shall take effect on the first day of the fourth month next following enactment"})
    assert e["date"] == "2026-05-01"

def test_quote_fallback_days():
    e = r({"kind": "offset_after_anchor", "anchor_date": "2026-03-03", "quote": "This ordinance shall become effective thirty (30) days after its adoption."}, state="CA")
    assert e["date"] == "2026-04-02"
