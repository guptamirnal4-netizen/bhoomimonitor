"""
rtc_verifier.py performs a REAL fetch by default (rtc_crawler.py /
rtc_parser.py, confirmed live, no CAPTCHA) - so unit tests mock the
network layer (rtc_crawler.fetch_current_rtc) rather than let it run,
per Sec. 31 ("no live Bhoomi requests in unit tests"), while still
exercising the real parser against real fixture HTML.

verify_current_status ALWAYS returns a list now (one entry per hissa
fetched) - never a bare tuple - per your instruction: a specific
hissa_no returns exactly that one hissa; omitting it returns every
hissa under the survey, never a silent guess.
"""

import bhoomi_session
import rtc_crawler
import rtc_verifier
from conftest import load_fixture


def test_specific_hissa_returns_single_item_list(monkeypatch):
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    monkeypatch.setattr(
        rtc_crawler, "fetch_current_rtc",
        lambda *a, **k: [{"hissa_no": "1", "html": html, "error": None}],
    )

    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", "1",
    )

    assert len(results) == 1
    r = results[0]
    assert r["hissa_no"] == "1"
    assert r["currentStatus"]["land_id"] == "5986 1500 1025"
    assert r["currentStatus"]["ongoing_mutation"] == "No"
    assert r["statusVerification"]["verificationRequired"] is False
    assert r["statusVerification"]["reasonCode"] == "NONE"


def test_omitted_hissa_returns_every_hissa_not_a_guess(monkeypatch):
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    monkeypatch.setattr(
        rtc_crawler, "fetch_current_rtc",
        lambda *a, **k: [
            {"hissa_no": "1", "html": html, "error": None},
            {"hissa_no": "2", "html": html, "error": None},
            {"hissa_no": "3", "html": html, "error": None},
        ],
    )

    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56",  # hissa_no omitted
    )
    assert [r["hissa_no"] for r in results] == ["1", "2", "3"]
    assert all(r["currentStatus"] is not None for r in results)


def test_one_hissa_failing_does_not_break_the_others(monkeypatch):
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    monkeypatch.setattr(
        rtc_crawler, "fetch_current_rtc",
        lambda *a, **k: [
            {"hissa_no": "1", "html": html, "error": None},
            {"hissa_no": "2", "html": None, "error": "No Period options for Hissa '2'"},
        ],
    )
    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56",
    )
    assert results[0]["currentStatus"] is not None
    assert results[1]["currentStatus"] is None
    assert results[1]["statusVerification"]["reasonCode"] == "SOURCE_PARSE_FAILED"
    assert results[1]["hissa_no"] == "2"


def test_hissa_mismatch_flagged_not_silently_trusted(monkeypatch):
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")  # echoes hissa_no "1"
    monkeypatch.setattr(
        rtc_crawler, "fetch_current_rtc",
        lambda *a, **k: [{"hissa_no": "2", "html": html, "error": None}],
    )
    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", "2",
    )
    assert results[0]["statusVerification"]["verificationRequired"] is True
    assert results[0]["statusVerification"]["reasonCode"] == "COLUMN_ALIGNMENT_AMBIGUOUS"


def test_session_expired_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise bhoomi_session.BhoomiSessionExpired("dead session")
    monkeypatch.setattr(rtc_crawler, "fetch_current_rtc", boom)

    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", "1",
    )
    assert len(results) == 1
    assert results[0]["currentStatus"] is None
    assert results[0]["statusVerification"]["reasonCode"] == "SESSION_EXPIRED"


def test_request_failure_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise bhoomi_session.BhoomiRequestFailed("timeout after retries")
    monkeypatch.setattr(rtc_crawler, "fetch_current_rtc", boom)

    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", "1",
    )
    assert results[0]["currentStatus"] is None
    assert results[0]["statusVerification"]["reasonCode"] == "RTC_REQUEST_FAILED"


def test_workflow_error_reported_not_raised(monkeypatch):
    def boom(*a, **k):
        raise rtc_crawler.RtcWorkflowError("Hissa '9' not found among options")
    monkeypatch.setattr(rtc_crawler, "fetch_current_rtc", boom)

    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", "9",
    )
    assert results[0]["currentStatus"] is None
    assert results[0]["statusVerification"]["reasonCode"] == "SOURCE_PARSE_FAILED"


def test_no_active_loan_boolean_fabricated_without_loan_history(monkeypatch):
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    monkeypatch.setattr(
        rtc_crawler, "fetch_current_rtc",
        lambda *a, **k: [{"hissa_no": "1", "html": html, "error": None}],
    )

    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", "1",
    )
    inference = results[0]["currentStatus"]["activeChargeInference"]
    assert inference["hasActiveLoanInferred"] is None
    assert inference["basis"] == "NO_LOAN_HISTORY_PROVIDED"
    assert inference["verificationRequired"] is True


def test_unreleased_charge_in_loan_history_surfaced_as_inference_not_fact(monkeypatch):
    html = load_fixture("rtc_fetch_details_hissa1_no_charge.html")
    monkeypatch.setattr(
        rtc_crawler, "fetch_current_rtc",
        lambda *a, **k: [{"hissa_no": "1", "html": html, "error": None}],
    )

    loan_history = [{
        "mrNo": "47", "instrumentClass": "charge_creation", "releaseStatus": "unreleased_as_of_crawl",
    }]
    results = rtc_verifier.verify_current_status(
        "BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", "1", loan_history=loan_history,
    )
    inference = results[0]["currentStatus"]["activeChargeInference"]
    assert inference["hasActiveLoanInferred"] is True
    assert inference["unreleasedMrNos"] == ["47"]
    assert inference["verificationRequired"] is True  # always - it's an inference, not the RTC's own statement
    assert inference["basis"] == "UNRELEASED_CHARGE_CREATION_IN_CRAWLED_MUTATION_HISTORY"
    assert "reasonCode" not in inference  # deliberately not part of the closed reason_codes.py vocabulary
