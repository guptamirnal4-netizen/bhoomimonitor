"""
atlas_service.py
-----------------
Live, non-persisting lookups for the Atlas portal: Mutation (legal
history) and Loan (financial charges), plus the cascading dropdown
options the search form needs. Everything here is a thin orchestration
layer over the EXISTING crawler/parser modules (mutation_crawler,
preview_fetcher, loan_parser, reconcile, rtc_crawler) - none of them are
modified, and nothing is written to MongoDB.

Honesty rules (same spirit as rtc_verifier.py):
  - status is one of "ok" | "no_records" | "partial" | "error". A portal
    failure is NEVER reported as "no_records", and an empty grid is
    never reported as an error.
  - An empty mutation grid for a numeric survey is the portal's own way
    of saying "nothing recorded" (confirmed live: survey 99999 returns a
    grid with zero rows). A MISSING grid is a failure.
  - Every failure carries an explicit reasonCode.
"""

import logging
import re
import threading
import time
from datetime import datetime, timezone

import loan_parser
import mutation_crawler as mc
import preview_fetcher
import reconcile as reconcile_mod
import rtc_crawler
from bhoomi_session import BhoomiRequestFailed, BhoomiSession, BhoomiSessionExpired
from preview_fetcher import PreviewParseError

log = logging.getLogger("bhoomi")

MUTATION_BASE_URL = "https://landrecords.karnataka.gov.in/Service11/"
RTC_BASE_URL = "https://landrecords.karnataka.gov.in/Service2/"

SURVEY_RE = re.compile(r"^\d{1,6}$")
LEVELS = ("district", "taluk", "hobli", "village", "hissa")

# Courtesy limit: never run more than this many concurrent sessions
# against the government portal from this process.
MAX_CONCURRENT_PORTAL_SESSIONS = 3
_portal_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PORTAL_SESSIONS)


class InvalidInput(ValueError):
    """Caller-supplied value can't be valid (maps to HTTP 422)."""


class PortalBusy(Exception):
    """Too many concurrent portal sessions (maps to HTTP 429)."""


class LocationNotFound(Exception):
    """A dropdown value the caller supplied doesn't exist on the portal."""


def validate_survey(survey_number: str) -> str:
    survey_number = (survey_number or "").strip()
    if not SURVEY_RE.match(survey_number):
        raise InvalidInput("Survey number must be numeric (digits only, up to 6 digits).")
    return survey_number


class _slot:
    def __enter__(self):
        if not _portal_slots.acquire(blocking=False):
            raise PortalBusy("The portal gateway is busy with other lookups. Please retry in a few seconds.")

    def __exit__(self, *exc):
        _portal_slots.release()
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find(options: list[tuple[str, str]], name: str) -> str | None:
    for value, text in options:
        if text.strip().lower() == name.strip().lower():
            return value
    return None


def _error(query: dict, reason_code: str, detail: str, **extra) -> dict:
    return {"success": False, "status": "error", "query": query, "fetchedAt": _now(),
            "error": {"reasonCode": reason_code, "detail": detail}, **extra}


def _classify(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, BhoomiSessionExpired):
        return "SESSION_EXPIRED", str(exc)
    if isinstance(exc, BhoomiRequestFailed):
        return "PORTAL_UNREACHABLE", str(exc)
    if isinstance(exc, LocationNotFound):
        return "LOCATION_NOT_FOUND", str(exc)
    return "SOURCE_PARSE_FAILED", str(exc)


# ---------------------------------------------------------------------------
# Dropdown options
# ---------------------------------------------------------------------------

_OPTIONS_TTL_SECONDS = 6 * 3600
_options_cache: dict[tuple, tuple[float, list[str]]] = {}
_options_lock = threading.Lock()


def _cached_options(key: tuple):
    with _options_lock:
        hit = _options_cache.get(key)
    if hit and time.monotonic() - hit[0] < _OPTIONS_TTL_SECONDS:
        return hit[1]
    return None


def _store_options(key: tuple, values: list[str]):
    with _options_lock:
        _options_cache[key] = (time.monotonic(), values)


