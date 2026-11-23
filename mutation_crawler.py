"""
mutation_crawler.py
---------------------
Drives MR_MutationExtract.aspx end to end for one district/taluk/hobli/
village/survey unit: cascading dropdown selection -> survey number entry
-> "Fetch Details" -> parse the mutation grid -> for a chosen row,
"Select" -> "Preview" -> capture the dynamically-generated Preview URL.

Every step here was verified against the live page before being written
(see the inspection notes in bhoomi_session.py's module docstring) - the
field names, event targets, and postback shapes below are what the
portal actually does, not what the manual browser walkthrough implied.

MutType/Type/Phodi_Process are NEVER hardcoded - trigger_preview() reads
them out of the live `window.open(...)` script the portal emits for the
specific mutation row selected.
"""

import logging
import re

from bhoomi_session import BhoomiSession, BhoomiRequestFailed

log = logging.getLogger("bhoomi")

PATH = "MR_MutationExtract.aspx"
P = "ctl00$MainContent$"  # form-field name prefix for every control on this page

DROPDOWN_IDS = {
    "district": "ctl00_MainContent_drpdist",
    "taluk": "ctl00_MainContent_drptaluk",
    "hobli": "ctl00_MainContent_drphobli",
    "village": "ctl00_MainContent_drpvillage",
}
DROPDOWN_FIELDS = {
    "district": P + "drpdist",
    "taluk": P + "drptaluk",
    "hobli": P + "drphobli",
    "village": P + "drpvillage",
}

MUTATION_GRID_ID = "ctl00_MainContent_gv_MutationDetails"
SURVEY_FIELD = P + "txtSurvey"
FETCH_BUTTON_FIELD = P + "btnFetch"
PREVIEW_BUTTON_FIELD = P + "btnPreview"

# Mutation grid column order, as observed live:
# ['', 'Survey No', 'Transaction Year', 'Transaction No', 'MR Number',
#  'Mutation Type', 'Acquistion Type', 'Tahsildar Approved Date']
GRID_COLUMNS = [
    "select", "surveyNo", "transactionYear", "transactionNo",
    "mrNo", "mutationType", "acquisitionType", "approvedDate",
]

WINDOW_OPEN_RE = re.compile(r"window\.open\('([^']+)'")


class MutationWorkflowError(Exception):
    """Raised when the portal responds in a shape this module doesn't
    recognize (e.g. a dropdown/grid/button that should be present after
    a given step isn't there). Caller should treat this like
    SOURCE_PARSE_FAILED / MUTATION_REQUEST_FAILED, not retry blindly."""


def start(session: BhoomiSession):
    """Fresh GET - always the first call of a new workflow."""
    return session.get_page(PATH)


def list_options(soup, level: str) -> list[tuple[str, str]]:
    """Options currently populated for `level`'s dropdown, excluding the
    leading "Select ..." placeholder (value '0')."""
    opts = session_independent_options(soup, DROPDOWN_IDS[level])
    return [(v, t) for v, t in opts if v not in (None, "", "0")]


def session_independent_options(soup, select_id):
    return BhoomiSession.select_options(soup, select_id)


def select_district(session: BhoomiSession, soup, value: str):
    new_soup, _ = session.postback(
        PATH, soup, {DROPDOWN_FIELDS["district"]: value},
        event_target=DROPDOWN_FIELDS["district"],
    )
    return new_soup


def select_taluk(session: BhoomiSession, soup, district_value: str, value: str):
    new_soup, _ = session.postback(
        PATH, soup,
        {DROPDOWN_FIELDS["district"]: district_value, DROPDOWN_FIELDS["taluk"]: value},
        event_target=DROPDOWN_FIELDS["taluk"],
    )
    return new_soup


def select_hobli(session: BhoomiSession, soup, district_value: str, taluk_value: str, value: str):
    new_soup, _ = session.postback(
        PATH, soup,
        {DROPDOWN_FIELDS["district"]: district_value, DROPDOWN_FIELDS["taluk"]: taluk_value,
         DROPDOWN_FIELDS["hobli"]: value},
        event_target=DROPDOWN_FIELDS["hobli"],
    )
    return new_soup


def select_village(session: BhoomiSession, soup, district_value: str, taluk_value: str,
                    hobli_value: str, value: str):
    new_soup, _ = session.postback(
        PATH, soup,
        {DROPDOWN_FIELDS["district"]: district_value, DROPDOWN_FIELDS["taluk"]: taluk_value,
         DROPDOWN_FIELDS["hobli"]: hobli_value, DROPDOWN_FIELDS["village"]: value},
        event_target=DROPDOWN_FIELDS["village"],
    )
    return new_soup


def _selection_fields(district_value, taluk_value, hobli_value, village_value, survey_number=None):
    fields = {
        DROPDOWN_FIELDS["district"]: district_value,
        DROPDOWN_FIELDS["taluk"]: taluk_value,
        DROPDOWN_FIELDS["hobli"]: hobli_value,
        DROPDOWN_FIELDS["village"]: village_value,
    }
    if survey_number is not None:
        fields[SURVEY_FIELD] = str(survey_number)
    return fields


