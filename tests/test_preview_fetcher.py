"""Structural (DOM-first, not label-regex) parsing of real Preview
fixtures - Sec. 14. Checks that the info table and encumbrance table are
found and split apart correctly, and that the party-column header text
(a corroborating signal, not the sole classifier) differs between a
charge_creation and a charge_release page exactly as observed live."""

import preview_fetcher
from conftest import load_fixture


def test_info_table_parsed_for_charge_creation_fixture():
    html = load_fixture("preview_mr3760_charge_creation_court_order.html")
    data = preview_fetcher.parse_preview_html(html, source_url="https://example/mr3760")
    assert data["info"]["district"] == "ಬಾಗಲಕೋಟ"
    assert data["info"]["mrNo"] == "3760"
    assert data["info"]["mutationType"] == "ಹಕ್ಕು ಮತ್ತು ಋಣ"
    assert data["info"]["acquisitionType"] == "ಆಧಾರ/ಭೋಗ್ಯ"


def test_encumbrance_row_extracted_with_linebreak_preserved():
    html = load_fixture("preview_mr3760_charge_creation_court_order.html")
    data = preview_fetcher.parse_preview_html(html, source_url="https://example/mr3760")
    assert len(data["chargeRows"]) == 1
    row = data["chargeRows"][0]
    assert row["surveyHissa"] == "56/*/1"
    # the <br/> boundary must survive as a newline, not vanish into a
    # silently-concatenated blob
    assert "\n" in row["partyCellRaw"]
    assert row["partyCellRaw"].startswith("ಗುರುರಾಜ")


def test_party_column_header_differs_between_creation_and_release():
    creation = preview_fetcher.parse_preview_html(
        load_fixture("preview_mr3760_charge_creation_court_order.html"), "https://example/creation")
    release = preview_fetcher.parse_preview_html(
        load_fixture("preview_mr120_charge_release.html"), "https://example/release")
    assert creation["partyColumnHeader"] != release["partyColumnHeader"]
    assert "ಕೋರ್ಟ್" in creation["partyColumnHeader"]
    assert "ಆಧಾರ" in release["partyColumnHeader"]


def test_narrative_text_extracted_as_supplementary_evidence():
    html = load_fixture("preview_mr3760_charge_creation_court_order.html")
    data = preview_fetcher.parse_preview_html(html, source_url="https://example/mr3760")
    assert "ಜೆ ಎಂ ಎಫ್ ಸಿ" in data["narrativeText"]


def test_raw_html_always_preserved():
    html = load_fixture("preview_mr55_amount_only_no_lender.html")
    data = preview_fetcher.parse_preview_html(html, source_url="https://example/mr55")
    assert data["rawHtml"] == html


def test_parse_error_on_completely_unrecognized_page():
    import pytest
    with pytest.raises(preview_fetcher.PreviewParseError):
        preview_fetcher.parse_preview_html("<html><body>Portal error 500</body></html>",
                                            source_url="https://example/broken")
