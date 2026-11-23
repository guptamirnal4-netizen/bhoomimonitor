"""
preview_fetcher.py
--------------------
Fetches a ReportPreview.aspx URL (extracted dynamically by
mutation_crawler.trigger_preview - never hardcoded here) with the SAME
session used for the MR_MutationExtract.aspx workflow, and pulls the raw
DOM structure apart into the pieces loan_parser.py needs.

Confirmed live structure (Sec. 14: DOM/table structure first, not label
regexes) across 4 real Preview pages (a charge_creation via court order,
two "amount only, no lender" charges, and one charge_release that
references a prior MR):

  - An info table identified by the "ಜಿಲ್ಲೆ :" (District) label cell -
    district/taluk/hobli/village/transactionYear/transactionNo/
    mutationType/acquisitionType/approvedDate/mrNo pairs. Field labels
    and even the header WORDING for the party column shift between
    creation ("ಕೋರ್ಟ್ ತಡೆಗೆ ಒಳಗಾದ ವಹಿವಾಟುದಾರರು") and release
    ("ಆಧಾರ/ಭೋಗ್ಯ ಮಾಡಿದವರು") - a corroborating signal, never the sole
    classifier (Sec. 10).
  - An encumbrance table identified by the "ಬಾದಿತವಾದ ಸರ್ವೆ" (Affected
    survey) header cell. Each data row: [surveyHissa, party/charge cell,
    area, (blank), remarks]. The party/charge cell sometimes holds a
    name and a SECOND line (separated by a real <br/>) that is itself an
    unspaced run-on of court/bank/case-number/amount tokens - this is
    preserved verbatim as rawSourceText; loan_parser.py, not this
    module, decides what can be safely pulled out of it. Sometimes the
    cell holds nothing but a bare amount, with no lender at all - also
    preserved as-is, not treated as an error.
  - A narrative table identified by the "ಮ್ಯುಟೇಶನ್ ಆದೇಶದ ವಿವರ" (mutation
    order details) label - free-text corroborating evidence, kept as
    supplementary rawSourceText only. It is sometimes rendered as a
    single prose paragraph and sometimes fragmented across several
    single-cell table rows (a portal rendering quirk) - both forms are
    joined with a space and preserved, never used to override the
    structured encumbrance row.
"""

import re

from bs4 import BeautifulSoup

from bhoomi_session import BhoomiSession

INFO_MARKER = "ಜಿಲ್ಲೆ"          # "District" - anchors the header info table
ENCUMBRANCE_MARKER = "ಬಾದಿತವಾದ ಸರ್ವೆ"  # "Affected survey" - anchors the charge table
NARRATIVE_MARKER = "ಮ್ಯುಟೇಶನ್ ಆದೇಶದ ವಿವರ"  # "Mutation order details"

INFO_LABELS = {
    "ಜಿಲ್ಲೆ": "district", "ತಾಲ್ಲೂಕು": "taluk", "ಗ್ರಾಮ": "village",
    "ವಹಿವಾಟು ವರ್ಷ": "transactionYear", "ವಹಿವಾಟು ಸಂಖ್ಯೆ": "transactionNo",
    "ಬದಲಾವಣೆ ರೀತಿ": "mutationType", "ಸ್ವಾಧೀನತೆ ರೀತಿ": "acquisitionType",
    "ದಿನಾಂಕ": "approvedDate", "M.R.ನಂಬರ್": "mrNo", "ಮೂಲ": "source", "ಸಂಖ್ಯೆ": "sourceNo",
}


class PreviewParseError(Exception):
    """The Preview page didn't match any known table shape at all - a
    SOURCE_PARSE_FAILED condition. Raw HTML is preserved by the caller
    regardless (never discarded), per Sec. 8.

    Confirmed live (multi-village shakedown, BELAGAVI/Chikkodi/CHIKKODI):
    MutType 21/22 - observed on mutationType "ಖಾತೆ ಬದಲಾವಣೆ" (account/khata
    change, e.g. acquisitionType "ಪಿತ್ರಾರ್ಜಿತ"/inheritance) and
    "ಕೋರ್ಟ್ ಆದೇಶ" (court order/pahani change) rows - return HTML with an
    empty <div id="divXML"></div> to a plain GET: that report type's
    content is rendered client-side by JavaScript against data this
    HTTP-only crawler never receives, not a page this parser failed to
    understand. It is legitimately not fetchable this way; it is NOT
    known to carry loan/charge data (these mutation types aren't
    loan-shaped), but per Sec. 14 we still don't suppress the failure
    based on the label - it's correctly surfaced as SOURCE_PARSE_FAILED
    for a human to confirm, same as any other unparseable page. Fixing
    this (if ever needed for a loan-shaped MutType) would require either
    finding the AJAX/XML endpoint the page's JS calls, or a headless
    browser - not guessed at here (Sec. 39: don't invent request
    parameters)."""