def enter_survey_and_fetch(session: BhoomiSession, soup, district_value: str, taluk_value: str,
                            hobli_value: str, village_value: str, survey_number: str):
    """txtSurvey's onchange fires its own postback, then "Fetch Details"
    is a plain submit - both confirmed live. Returns the soup containing
    the mutation grid."""
    soup, _ = session.postback(
        PATH, soup,
        _selection_fields(district_value, taluk_value, hobli_value, village_value, survey_number),
        event_target=SURVEY_FIELD,
    )
    fields = _selection_fields(district_value, taluk_value, hobli_value, village_value, survey_number)
    fields[FETCH_BUTTON_FIELD] = "Fetch Details"
    soup, _ = session.postback(PATH, soup, fields)  # plain submit - no __EVENTTARGET
    if not soup.find("table", {"id": MUTATION_GRID_ID}):
        raise MutationWorkflowError(
            f"No mutation grid after Fetch Details for survey {survey_number!r} - "
            "portal may have returned an error page or the survey has no records."
        )
    return soup


def parse_mutation_grid(soup) -> list[dict]:
    """Parses the mutation grid into the existing rawMutations shape
    (Sec. 4), unchanged, plus a derived hissaNumber pulled out of the
    "56/*/1"-style surveyNo. Skips the header row."""
    table = soup.find("table", {"id": MUTATION_GRID_ID})
    if not table:
        return []
    rows = table.find_all("tr")[1:]  # first row is the header
    out = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) != len(GRID_COLUMNS):
            log.warning("[MUTATION] grid row has %d cells, expected %d - skipping row, raw=%r",
                        len(cells), len(GRID_COLUMNS), row.get_text(" ", strip=True))
            continue
        vals = {col: c.get_text(" ", strip=True) for col, c in zip(GRID_COLUMNS, cells)}
        survey_no = vals["surveyNo"]
        hissa = survey_no.split("/")[-1] if "/" in survey_no else None
        out.append({
            "surveyNo": survey_no,
            "hissaNumber": hissa,
            "transactionYear": vals["transactionYear"],
            "transactionNo": vals["transactionNo"],
            "mrNo": vals["mrNo"],
            "mutationType": vals["mutationType"],
            "acquisitionType": vals["acquisitionType"],
            "approvedDate": vals["approvedDate"],
        })
    return out


def grid_row_index(soup, mr_no: str, transaction_no: str) -> int | None:
    """Finds the 0-based row index (matching the GridView's Select$N
    postback argument) for a given (mrNo, transactionNo) pair - the
    natural key that's stable regardless of row ordering."""
    table = soup.find("table", {"id": MUTATION_GRID_ID})
    if not table:
        return None
    rows = table.find_all("tr")[1:]
    for i, row in enumerate(rows):
        cells = row.find_all("td")
        if len(cells) != len(GRID_COLUMNS):
            continue
        vals = {col: c.get_text(" ", strip=True) for col, c in zip(GRID_COLUMNS, cells)}
        if vals["mrNo"] == mr_no and vals["transactionNo"] == transaction_no:
            return i
    return None


def select_mutation_row(session: BhoomiSession, soup, district_value: str, taluk_value: str,
                         hobli_value: str, village_value: str, survey_number: str, row_index: int):
    """Fires the GridView's Select$N postback for `row_index`. Returns
    the soup showing that row's detail panel + the "Preview" button."""
    fields = _selection_fields(district_value, taluk_value, hobli_value, village_value, survey_number)
    new_soup, _ = session.postback(
        PATH, soup, fields,
        event_target="ctl00$MainContent$gv_MutationDetails",
        event_argument=f"Select${row_index}",
    )
    if not new_soup.find(id="ctl00_MainContent_btnPreview"):
        raise MutationWorkflowError(
            f"No Preview button after selecting row {row_index} - portal may not have "
            "accepted the selection (stale ViewState / session issue)."
        )
    return new_soup


def trigger_preview(session: BhoomiSession, soup, district_value: str, taluk_value: str,
                     hobli_value: str, village_value: str, survey_number: str) -> str:
    """Clicks "Preview" and extracts the dynamically-generated
    ReportPreview.aspx URL (with whatever Type/MutType/Phodi_Process the
    portal decided for THIS mutation - never assumed). Returns the
    absolute URL; does not fetch it (that's preview_fetcher.py's job, so
    this module stays about MR_MutationExtract.aspx navigation only)."""
    fields = _selection_fields(district_value, taluk_value, hobli_value, village_value, survey_number)
    fields[PREVIEW_BUTTON_FIELD] = "Preview"
    _, resp = session.postback(PATH, soup, fields)
    m = WINDOW_OPEN_RE.search(resp.text)
    if not m:
        raise MutationWorkflowError(
            "Preview button postback did not contain a window.open(...) Preview URL - "
            "portal response shape has changed or the selection was lost."
        )
    return session.base_url + m.group(1)
