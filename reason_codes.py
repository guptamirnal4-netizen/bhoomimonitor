"""
reason_codes.py
----------------
Closed vocabulary of verification reason codes for the loan/charge
extraction pipeline. Mirrors the discipline schema.py already applies to
ERROR_TYPES: every code must be added here deliberately, so a record that
"needs verification" always says exactly why instead of falling into a
silent "unknown" bucket.

These are a SEPARATE namespace from schema.py's ERROR_TYPES. ERROR_TYPES
describes why an *HTTP fetch attempt* failed (for the fetch_events
dashboard). REASON_CODES describes why a *loan/charge extraction* is
uncertain, even when the underlying HTTP fetch succeeded with a 200 - the
distinction the brief calls "HTTP success vs DATA extraction success".
"""

REASON_CODES = [
    "NONE",                          # fully resolved, nothing to flag
    "RTC_NOT_FETCHED",                # current-status check needs RTC; RTC wasn't fetched (e.g. CAPTCHA-gated, not yet wired)
    "LENDER_NOT_STATED_IN_RECORD",    # an amount is present but no bank/lender text accompanies it anywhere in the source
    "MULTIPLE_CHARGES_IN_LEDGER",     # more than one charge line found in one MR - each preserved individually, flagged for review
    "COLUMN_ALIGNMENT_AMBIGUOUS",     # multiple banks and/or amounts present but the source does not unambiguously pair them
    "CHARGE_UNRELEASED_AT_ACQUISITION",  # a charge_creation event has no matching release anywhere in this record's history
    "RELEASE_MATCH_AMBIGUOUS",        # a release event exists but more than one prior charge could plausibly match it
    "CHARGE_RELEASE_NOT_MATCHED",     # a release event exists but no MR reference in the source ties it to a specific prior charge
    "SOURCE_PARSE_FAILED",            # the Preview HTML did not match any known table shape - raw HTML preserved, nothing inferred
    "SESSION_EXPIRED",                # ASP.NET session/ViewState died mid-workflow; operation must be retried with a fresh session
    "PREVIEW_REQUEST_FAILED",         # the Preview page request itself failed (timeout/5xx/connection error) after retries
    "MUTATION_REQUEST_FAILED",        # the Fetch-Details / mutation-grid request failed after retries
    "RTC_REQUEST_FAILED",             # the RTC request failed after retries (distinct from RTC_NOT_FETCHED = never attempted)
    "UNKNOWN",                        # a real failure occurred that doesn't fit any code above - never used to hide a code we should add instead
]

# Codes that MUST also set verificationRequired=True wherever they're attached.
# ("NONE" is the only code compatible with verificationRequired=False.)
VERIFICATION_REQUIRED_CODES = set(REASON_CODES) - {"NONE"}


def requires_verification(reason_code: str) -> bool:
    if reason_code not in REASON_CODES:
        raise ValueError(f"Unknown reason code: {reason_code!r}")
    return reason_code in VERIFICATION_REQUIRED_CODES
