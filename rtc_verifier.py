"""
rtc_verifier.py
-----------------
Current-status verification, backed by the RTC form on
https://landrecords.karnataka.gov.in/Service2/.

HISTORY: earlier live inspection found rtc.karnataka.gov.in/service78/
("Click here to get the original digitally signed RTC and MR") gated
behind a CAPTCHA, and this module was a stub as a result. You then
pointed out that the Service2/ page itself has a "Fetch details" flow
(District/Taluk/Hobli/Village/Survey -> "Go" -> Surnoc/Hissa/Period/Year
-> "Fetch details") that returns the current RTC with NO CAPTCHA -
confirmed live (rtc_crawler.py/rtc_parser.py). This module now performs
that fetch for real.

What this DOES return (Sec. 29 - directly observed or deterministically
parsed): land_id, ongoing_mutation, pyki, the owner grid (name/extent/
owner_category/gov_restriction/court_stay/alienated), the Blockchain-
verification banner, and the portal's own echoed village/survey/surnoc/
hissa/period/year.

What this DOES NOT compute (Sec. 7/29 - zero assumption): a
"hasActiveLoan" boolean. The owner grid observed live has no direct
loan/charge column - Gov Restriction and Court stay are distinct
concepts from a mortgage/bhogya charge. Deriving hasActiveLoan from
those would be an invented association. If loan_history (from the
Mutation/Preview pipeline) is passed in, this module cross-references it
ONLY to report whether there's an unreleased charge_creation on record -
labeled explicitly as an inference from mutation history, not as
something this RTC page itself states, and always verificationRequired.
"""

from datetime import datetime, timezone

from bhoomi_session import BhoomiSession, BhoomiRequestFailed, BhoomiSessionExpired
import rtc_crawler
import rtc_parser

BASE_URL = "https://landrecords.karnataka.gov.in/Service2/"


def _infer_active_charge(loan_history: list[dict] | None) -> dict:
    """Best-available inference from mutation history alone (Sec. 11/12)
    - NEVER stated as fact, always flagged for verification, because
    mutation history crawled so far may be incomplete (a release could
    exist in a mutation this crawl hasn't reached yet).

    `basis` distinguishes "nobody asked for this cross-reference"
    (loan_history=None - the normal case for a standalone GET /api/rtc
    call with no mutation crawl behind it - NOT a failure of anything)
    from "the mutation history was checked and is simply empty"
    (loan_history=[] - a real, if weak, negative signal). Neither of
    those is an RTC failure, so reason_codes.py's RTC_NOT_FETCHED does
    NOT apply here - that code means the RTC page itself couldn't be
    fetched, which by the time this function runs already succeeded.

    Deliberately has no `reasonCode` key: this is a nested confidence
    note on ONE inferred field, always uncertain by construction, not a
    top-level record-verification outcome - reusing reason_codes.py's
    closed vocabulary here (which treats "NONE" as meaning "nothing to
    flag", incompatible with verificationRequired=True) would misstate
    what this field is. `note` is free text instead."""
    if loan_history is None:
        return {
            "hasActiveLoanInferred": None,
            "basis": "NO_LOAN_HISTORY_PROVIDED",
            "verificationRequired": True,
            "note": "No mutation-derived loan_history was passed to this call - this is normal for a "
                     "standalone RTC lookup with no mutation crawl behind it, not a failure.",
        }
    if not loan_history:
        return {
            "hasActiveLoanInferred": None,
            "basis": "MUTATION_HISTORY_CHECKED_BUT_EMPTY",
            "verificationRequired": True,
            "note": "loan_history was provided but contains no events - a weak negative signal, "
                     "not proof there is no charge (the mutation crawl itself may be incomplete).",
        }
    unreleased = [
        e for e in loan_history
        if e.get("instrumentClass") == "charge_creation" and e.get("releaseStatus") == "unreleased_as_of_crawl"
    ]
    return {
        "hasActiveLoanInferred": bool(unreleased),
        "basis": "UNRELEASED_CHARGE_CREATION_IN_CRAWLED_MUTATION_HISTORY" if unreleased
                 else "NO_UNRELEASED_CHARGE_IN_CRAWLED_MUTATION_HISTORY",
        "unreleasedMrNos": [e["mrNo"] for e in unreleased],
        "verificationRequired": True,  # always - this is an inference, never the RTC's own statement
        "note": "Derived from mutation history reconciliation (reconcile.py), not stated by the RTC page itself.",
    }


