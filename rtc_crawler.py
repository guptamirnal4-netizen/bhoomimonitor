"""
rtc_crawler.py
---------------
Drives the RTC form on https://landrecords.karnataka.gov.in/Service2/ end
to end: district -> taluk -> hobli -> village -> survey number -> "Go"
-> Surnoc -> Hissa -> Period -> Year -> "Fetch details".

Confirmed live (no CAPTCHA anywhere in this flow, unlike
rtc.karnataka.gov.in/service78/ - see rtc_verifier.py's history): this is
a classic ASP.NET WebForms cascade, same mechanics as
MR_MutationExtract.aspx (mutation_crawler.py) but a distinct set of
control IDs (ddlCDistrict/ddlCTaluk/ddlCHobli/ddlCVillage/txtCSurveyNo/
ddlCSurnocNo/ddlCHissaNo/ddlCPeriod/ddlCYear/btnCGo/btnCFetchDetails), so
it reuses bhoomi_session.BhoomiSession rather than duplicating the
postback engine.

One real quirk observed live and worth flagging: on a couple of repeat
attempts (heavy request volume in one sitting), the Surnoc/Hissa
dropdowns came back empty on "Go" even with an identical, previously-
successful payload - the portal appears to throttle/degrade under
sustained load rather than erroring cleanly. Treat an empty Surnoc/Hissa
list after "Go" as a retryable condition (RTC_REQUEST_FAILED), not
proof the survey has no RTC data - don't conclude "no RTC record" from
a single empty cascade response.
"""

import logging

from bhoomi_session import BhoomiSession

log = logging.getLogger("bhoomi")

PATH = ""  # Service2/'s form posts back to "./" (the root itself)
P = "ctl00$MainContent$"

DROPDOWN_IDS = {
    "district": "ctl00_MainContent_ddlCDistrict",
    "taluk": "ctl00_MainContent_ddlCTaluk",
    "hobli": "ctl00_MainContent_ddlCHobli",
    "village": "ctl00_MainContent_ddlCVillage",
    "surnoc": "ctl00_MainContent_ddlCSurnocNo",
    "hissa": "ctl00_MainContent_ddlCHissaNo",
    "period": "ctl00_MainContent_ddlCPeriod",
    "year": "ctl00_MainContent_ddlCYear",
}
DROPDOWN_FIELDS = {
    "district": P + "ddlCDistrict", "taluk": P + "ddlCTaluk",
    "hobli": P + "ddlCHobli", "village": P + "ddlCVillage",
    "surnoc": P + "ddlCSurnocNo", "hissa": P + "ddlCHissaNo",
    "period": P + "ddlCPeriod", "year": P + "ddlCYear",
}
SURVEY_FIELD = P + "txtCSurveyNo"
GO_BUTTON_FIELD = P + "btnCGo"
FETCH_BUTTON_FIELD = P + "btnCFetchDetails"

PLACEHOLDER_VALUES = {"", "0", "Select District", "Select Taluk", "Select Hobli", "Select Village",
                      "Select Surnoc", "Select Hissa", "Select Period", "Select Year"}


class RtcWorkflowError(Exception):
    """The portal responded in a shape this module doesn't recognize, or
    a cascade step came back empty when it shouldn't have (Sec. 13/36:
    surface it, don't guess past it)."""


def start(session: BhoomiSession):
    return session.get_page(PATH)


def list_options(soup, level: str) -> list[tuple[str, str]]:
    opts = BhoomiSession.select_options(soup, DROPDOWN_IDS[level])
    return [(v, t) for v, t in opts if v not in PLACEHOLDER_VALUES and t not in PLACEHOLDER_VALUES]


def _base_fields(district_value, taluk_value=None, hobli_value=None, village_value=None,
                  survey_number=None, surnoc_value=None, hissa_value=None, period_value=None,
                  year_value=None):
    fields = {DROPDOWN_FIELDS["district"]: district_value}
    for key, val in (("taluk", taluk_value), ("hobli", hobli_value), ("village", village_value),
                      ("surnoc", surnoc_value), ("hissa", hissa_value), ("period", period_value),
                      ("year", year_value)):
        if val is not None:
            fields[DROPDOWN_FIELDS[key]] = val
    if survey_number is not None:
        fields[SURVEY_FIELD] = str(survey_number)
    return fields


