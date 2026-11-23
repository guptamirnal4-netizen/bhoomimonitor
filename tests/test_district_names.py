"""district_names.py: bridges the live portal's inconsistently-cased
dropdown text to schema.py's curated DISTRICTS vocabulary, without
raising on the districts that don't map (Sec. 16 shakedown finding)."""

import district_names


def test_case_insensitive_match_to_canonical_spelling():
    assert district_names.normalize_for_dashboard("BELAGAVI") == "Belagavi"
    assert district_names.normalize_for_dashboard("bagalkote") == "Bagalkote"
    assert district_names.normalize_for_dashboard("Bagalkote") == "Bagalkote"


def test_unmapped_district_returns_none_not_an_exception():
    assert district_names.normalize_for_dashboard("Not A Real District") is None