def _failure_result(hissa_label, reason_code, detail):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "hissa_no": hissa_label,
        "currentStatus": None,
        "statusVerification": {
            "verificationRequired": True, "reasonCode": reason_code,
            "checkedAt": now, "detail": detail,
        },
    }


def _build_success_result(parsed: dict, requested_hissa_no: str | None, loan_history) -> dict:
    """`requested_hissa_no` is always what rtc_crawler actually selected
    for THIS fetch (either the caller's explicit hissa_no, or the
    specific option rtc_crawler iterated to when it was omitted) - it is
    ALWAYS the authoritative label for this result entry, so a caller
    iterating a multi-hissa `results` list can reliably tell which entry
    is which. `matched_hissa` (what the portal's own Details panel
    echoes back) is used ONLY to detect a mismatch worth flagging -
    never to override the label, since doing so would silently
    relabel/misalign entries if the portal's echo were ever wrong."""
    matched_hissa = parsed["details"].get("hissa_no")
    hissa_mismatch = (requested_hissa_no is not None and matched_hissa is not None
                       and str(matched_hissa) != str(requested_hissa_no))
    now = datetime.now(timezone.utc).isoformat()

    current_status = {
        "land_id": parsed["land_id"],
        "ongoing_mutation": parsed["ongoing_mutation"],
        "pyki": parsed["pyki"],
        "owners": parsed["owners"],
        "verification": parsed["verification"],
        "details": parsed["details"],
        "activeChargeInference": _infer_active_charge(loan_history),
        "sourceUrl": parsed["sourceUrl"],
        "fetchedAt": now,
    }
    return {
        "hissa_no": requested_hissa_no,
        "currentStatus": current_status,
        "statusVerification": {
            "verificationRequired": hissa_mismatch,
            "reasonCode": "COLUMN_ALIGNMENT_AMBIGUOUS" if hissa_mismatch else "NONE",
            "checkedAt": now,
            "detail": (
                f"Requested Hissa {requested_hissa_no!r} but portal echoed Hissa {matched_hissa!r} - "
                "verify before trusting this record." if hissa_mismatch else None
            ),
        },
    }


def verify_current_status(district: str, taluk: str, hobli: str, village: str,
                           survey_number: str, hissa_no: str | None = None,
                           loan_history: list[dict] | None = None,
                           session: BhoomiSession | None = None) -> list[dict]:
    """Performs a live RTC fetch. Returns a LIST of per-hissa results,
    always - one item if `hissa_no` was given (matching that hissa, or
    a single failure entry if the whole lookup failed), one item per
    hissa under the survey if `hissa_no` is omitted (Sec. per your
    instruction: don't guess which hissa was meant - show all of them).

    Each item: {"hissa_no": ..., "currentStatus": dict|None,
    "statusVerification": {...}}. currentStatus is None only when that
    specific hissa's fetch/parse genuinely failed - statusVerification
    always carries verificationRequired=True with an explicit reasonCode
    in that case (Sec. 12/13).
    """
    own_session = session is None
    if own_session:
        session = BhoomiSession(BASE_URL)

    try:
        raw_results = rtc_crawler.fetch_current_rtc(session, district, taluk, hobli, village,
                                                      survey_number, hissa_no)
    except BhoomiSessionExpired as exc:
        return [_failure_result(hissa_no, "SESSION_EXPIRED", str(exc))]
    except BhoomiRequestFailed as exc:
        return [_failure_result(hissa_no, "RTC_REQUEST_FAILED", str(exc))]
    except rtc_crawler.RtcWorkflowError as exc:
        return [_failure_result(hissa_no, "SOURCE_PARSE_FAILED", str(exc))]

    results = []
    for raw in raw_results:
        if raw["html"] is None:
            results.append(_failure_result(raw["hissa_no"], "SOURCE_PARSE_FAILED", raw["error"]))
            continue
        try:
            parsed = rtc_parser.parse_rtc_html(raw["html"], source_url=BASE_URL)
        except rtc_parser.RtcParseError as exc:
            results.append(_failure_result(raw["hissa_no"], "SOURCE_PARSE_FAILED", str(exc)))
            continue
        results.append(_build_success_result(parsed, raw["hissa_no"], loan_history))
    return results
