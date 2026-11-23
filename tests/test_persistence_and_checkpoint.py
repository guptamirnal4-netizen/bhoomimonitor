"""
Idempotency + resumability (Sec. 18/19/20): re-persisting the same
record, re-recording the same checkpoint, and re-crawling the same MR
must never multiply rows. Uses conftest.FakeCollection - no real MongoDB
needed to prove this logic.
"""

from checkpoint_store import CheckpointStore
from conftest import FakeCollection
from land_schema import new_land_record, record_key, short_key
from persistence import LandRecordStore


def _sample_land_record():
    return new_land_record(
        district="BAGALKOTE", taluk="MUDHOL", hobli="LOKAPURA", village="LOKAPURA",
        survey_number="56", hissa_no="1",
        raw_mutations=[{"surveyNo": "56/*/1", "mrNo": "3760"}],
        loan_history=[{
            "mrNo": "3760", "transactionYear": "2003-2004", "transactionNo": "25023",
            "instrumentClass": "charge_creation",
            "charges": [{"bank": None, "bankRaw": None, "amount": 12000.0, "amountRaw": "12,000/-"}],
            "sourceVerification": {"verificationRequired": True, "reasonCode": "LENDER_NOT_STATED_IN_RECORD",
                                    "sourceUrl": "https://example/mr3760"},
        }],
        current_status=None,
        status_verification={"verificationRequired": True, "reasonCode": "RTC_NOT_FETCHED"},
        crawl_metadata={"http_status": 200, "fetched_at": "2026-08-09T00:00:00Z"},
    )


# ---------- Scenario 16: duplicate crawl must not multiply land_records ----------

def test_duplicate_crawl_does_not_multiply_land_records():
    land_records = FakeCollection()
    loan_events = FakeCollection()
    store = LandRecordStore(land_records=land_records, loan_events=loan_events)

    doc = _sample_land_record()
    store.upsert_land_record(doc)
    store.upsert_land_record(doc)  # simulate re-crawling the exact same record
    store.upsert_land_record(doc)

    matches = [d for d in land_records.docs.values() if d["record_key"] == doc["record_key"]]
    assert len(matches) == 1, "re-persisting the same record_key must overwrite, not duplicate"


# ---------- Scenario 15: duplicate MR within loan_events must not multiply ----------

def test_duplicate_mr_in_loan_events_does_not_multiply():
    land_records = FakeCollection()
    loan_events = FakeCollection()
    store = LandRecordStore(land_records=land_records, loan_events=loan_events)

    doc = _sample_land_record()
    rec_key = doc["record_key"]
    s_key = short_key("LOKAPURA", "56", "1")

    store.upsert_loan_events(rec_key, s_key, "BAGALKOTE", doc["loan_history"])
    store.upsert_loan_events(rec_key, s_key, "BAGALKOTE", doc["loan_history"])  # re-run

    mr3760_rows = [d for d in loan_events.docs.values() if d["mrNo"] == "3760"]
    assert len(mr3760_rows) == 1, "the same MR+transactionNo+charge-index must overwrite, not duplicate"
    assert mr3760_rows[0]["amount"] == 12000.0


def test_record_key_never_survey_number_alone():
    key = record_key("BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", "1")
    assert key == "BAGALKOTE:MUDHOL:LOKAPURA:LOKAPURA:56:1"
    assert key != "56"  # Sec. 17


# ---------- Scenario 17: partial checkpoint recovery ----------

def test_checkpoint_marks_success_after_persistence_and_is_queryable_for_resume():
    ckpt = CheckpointStore(checkpoints=FakeCollection(), failed=FakeCollection())

    assert ckpt.is_complete("BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", None, None,
                             "record_persisted") is False

    ckpt.record("BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", None, None,
                "record_persisted", "success", {"hissas": ["1"]})

    assert ckpt.is_complete("BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", None, None,
                             "record_persisted") is True

    # re-recording the same unit+stage overwrites, never duplicates checkpoint rows
    ckpt.record("BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "56", None, None,
                "record_persisted", "success", {"hissas": ["1", "2"]})
    matches = [d for d in ckpt.checkpoints.docs.values() if d["survey"] == "56" and d["stage"] == "record_persisted"]
    assert len(matches) == 1


def test_failed_checkpoint_recorded_separately_from_success():
    ckpt = CheckpointStore(checkpoints=FakeCollection(), failed=FakeCollection())
    ckpt.record_failure("BAGALKOTE", "preview_complete", "PREVIEW_REQUEST_FAILED",
                         {"mrNo": "58", "error": "timeout"})
    assert ckpt.is_complete("BAGALKOTE", "MUDHOL", "LOKAPURA", "LOKAPURA", "58", None, None,
                             "preview_complete") is False
    assert len(ckpt.failed.docs) == 1