def _mutation_cascade_to(session: BhoomiSession, level: str, district=None, taluk=None, hobli=None):
    """Walks Service11's cascade down to `level` and returns the options
    available there. Raises LocationNotFound for an unknown parent."""
    soup = mc.start(session)
    if level == "district":
        return mc.list_options(soup, "district")
    d = _find(mc.list_options(soup, "district"), district or "")
    if not d:
        raise LocationNotFound(f"District {district!r} not found")
    soup = mc.select_district(session, soup, d)
    if level == "taluk":
        return mc.list_options(soup, "taluk")
    t = _find(mc.list_options(soup, "taluk"), taluk or "")
    if not t:
        raise LocationNotFound(f"Taluk {taluk!r} not found under {district!r}")
    soup = mc.select_taluk(session, soup, d, t)
    if level == "hobli":
        return mc.list_options(soup, "hobli")
    h = _find(mc.list_options(soup, "hobli"), hobli or "")
    if not h:
        raise LocationNotFound(f"Hobli {hobli!r} not found under {district!r}/{taluk!r}")
    soup = mc.select_hobli(session, soup, d, t, h)
    return mc.list_options(soup, "village")


def list_options(level: str, district=None, taluk=None, hobli=None, village=None, survey_number=None) -> dict:
    if level not in LEVELS:
        raise InvalidInput(f"level must be one of {', '.join(LEVELS)}")
    required = {"district": [], "taluk": ["district"], "hobli": ["district", "taluk"],
                "village": ["district", "taluk", "hobli"],
                "hissa": ["district", "taluk", "hobli", "village", "survey_number"]}[level]
    given = {"district": district, "taluk": taluk, "hobli": hobli, "village": village,
             "survey_number": survey_number}
    missing = [p for p in required if not given[p]]
    if missing:
        raise InvalidInput(f"level={level} requires: {', '.join(missing)}")
    if level == "hissa":
        validate_survey(survey_number)

    key = (level, district, taluk, hobli, village, survey_number)
    cached = _cached_options(key) if level != "hissa" else None
    if cached is not None:
        return {"level": level, "options": cached, "cached": True, "fetchedAt": _now()}

    with _slot():
        if level == "hissa":
            session = BhoomiSession(RTC_BASE_URL)
            soup = rtc_crawler.start(session)
            d = _find(rtc_crawler.list_options(soup, "district"), district)
            if not d:
                raise LocationNotFound(f"District {district!r} not found")
            soup = rtc_crawler.select_district(session, soup, d)
            t = _find(rtc_crawler.list_options(soup, "taluk"), taluk)
            if not t:
                raise LocationNotFound(f"Taluk {taluk!r} not found under {district!r}")
            soup = rtc_crawler.select_taluk(session, soup, d, t)
            h = _find(rtc_crawler.list_options(soup, "hobli"), hobli)
            if not h:
                raise LocationNotFound(f"Hobli {hobli!r} not found")
            soup = rtc_crawler.select_hobli(session, soup, d, t, h)
            v = _find(rtc_crawler.list_options(soup, "village"), village)
            if not v:
                raise LocationNotFound(f"Village {village!r} not found")
            soup = rtc_crawler.select_village(session, soup, d, t, h, v)
            soup = rtc_crawler.enter_survey_and_go(session, soup, d, t, h, v, survey_number)
            surnoc_opts = rtc_crawler.list_options(soup, "surnoc")
            if not surnoc_opts:
                return {"level": level, "options": [], "cached": False, "fetchedAt": _now()}
            surnoc_soup = rtc_crawler.select_surnoc(session, soup, d, t, h, v, survey_number, surnoc_opts[0][0])
            values = [text for _, text in rtc_crawler.list_options(surnoc_soup, "hissa")]
            return {"level": level, "options": values, "cached": False, "fetchedAt": _now()}

        session = BhoomiSession(MUTATION_BASE_URL)
        values = [text for _, text in _mutation_cascade_to(session, level, district, taluk, hobli)]
    _store_options(key, values)
    return {"level": level, "options": values, "cached": False, "fetchedAt": _now()}