def select_district(session, soup, value):
    new_soup, _ = session.postback(PATH, soup, {DROPDOWN_FIELDS["district"]: value},
                                    event_target=DROPDOWN_FIELDS["district"])
    return new_soup


def select_taluk(session, soup, district_value, value):
    new_soup, _ = session.postback(PATH, soup, _base_fields(district_value, taluk_value=value),
                                    event_target=DROPDOWN_FIELDS["taluk"])
    return new_soup


def select_hobli(session, soup, district_value, taluk_value, value):
    new_soup, _ = session.postback(
        PATH, soup, _base_fields(district_value, taluk_value, hobli_value=value),
        event_target=DROPDOWN_FIELDS["hobli"])
    return new_soup


def select_village(session, soup, district_value, taluk_value, hobli_value, value):
    new_soup, _ = session.postback(
        PATH, soup, _base_fields(district_value, taluk_value, hobli_value, village_value=value),
        event_target=DROPDOWN_FIELDS["village"])
    return new_soup


def enter_survey_and_go(session, soup, district_value, taluk_value, hobli_value, village_value, survey_number):
    """txtCSurveyNo's onchange fires its own postback, then "Go" is a
    plain submit button (mirrors mutation_crawler's Fetch Details)."""
    fields = _base_fields(district_value, taluk_value, hobli_value, village_value, survey_number)
    soup, _ = session.postback(PATH, soup, fields, event_target=SURVEY_FIELD)
    fields = _base_fields(district_value, taluk_value, hobli_value, village_value, survey_number)
    fields[GO_BUTTON_FIELD] = "Go"
    soup, _ = session.postback(PATH, soup, fields)
    return soup


def select_surnoc(session, soup, district_value, taluk_value, hobli_value, village_value,
                   survey_number, surnoc_value):
    fields = _base_fields(district_value, taluk_value, hobli_value, village_value, survey_number,
                           surnoc_value=surnoc_value)
    new_soup, _ = session.postback(PATH, soup, fields, event_target=DROPDOWN_FIELDS["surnoc"])
    return new_soup


def select_hissa(session, soup, district_value, taluk_value, hobli_value, village_value,
                  survey_number, surnoc_value, hissa_value):
    fields = _base_fields(district_value, taluk_value, hobli_value, village_value, survey_number,
                           surnoc_value, hissa_value)
    new_soup, _ = session.postback(PATH, soup, fields, event_target=DROPDOWN_FIELDS["hissa"])
    return new_soup


def select_period(session, soup, district_value, taluk_value, hobli_value, village_value,
                   survey_number, surnoc_value, hissa_value, period_value):
    fields = _base_fields(district_value, taluk_value, hobli_value, village_value, survey_number,
                           surnoc_value, hissa_value, period_value)
    new_soup, _ = session.postback(PATH, soup, fields, event_target=DROPDOWN_FIELDS["period"])
    return new_soup


def fetch_details(session, soup, district_value, taluk_value, hobli_value, village_value,
                   survey_number, surnoc_value, hissa_value, period_value, year_value=None):
    """Clicks "Fetch details" - a plain submit button. Returns the
    response HTML text (rtc_parser.py parses it), not just the soup, so
    callers/tests can keep the raw source (Sec. 8)."""
    fields = _base_fields(district_value, taluk_value, hobli_value, village_value, survey_number,
                           surnoc_value, hissa_value, period_value, year_value)
    fields[FETCH_BUTTON_FIELD] = "Fetch details"
    _, resp = session.postback(PATH, soup, fields)
    return resp.text


def _fetch_one_hissa(session, d_val, t_val, h_val, v_val, survey_number, surnoc_val, surnoc_soup,
                      hissa_val, hissa_text):
    """Select this one hissa -> Period -> Year -> Fetch details. Raises
    RtcWorkflowError on a genuine cascade break for THIS hissa - caller
    decides whether that aborts everything or is isolated to this hissa
    (Sec. 36: one bad hissa must not take down the others)."""
    soup = select_hissa(session, surnoc_soup, d_val, t_val, h_val, v_val, survey_number, surnoc_val, hissa_val)

    period_opts = list_options(soup, "period")
    if not period_opts:
        raise RtcWorkflowError(f"No Period options for Hissa {hissa_text!r}, survey {survey_number!r}")
    till_date_opts = [v for v, t in period_opts if "till date" in t.lower()]
    period_val = till_date_opts[0] if till_date_opts else period_opts[0][0]
    soup = select_period(session, soup, d_val, t_val, h_val, v_val, survey_number, surnoc_val,
                          hissa_val, period_val)

    year_opts = list_options(soup, "year")
    year_val = year_opts[0][0] if year_opts else None

    return fetch_details(session, soup, d_val, t_val, h_val, v_val, survey_number, surnoc_val,
                          hissa_val, period_val, year_val)


