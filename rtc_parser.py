"""
rtc_parser.py
--------------
Parses the HTML returned by rtc_crawler.fetch_details() into the
currentStatus shape. Confirmed live against one real, complete example
(BAGALKOTE/MUDHOL/LOKAPURA/LOKAPURA/56, Hissa 1, no ongoing mutation, a
single government-corporation owner) - a clean ASP.NET GridView with
proper <tr>/<td>/<span id=...> structure (unlike the Mutation Preview
pages' malformed info table - Sec. 14 still applies: parse by element
id/marker, not by fixed row/column position, in case other
records render differently).

IMPORTANT - what this page does NOT show (zero-assumption, Sec. 7/29):
the owner grid's columns are Owner / Extent / Owner Category /
Gov Restriction / Court stay / Alienated. None of these is a direct
"active loan/charge" flag. This parser does NOT compute hasActiveLoan
from this data - Gov Restriction and Court stay are distinct concepts
from a mortgage/bhogya charge, and inferring one from the other would
be exactly the "reasonable assumption" category the brief forbids.
Whether a currently-active charge exists is answered by cross-
referencing this owner snapshot against the mutation-history-based
loan_history (see crawler.py) - not by this parser alone.
"""

from bs4 import BeautifulSoup


class RtcParseError(Exception):
    """The page loaded but had none of the expected RTC markers at all -
    a SOURCE_PARSE_FAILED condition (Sec. 8: raw HTML is preserved by
    the caller regardless)."""


def _text_by_id(soup, element_id):
    el = soup.find(id=element_id)
    return el.get_text(strip=True) if el else None


def parse_rtc_html(html: str, source_url: str | None = None) -> dict:
    soup = BeautifulSoup(html, "lxml")

    land_id = _text_by_id(soup, "ctl00_MainContent_LandID")
    ongoing_mutation = _text_by_id(soup, "ctl00_MainContent_LblOngoingTransactionResult")
    pyki = _text_by_id(soup, "ctl00_MainContent_LblPYKIResult")

    if land_id is None and ongoing_mutation is None and pyki is None:
        raise RtcParseError(
            f"No recognizable RTC markers (Land ID/OnGoing Mutation/PYKI) found"
            f"{' at ' + source_url if source_url else ''} - portal page structure "
            "may have changed, or the cascade didn't reach a real record."
        )

    verified_el = soup.find(id="ctl00_MainContent_Blockchain_Verified")
    verification = (
        {"status": "VERIFIED", "message": verified_el.get_text(strip=True)}
        if verified_el else {"status": "UNVERIFIED", "message": None}
    )

    owners = []
    grid = soup.find("table", id=lambda x: x and "grdCurrentYearOwnerDetails" in x)
    if grid:
        for row in grid.find_all("tr")[1:]:  # skip header row
            cells = row.find_all("td")
            if len(cells) < 6:
                continue
            owners.append({
                "name": cells[0].get_text(strip=True),
                "extent": cells[1].get_text(strip=True),
                "owner_category": cells[2].get_text(strip=True),
                "gov_restriction": cells[3].get_text(strip=True),
                "court_stay": cells[4].get_text(strip=True),
                "alienated": cells[5].get_text(strip=True),
            })

    # "Details" echo panel - village/survey/surnoc/hissa/period/year as
    # the portal itself confirms them (cross-check against what was
    # requested, rather than trusting the request alone).
    details = {}
    detail_labels = {
        "Village": "village", "Survey Number": "survey_number", "Surnoc": "surnoc",
        "Hissa No": "hissa_no", "Period": "period", "Year": "year",
    }
    for span in soup.find_all("span"):
        text = span.get_text(strip=True)
        for label, field in detail_labels.items():
            if text == label:
                sib = span.find_next("span")
                if sib:
                    details[field] = sib.get_text(strip=True)

    return {
        "land_id": land_id,
        "ongoing_mutation": ongoing_mutation,
        "pyki": pyki,
        "owners": owners,
        "verification": verification,
        "details": details,
        "sourceUrl": source_url,
        "rawHtml": html,
    }