# ---------------------------------------------------------------------------
# Mutation grid (shared by /api/mutation and /api/loan)
# ---------------------------------------------------------------------------

def _open_mutation_grid(session: BhoomiSession, district, taluk, hobli, village, survey_number):
    """Returns (soup_with_grid, d_val, t_val, h_val, v_val)."""
    soup = mc.start(session)
    d = _find(mc.list_options(soup, "district"), district)
    if not d:
        raise LocationNotFound(f"District {district!r} not found in the portal's list")
    soup = mc.select_district(session, soup, d)
    t = _find(mc.list_options(soup, "taluk"), taluk)
    if not t:
        raise LocationNotFound(f"Taluk {taluk!r} not found under {district!r}")
    soup = mc.select_taluk(session, soup, d, t)
    h = _find(mc.list_options(soup, "hobli"), hobli)
    if not h:
        raise LocationNotFound(f"Hobli {hobli!r} not found under {district!r}/{taluk!r}")
    soup = mc.select_hobli(session, soup, d, t, h)
    v = _find(mc.list_options(soup, "village"), village)
    if not v:
        raise LocationNotFound(f"Village {village!r} not found under {district!r}/{taluk!r}/{hobli!r}")
    soup = mc.select_village(session, soup, d, t, h, v)
    soup = mc.enter_survey_and_fetch(session, soup, d, t, h, v, survey_number)
    return soup, d, t, h, v


def _parse_date(raw: str | None):
    try:
        return datetime.strptime((raw or "").strip(), "%d/%m/%Y")
    except ValueError:
        return None


def _decorate(row: dict) -> dict:
    dt = _parse_date(row.get("approvedDate"))
    return {**row, "approvedDateIso": dt.date().isoformat() if dt else None}


def _in_range(row: dict, from_year: int | None, to_year: int | None) -> bool:
    """Rows without a parseable approved date are KEPT (never silently
    dropped by a year filter) - the caller sees approvedDateIso=None."""
    if from_year is None and to_year is None:
        return True
    dt = _parse_date(row.get("approvedDate"))
    if dt is None:
        return True
    if from_year is not None and dt.year < from_year:
        return False
    if to_year is not None and dt.year > to_year:
        return False
    return True


def _run_with_one_retry(fn):
    """A dead ASP.NET session is retried exactly once on a fresh session."""
    try:
        return fn()
    except BhoomiSessionExpired:
        log.warning("[ATLAS] session expired - retrying once on a fresh session")
        return fn()


# ---------------------------------------------------------------------------
# Mutation (legal history)
# ---------------------------------------------------------------------------

def fetch_mutation_history(district, taluk, hobli, village, survey_number, hissa_no=None,
                           from_year=None, to_year=None) -> dict:
    survey_number = validate_survey(survey_number)
    if from_year is not None and to_year is not None and from_year > to_year:
        raise InvalidInput("from_year cannot be after to_year")
    query = {"district": district, "taluk": taluk, "hobli": hobli, "village": village,
             "survey_number": survey_number, "hissa_no": hissa_no,
             "from_year": from_year, "to_year": to_year}

    def run():
        session = BhoomiSession(MUTATION_BASE_URL)
        soup, *_ = _open_mutation_grid(session, district, taluk, hobli, village, survey_number)
        return mc.parse_mutation_grid(soup)

    try:
        with _slot():
            raw_rows = _run_with_one_retry(run)
    except PortalBusy:
        raise
    except (BhoomiSessionExpired, BhoomiRequestFailed, LocationNotFound, mc.MutationWorkflowError) as exc:
        code, detail = _classify(exc)
        return _error(query, code, detail, count=0, records=[])

    total_on_portal = len(raw_rows)
    rows = [r for r in raw_rows
            if (hissa_no is None or (r["hissaNumber"] or "").strip().lower() == hissa_no.strip().lower())
            and _in_range(r, from_year, to_year)]
    records = [_decorate(r) for r in rows]
    records.sort(key=lambda r: (r["approvedDateIso"] is None, r["approvedDateIso"] or ""))

    return {
        "success": True,
        "status": "ok" if records else "no_records",
        "query": query,
        "totalOnPortalForSurvey": total_on_portal,
        "count": len(records),
        "records": records,
        "error": None,
        "fetchedAt": _now(),
    }


