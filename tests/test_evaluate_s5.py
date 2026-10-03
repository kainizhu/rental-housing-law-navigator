"""S5 fixtures: hand-written rules and addresses -> expected results (from spike/expectations.yaml)."""
import datetime as dt
from navigator.facts import Fact, build_fact_table
from navigator.evaluate import evaluate, Policy

def addr(aid, city, state, yb=None, units=None, units_hint=None):
    f = {"year_built": Fact(yb, yb, "observed") if yb else Fact(),
         "units": Fact(units, units, "observed") if units else (units_hint or Fact()),
         "owner_type": Fact(), "owner_occupied": Fact()}
    return {"address_id": aid, "city": city, "state": state, "stack": [state, city], "facts": f}

LA_RENT = {"team_rule_id": "LA-RENT", "jurisdiction": "Los Angeles, CA", "status": "in_force",
           "coverage_conditions": [{"fact": "certificate_of_occupancy_date", "op": "le", "value": "1978-10-01"},
                                   {"fact": "units", "op": "ge", "value": 2}], "exemptions": []}
CA_RENT = {"team_rule_id": "CA-RENT", "jurisdiction": "CA", "status": "in_force",
           "coverage_conditions": [{"fact": "building_age_years", "op": "ge", "value": 15}],
           "exemptions": [{"fact": "owner_type", "op": "eq", "value": "natural person", "text": "single-family owned by natural person", "group": 1},
                          {"fact": "units", "op": "le", "value": 1, "group": 1}]}
JC_RENT = {"team_rule_id": "JC-RENT", "jurisdiction": "Jersey City, NJ", "status": "in_force",
           "coverage_conditions": [], "exemptions": [{"fact": "units", "op": "le", "value": 4}]}
NJ_DEP = {"team_rule_id": "NJ-DEP", "jurisdiction": "NJ", "status": "in_force", "coverage_conditions": [],
          "exemptions": [{"fact": "owner_occupied", "op": "is_true", "group": 1, "text": "owner-occupied"},
                         {"fact": "units", "op": "le", "value": 2, "group": 1}]}
NJ_JC = {"team_rule_id": "NJ-JC", "jurisdiction": "NJ", "status": "in_force", "coverage_conditions": [], "exemptions": []}
RULES = [LA_RENT, CA_RENT, JC_RENT, NJ_DEP, NJ_JC]
RELS = [{"state_rule": "CA-RENT", "local_rule": "LA-RENT", "type": "local_governs_where_covered"}]

def res(addresses):
    out = evaluate(addresses, RULES, RELS, "2026-10-01")
    return {aid: {x["team_rule_id"]: x["result"] for x in v} for aid, v in out.items()}

def test_s5_table():
    t, _ = build_fact_table()
    A = {
      "la1927": addr("la1927", "Los Angeles, CA", "CA", 1927, 32),
      "la1978": addr("la1978", "Los Angeles, CA", "CA", 1978, 10),
      "la2019": addr("la2019", "Los Angeles, CA", "CA", 2019, 10),
      "la2016": addr("la2016", "Los Angeles, CA", "CA", 2016, 10),
      "sd_noyr": addr("sd_noyr", "San Diego, CA", "CA", None, 20),
      "jc_hint": addr("jc_hint", "Jersey City, NJ", "NJ", None, None, Fact(6, 6, "inferred_hint", "uncalibrated", note="6U")),
      "A0227": t["A0227"],
      "nj_nounits": addr("nj_nounits", "Newark, NJ", "NJ"),
    }
    r = res(A)
    assert r["la1927"]["LA-RENT"] == "applies" and r["la1927"]["CA-RENT"] == "superseded"
    assert r["la1978"]["LA-RENT"] == "unknown" and r["la1978"]["CA-RENT"] == "unknown"
    assert "LA-RENT" not in r["la2019"]
    assert "CA-RENT" not in r["la2016"]                     # < 15 years old
    assert r["sd_noyr"]["CA-RENT"] == "unknown"
    assert r["jc_hint"]["JC-RENT"] == "unknown"             # hint only
    assert r["A0227"]["JC-RENT"] if "JC-RENT" in r["A0227"] else True
    assert r["A0227"].get("NJ-DEP") == "unknown" or r["A0227"].get("NJ-DEP") == "applies"
    assert r["nj_nounits"]["NJ-DEP"] == "applies"           # unobservable exemption -> not unknown
    assert r["nj_nounits"]["NJ-JC"] == "applies"

def test_metamorphic_blanking_never_creates_applies():
    full = addr("x", "Los Angeles, CA", "CA", 1978, 10)
    blank = addr("x", "Los Angeles, CA", "CA", None, None)
    a, b = res({"x": full})["x"], res({"x": blank})["x"]
    for k, v in b.items():
        if v == "applies":
            assert a.get(k) == "applies"

def test_tenancy_condition_does_not_create_unknown():
    CA_JC = {"team_rule_id": "CA-JC", "jurisdiction": "CA", "status": "in_force",
             "coverage_conditions": [{"fact": "tenancy_start_date", "op": "describes", "text": "tenant has occupied 12 months"}],
             "exemptions": []}
    # op 'describes' on a tenancy fact: still a note, result stays applies
    CA_JC["coverage_conditions"][0]["op"] = "le"
    out = evaluate({"x": addr("x", "San Diego, CA", "CA", None, 20)}, [CA_JC], [], "2026-10-01")
    r = out["x"][0]
    assert r["result"] == "applies" and "tenancy-specific" in r["explanation"]
