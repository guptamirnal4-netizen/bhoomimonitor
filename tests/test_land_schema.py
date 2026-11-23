"""land_schema.py's validation discipline - a typo/logic slip must fail
loudly at write time, matching schema.py's existing discipline for
fetch_events."""

import pytest

import land_schema


def test_record_key_requires_all_six_parts():
    with pytest.raises(ValueError):
        land_schema.record_key("BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "", "1")


def test_record_key_is_fully_qualified_not_survey_alone():
    key = land_schema.record_key("bagalkote", "mudhol", "lokapura", "lokapura", "56", "1")
    assert key == "BAGALKOTE:MUDHOL:LOKAPURA:LOKAPURA:56:1"


def test_short_key_matches_existing_project_style():
    assert land_schema.short_key("LOKAPURA", "56", "1") == "LOKAPURA|56|1"


def test_new_land_record_rejects_unverified_current_status():
    # Sec. 12: a missing currentStatus must never be silently treated as verified.
    with pytest.raises(ValueError):
        land_schema.new_land_record(
            district="BAGALKOTE", taluk="MUDHOL", hobli="LOKAPURA", village="LOKAPURA",
            survey_number="56", hissa_no="1", raw_mutations=[], loan_history=[],
            current_status=None,
            status_verification={"verificationRequired": False, "reasonCode": "NONE"},
            crawl_metadata={},
        )


def test_new_land_record_accepts_flagged_unverified_status():
    doc = land_schema.new_land_record(
        district="BAGALKOTE", taluk="MUDHOL", hobli="LOKAPURA", village="LOKAPURA",
        survey_number="56", hissa_no="1", raw_mutations=[], loan_history=[],
        current_status=None,
        status_verification={"verificationRequired": True, "reasonCode": "RTC_NOT_FETCHED"},
        crawl_metadata={},
    )
    assert doc["currentStatus"] is None
    assert doc["statusVerification"]["reasonCode"] == "RTC_NOT_FETCHED"


def test_new_loan_event_rejects_unknown_instrument_class():
    with pytest.raises(ValueError):
        land_schema.new_loan_event(
            record_key_="X", short_key_="X", district="BAGALKOTE", mr_no="1",
            transaction_year="2020-2021", transaction_no="1", instrument_class="loan_active",
            bank=None, bank_raw=None, amount=None, amount_raw=None,
            verification_required=True, reason_code="UNKNOWN", source_url="",
        )


def test_new_checkpoint_rejects_unknown_stage_or_status():
    with pytest.raises(ValueError):
        land_schema.new_checkpoint("BAGALKOTE", None, None, None, None, None, None,
                                    stage="not_a_real_stage", status="success")
    with pytest.raises(ValueError):
        land_schema.new_checkpoint("BAGALKOTE", None, None, None, None, None, None,
                                    stage="mutation_fetched", status="not_a_real_status")