# ---------------------------------------------------------------------------
# Loan (financial charges)
# ---------------------------------------------------------------------------

def fetch_loan_history(district, taluk, hobli, village, survey_number, hissa_no) -> dict:
    """hissa_no is required: every mutation row costs three portal
    round-trips (Select, Preview, Preview GET), so an unbounded
    "every hissa" crawl isn't appropriate for a synchronous request."""
    survey_number = validate_survey(survey_number)
    if not (hissa_no or "").strip():
        raise InvalidInput("hissa_no is required for a loan lookup")
    hissa_no = hissa_no.strip()
    query = {"district": district, "taluk": taluk, "hobli": hobli, "village": village,
             "survey_number": survey_number, "hissa_no": hissa_no}

    failures: list[dict] = []
    stats = {"previewsOk": 0, "previewsFailed": 0, "mutationsChecked": 0}

    def run():
        failures.clear()
        stats.update(previewsOk=0, previewsFailed=0, mutationsChecked=0)
        session = BhoomiSession(MUTATION_BASE_URL)
        soup, d, t, h, v = _open_mutation_grid(session, district, taluk, hobli, village, survey_number)
        rows = [r for r in mc.parse_mutation_grid(soup)
                if (r["hissaNumber"] or "").strip().lower() == hissa_no.lower()]
        events = []
        for row in rows:
            stats["mutationsChecked"] += 1
            idx = mc.grid_row_index(soup, row["mrNo"], row["transactionNo"])
            if idx is None:
                stats["previewsFailed"] += 1
                failures.append({"mrNo": row["mrNo"], "reasonCode": "SOURCE_PARSE_FAILED",
                                 "detail": "row not found for Select lookup"})
                continue
            try:
                sel = mc.select_mutation_row(session, soup, d, t, h, v, survey_number, idx)
                url = mc.trigger_preview(session, sel, d, t, h, v, survey_number)
                preview = preview_fetcher.fetch_preview(session, url, referer=MUTATION_BASE_URL + mc.PATH)
                event = loan_parser.build_loan_event(row, preview)
                stats["previewsOk"] += 1
                if event:
                    events.append(event)
            except BhoomiSessionExpired:
                raise
            except (BhoomiRequestFailed, mc.MutationWorkflowError, PreviewParseError) as exc:
                stats["previewsFailed"] += 1
                code = "SOURCE_PARSE_FAILED" if isinstance(exc, (PreviewParseError, mc.MutationWorkflowError)) \
                    else "PORTAL_UNREACHABLE"
                failures.append({"mrNo": row["mrNo"], "reasonCode": code, "detail": str(exc)})
        return rows, events

    try:
        with _slot():
            rows, events = _run_with_one_retry(run)
    except PortalBusy:
        raise
    except (BhoomiSessionExpired, BhoomiRequestFailed, LocationNotFound, mc.MutationWorkflowError) as exc:
        code, detail = _classify(exc)
        return _error(query, code, detail, loanHistory=[], mutationsChecked=0)

    history = reconcile_mod.reconcile(events)
    if stats["previewsFailed"] and not history:
        status = "error"
    elif stats["previewsFailed"]:
        status = "partial"
    elif history:
        status = "ok"
    else:
        status = "no_records"

    return {
        "success": status in ("ok", "partial", "no_records"),
        "status": status,
        "query": query,
        "mutationsChecked": stats["mutationsChecked"],
        "previewsOk": stats["previewsOk"],
        "previewsFailed": stats["previewsFailed"],
        "failures": failures,
        "loanHistory": history,
        "error": ({"reasonCode": failures[0]["reasonCode"],
                   "detail": "Every mutation preview failed - charges could not be determined."}
                  if status == "error" else None),
        "fetchedAt": _now(),
    }