def fetch_preview(session: BhoomiSession, preview_url: str, referer: str) -> dict:
    """GETs the Preview URL and returns the parsed structure. Raises
    BhoomiRequestFailed/BhoomiSessionExpired (from bhoomi_session) on
    transport problems - caller maps those to PREVIEW_REQUEST_FAILED /
    SESSION_EXPIRED. Raises PreviewParseError only when the page loaded
    fine but had no recognizable structure at all."""
    resp = session.get_raw(preview_url, referer=referer)
    return parse_preview_html(resp.text, source_url=preview_url)


def _find_table_by_marker(soup: BeautifulSoup, marker: str):
    """Returns the innermost <table> whose own text contains `marker`
    (skips outer layout tables that merely contain a nested match)."""
    candidates = [t for t in soup.find_all("table") if marker in t.get_text()]
    if not candidates:
        return None
    innermost = [t for t in candidates if t.find("table") is None]
    return innermost[0] if innermost else candidates[-1]


def _parse_info_table(table) -> dict:
    """Label/value pairs alternate [label, value, label, value, ...]
    across the whole table. Deliberately NOT grouped by <tr> - the live
    portal's markup for this table is invalid HTML: several of its <td>
    cells (Hobli / M.R. Number / Acquisition Type, confirmed live) sit
    directly inside <table> with no <tr> wrapper at all. A parser that
    walks row-by-row silently drops those cells; walking every <td> in
    document order survives the malformed markup instead."""
    info = {}
    cells = table.find_all("td")
    for i in range(0, len(cells) - 1, 2):
        label_raw = cells[i].get_text(strip=True).rstrip(":").rstrip(" :\xa0").strip()
        value = cells[i + 1].get_text(" ", strip=True)
        for label_key, field in INFO_LABELS.items():
            if label_raw.startswith(label_key):
                info[field] = value
                break
    return info


def _parse_encumbrance_table(table) -> tuple[str, list[dict]]:
    rows = table.find_all("tr")
    header_idx = None
    party_header_text = ""
    for i, row in enumerate(rows):
        text = row.get_text()
        if ENCUMBRANCE_MARKER in text:
            header_idx = i
            cells = row.find_all("td")
            # party/charge column is conventionally the 2nd cell in the header row
            party_header_text = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""
            break
    if header_idx is None:
        return party_header_text, []

    data_rows = []
    for row in rows[header_idx + 1:]:
        cells = row.find_all("td")
        if not cells or not any(c.get_text(strip=True) for c in cells):
            continue
        survey_hissa = cells[0].get_text(strip=True) if len(cells) > 0 else ""
        # get_text("\n", ...) inserts a separator at each <br/> boundary,
        # preserving the original line-break structure instead of
        # silently concatenating a name and a garbled second line together.
        party_cell_raw = cells[1].get_text("\n", strip=True) if len(cells) > 1 else ""
        area_cell = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
        remarks_cell = cells[-1].get_text(" ", strip=True) if len(cells) > 4 else ""
        data_rows.append({
            "surveyHissa": survey_hissa,
            "partyCellRaw": party_cell_raw,
            "areaCell": area_cell,
            "remarksCell": remarks_cell,
        })
    return party_header_text, data_rows


def _parse_narrative(soup: BeautifulSoup) -> str:
    tables = [t for t in soup.find_all("table") if NARRATIVE_MARKER in t.get_text()]
    if not tables:
        return ""
    parts = []
    for t in tables:
        for row in t.find_all("tr"):
            text = row.get_text(" ", strip=True)
            if text and text != NARRATIVE_MARKER.rstrip(" :"):
                parts.append(text)
    return " ".join(parts).strip()


def parse_preview_html(html: str, source_url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    info_table = _find_table_by_marker(soup, INFO_MARKER)
    encumbrance_table = _find_table_by_marker(soup, ENCUMBRANCE_MARKER)

    if info_table is None and encumbrance_table is None:
        raise PreviewParseError(
            f"No recognizable info/encumbrance table found at {source_url} - "
            "portal page structure may have changed."
        )

    info = _parse_info_table(info_table) if info_table is not None else {}
    party_header, charge_rows = (
        _parse_encumbrance_table(encumbrance_table) if encumbrance_table is not None else ("", [])
    )
    narrative = _parse_narrative(soup)

    return {
        "sourceUrl": source_url,
        "info": info,
        "partyColumnHeader": party_header,
        "chargeRows": charge_rows,
        "narrativeText": narrative,
        "rawHtml": html,
    }