def fetch_current_rtc(session: BhoomiSession, district: str, taluk: str, hobli: str, village: str,
                       survey_number: str, hissa_no: str | None = None) -> list[dict]:
    """Full workflow: Surnoc defaults to its first (usually only, "*")
    option. Hissa selection:

      - hissa_no given: matched against the Hissa dropdown TEXT ("1",
        "2", ...) exactly - if it's not found, that's a real error
        (RtcWorkflowError), never a silent fallback to some other hissa.
      - hissa_no is None: EVERY hissa under this survey is fetched, one
        "Fetch details" postback per hissa (Sec. 36: a failure on one
        hissa is isolated and doesn't abort the others).

    Always returns a list of dicts, even for a single requested hissa
    (consistent shape for callers/API consumers - never "sometimes an
    object, sometimes a list"):
        [{"hissa_no": "1", "html": "<...>", "error": None}, ...]
    `html` is None and `error` is set when that specific hissa's fetch
    failed. Raises RtcWorkflowError only for a failure common to ALL
    hissas (district/taluk/hobli/village/survey/surnoc not found, or the
    requested hissa_no doesn't exist at all)."""
    soup = start(session)

    d_val = _find(list_options(soup, "district"), district)
    if not d_val:
        raise RtcWorkflowError(f"District {district!r} not found in RTC dropdown")
    soup = select_district(session, soup, d_val)

    t_val = _find(list_options(soup, "taluk"), taluk)
    if not t_val:
        raise RtcWorkflowError(f"Taluk {taluk!r} not found under {district!r}")
    soup = select_taluk(session, soup, d_val, t_val)

    h_val = _find(list_options(soup, "hobli"), hobli)
    if not h_val:
        raise RtcWorkflowError(f"Hobli {hobli!r} not found under {district!r}/{taluk!r}")
    soup = select_hobli(session, soup, d_val, t_val, h_val)

    v_val = _find(list_options(soup, "village"), village)
    if not v_val:
        raise RtcWorkflowError(f"Village {village!r} not found under {district!r}/{taluk!r}/{hobli!r}")
    soup = select_village(session, soup, d_val, t_val, h_val, v_val)

    soup = enter_survey_and_go(session, soup, d_val, t_val, h_val, v_val, survey_number)

    surnoc_opts = list_options(soup, "surnoc")
    if not surnoc_opts:
        raise RtcWorkflowError(
            f"No Surnoc options after Go for survey {survey_number!r} - either no RTC record exists "
            "here, or the portal degraded under load (retry before concluding 'no record')."
        )
    surnoc_val = surnoc_opts[0][0]
    surnoc_soup = select_surnoc(session, soup, d_val, t_val, h_val, v_val, survey_number, surnoc_val)

    hissa_opts = list_options(surnoc_soup, "hissa")
    if not hissa_opts:
        raise RtcWorkflowError(f"No Hissa options after selecting Surnoc for survey {survey_number!r}")

    if hissa_no is not None:
        match = next(((v, t) for v, t in hissa_opts if t.strip().lower() == hissa_no.strip().lower()), None)
        if match is None:
            raise RtcWorkflowError(
                f"Hissa {hissa_no!r} not found among options {hissa_opts!r} for survey {survey_number!r}"
            )
        targets = [match]
    else:
        targets = hissa_opts

    results = []
    for hissa_val, hissa_text in targets:
        try:
            html = _fetch_one_hissa(session, d_val, t_val, h_val, v_val, survey_number, surnoc_val,
                                     surnoc_soup, hissa_val, hissa_text)
            results.append({"hissa_no": hissa_text, "html": html, "error": None})
        except RtcWorkflowError as exc:
            log.warning("[RTC] Hissa %r failed for survey %r: %s", hissa_text, survey_number, exc)
            results.append({"hissa_no": hissa_text, "html": None, "error": str(exc)})
    return results


def _find(options, name):
    for v, t in options:
        if t.strip().lower() == (name or "").strip().lower():
            return v
    return None
