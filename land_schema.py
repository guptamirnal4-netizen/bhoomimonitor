"""
land_schema.py
---------------
Single source of truth for the land-record extraction pipeline's Mongo
document shapes - the counterpart to schema.py, which only covers the
scrape-observability layer (fetch_events). Nothing here touches
schema.py's collection; this is new, additive state living alongside it
in the same `bhoomi` database.

Collections:

  land_records   - one document per (district, taluk, hobli, village,
                    survey, hissa). Holds rawMutations (metadata for
                    every mutation seen for that unit, unchanged shape
                    from the existing mutation JSON) plus loan_history
                    (this project's new extraction) and currentStatus
                    (RTC-backed, or a statusVerification flag when RTC
                    hasn't been fetched).

  loan_events     - one document per individual charge, flattened out of
                    loan_history, for cross-record/bank-wise analytics
                    (Sec. 28) without re-scanning every land_record.

  checkpoints     - one document per (unit, stage) - resumable progress
                    state (Sec. 19). Written AFTER successful
                    persistence of the corresponding stage's data, never
                    before.

  failed_requests - one document per failed HTTP/parse attempt (Sec. 20),
                    kept separate from checkpoints so failures can be
                    listed/retried without scanning success state.

Every builder below raises ValueError on structurally invalid input, the
same discipline schema.py uses for fetch_events - a typo should fail
loudly at write time.
"""

from datetime import datetime, timezone

SCHEMA_VERSION = "2.0"

INSTRUMENT_CLASSES = ["charge_creation", "charge_release", "unknown"]

CRAWL_STAGES = [
    "district_discovered",
    "taluk_discovered",
    "hobli_discovered",
    "village_discovered",
    "mutation_fetched",
    "preview_complete",
    "rtc_complete",
    "record_persisted",
]

CRAWL_STATUSES = ["success", "failed", "in_progress"]


def record_key(district: str, taluk: str, hobli: str, village: str, survey: str, hissa: str) -> str:
    """Fully qualified unique key - never use survey number alone (Sec. 17)."""
    parts = [district, taluk, hobli, village, survey, hissa]
    if any(p is None or str(p).strip() == "" for p in parts):
        raise ValueError(f"record_key requires all 6 parts, got {parts!r}")
    return ":".join(str(p).strip().upper() for p in parts)


def short_key(village: str, survey: str, hissa: str) -> str:
    """Preserves the existing shorter key style already used elsewhere
    in the project (e.g. RTC's "LOKAPURA|56|1"), per Sec. 17."""
    return f"{village}|{survey}|{hissa}"


def new_land_record(
    district: str,
    taluk: str,
    hobli: str,
    village: str,
    survey_number: str,
    hissa_no: str,
    raw_mutations: list,
    loan_history: list,
    current_status: dict | None,
    status_verification: dict,
    crawl_metadata: dict,
) -> dict:
    key = record_key(district, taluk, hobli, village, survey_number, hissa_no)
    skey = short_key(village, survey_number, hissa_no)
    if current_status is None and not status_verification.get("verificationRequired"):
        raise ValueError(
            "current_status is None but status_verification.verificationRequired "
            "is not True - Sec. 12 forbids silently treating a missing "
            "currentStatus as verified."
        )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "record_key": key,
        "short_key": skey,
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,
        "survey_number": survey_number,
        "Hissa_No": hissa_no,
        "rawMutationCount": len(raw_mutations),
        "rawMutations": raw_mutations,
        "loan_history": loan_history,
        "currentStatus": current_status,
        "statusVerification": status_verification,
        "crawl_metadata": crawl_metadata,
        "updated_at": datetime.now(timezone.utc),
    }


def new_loan_event(
    record_key_: str,
    short_key_: str,
    district: str,
    mr_no: str,
    transaction_year: str,
    transaction_no: str,
    instrument_class: str,
    bank: str | None,
    bank_raw: str | None,
    amount: float | None,
    amount_raw: str | None,
    verification_required: bool,
    reason_code: str,
    source_url: str,
) -> dict:
    if instrument_class not in INSTRUMENT_CLASSES:
        raise ValueError(f"Unknown instrument_class: {instrument_class!r}")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "record_key": record_key_,
        "short_key": short_key_,
        "district": district,
        "mrNo": mr_no,
        "transactionYear": transaction_year,
        "transactionNo": transaction_no,
        "instrumentClass": instrument_class,
        "bank": bank,
        "bankRaw": bank_raw,
        "amount": amount,
        "amountRaw": amount_raw,
        "verificationRequired": verification_required,
        "reasonCode": reason_code,
        "sourceUrl": source_url,
        "created_at": datetime.now(timezone.utc),
    }


def new_checkpoint(
    district: str,
    taluk: str | None,
    hobli: str | None,
    village: str | None,
    survey: str | None,
    hissa: str | None,
    mr_no: str | None,
    stage: str,
    status: str,
    detail: dict | None = None,
) -> dict:
    if stage not in CRAWL_STAGES:
        raise ValueError(f"Unknown stage: {stage!r}")
    if status not in CRAWL_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    return {
        "district": district,
        "taluk": taluk,
        "hobli": hobli,
        "village": village,
        "survey": survey,
        "hissa": hissa,
        "mrNo": mr_no,
        "stage": stage,
        "status": status,
        "detail": detail or {},
        "timestamp": datetime.now(timezone.utc),
    }


def new_failed_request(
    district: str,
    stage: str,
    reason_code: str,
    detail: dict,
    retry_count: int = 0,
) -> dict:
    return {
        "district": district,
        "stage": stage,
        "reason_code": reason_code,
        "detail": detail,
        "retry_count": retry_count,
        "timestamp": datetime.now(timezone.utc),
    }
