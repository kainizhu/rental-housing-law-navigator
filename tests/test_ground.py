from navigator.ground import locate, load_corpus, normalize_citation, level_guard, single_line_variant

C = load_corpus()

def test_raw_exact():
    d = C["D069"]; t = d.text
    q = "This act shall take effect on the first day of"
    r = locate(q, t, d.body_start)
    assert r.found and r.mode == "raw_exact" and r.span == q and r.single_line

def test_wrapped_line_ws_normalized():
    d = C["D069"]; t = d.text
    q = "This act shall take effect on the first day of the twelfth month next following the date of enactment."
    r = locate(q, t, d.body_start)
    assert r.found and r.mode == "ws_normalized"
    assert r.span in t and "\n" in r.span           # emitted span is verbatim raw text

def test_double_space():
    d = C["D052"]; t = d.text
    r = locate("occupancy calculated at the same rate as the first month; and,", t, d.body_start)
    assert r.found and r.span in t

def test_curly_quotes():
    d = C["D069"]; t = d.text
    r = locate('This act shall be known and may be cited as the "Forbidding the Algorithmic Inflation of Rent (FAIR) Act."', t, d.body_start)
    assert r.found and r.mode == "typo_normalized" and r.span in t

def test_header_not_quotable():
    d = C["D069"]
    assert not locate("https://pub.njleg.state.nj.us/Bills/2026/AL26/43_.HTM", d.text, d.body_start).found

def test_no_fuzzy():
    d = C["D069"]
    assert not locate("This act shall take effect on the first day of the eleventh month", d.text, d.body_start).found

def test_supplemental_loaded():
    assert C["S001"].origin == "supplemental" and "Hoboken" in C["S001"].jurisdictions[0]

def test_citations():
    assert normalize_citation("California Civil Code section 1947.12")[0] == "Cal. Civ. Code § 1947.12"
    assert normalize_citation("Cal. Civ. Code §1950.5")[0] == "Cal. Civ. Code § 1950.5"
    assert normalize_citation("C.56:9-20")[0] == "N.J.S.A. 56:9-20"
    assert normalize_citation("P.L. 2026, c.043")[0] == "P.L.2026, c.43"
    assert normalize_citation("M.G.L. c.186, s.15B")[0] in ("G.L. c. 186 § 15B",)
    assert normalize_citation("S.F. Admin. Code §37.10C")[0] == "S.F. Admin. Code § 37.10C"
    assert normalize_citation("Senate Bill S.2983")[0] == "S.2983"

def test_level_guard():
    assert level_guard("city", "Cal. Civ. Code § 1947.12")["flag"] == "state_citation_at_city_level"
    assert level_guard("city", "S.F. Admin. Code § 37.9") is None

def test_single_line_variant():
    assert single_line_variant("short\nline that is definitely longer than forty characters here") \
        == "line that is definitely longer than forty characters here"

def test_level_guard_state_bill():
    assert level_guard("city", "AB 1482")["flag"] == "state_citation_at_city_level"

def test_mass_gen_laws():
    assert normalize_citation("Mass. Gen. Laws ch. 112, § 87DDD1/2")[0] == "G.L. c. 112 § 87DDD½"
    assert normalize_citation("Massachusetts General Laws Chapter 186, Section 15B")[0] == "G.L. c. 186 § 15B"
