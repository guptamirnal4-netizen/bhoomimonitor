"""rtc_parser.py against a real, captured Fetch-Details response
(BAGALKOTE/MUDHOL/LOKAPURA/LOKAPURA/56, Hissa 1) - confirmed live, no
CAPTCHA involved in this flow."""

import pytest

import rtc_parser
from conftest import load_fixture


def test_real_rtc_fixture_parses_all_known_fields():
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    data = rtc_parser.parse_rtc_html(html, source_url="https://example/rtc")

    assert data["land_id"] == "5986 1500 1025"
    assert data["ongoing_mutation"] == "No"
    assert data["pyki"] == "No"
    assert data["verification"] == {
        "status": "VERIFIED",
        "message": "RTC Documents you view here, are Blockchain Verified now",
    }
    assert data["details"] == {
        "village": "LOKAPURA", "survey_number": "56", "surnoc": "*",
        "hissa_no": "1", "period": "2017-08-20 12:20:00 To Till Date (2026-2027)",
        "year": "2026-2027",
    }


def test_owner_grid_parsed_with_kannada_name_preserved_verbatim():
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    data = rtc_parser.parse_rtc_html(html)
    assert len(data["owners"]) == 1
    owner = data["owners"][0]
    assert owner["name"] == "ಕೃಷ್ಣಾ ಭಾಗ್ಯ ಜಲ ನಿಗಮ ನಿಯಮಿತ"  # not translated/transliterated
    assert owner["extent"] == "12.21.0"
    assert owner["owner_category"] == "Private"
    assert owner["gov_restriction"] == "No"
    assert owner["court_stay"] == "No"
    assert owner["alienated"] == "No"


def test_no_active_loan_field_is_fabricated_from_owner_grid():
    # Zero-assumption: the owner grid has no direct loan/charge column,
    # so the parser must not invent a hasActiveLoan/bankName/loan_amount
    # key from gov_restriction/court_stay.
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    data = rtc_parser.parse_rtc_html(html)
    for owner in data["owners"]:
        assert "hasActiveLoan" not in owner
        assert "bankName" not in owner
        assert "loan_amount" not in owner
    assert "hasActiveLoan" not in data


def test_raw_html_always_preserved():
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    data = rtc_parser.parse_rtc_html(html)
    assert data["rawHtml"] == html


def test_parse_error_on_completely_unrecognized_page():
    with pytest.raises(rtc_parser.RtcParseError):
        rtc_parser.parse_rtc_html("<html><body>Portal error 500</body></html>", source_url="https://example/broken")
