"""
aggregate_loans.py
--------------------
District-level loan/charge analytics (Sec. 28), computed ONLY from
land_records/loan_events actually persisted by this crawl - never
extrapolated to districts or units that haven't been crawled. Mirrors
aggregate.py's style (plain dict/list return values, ready to
JSON-serialize) but reads the new land_records/loan_events collections
instead of fetch_events.
"""

from pymongo.collection import Collection


def compute_district_loan_stats(land_records: Collection, loan_events: Collection, district: str) -> dict:
    """One district's stats, from whatever has actually been crawled and
    persisted for it so far. Sec. 28/29: no estimation, no Karnataka-wide
    rollup from a single district - callers must call this once per
    district they've actually crawled."""
    total_records = land_records.count_documents({"district": district})
    if total_records == 0:
        return {
            "district": district, "crawlStatus": "NOT_STARTED",
            "processedRecords": 0, "failedRecords": 0,
            "recordsWithHistoricalCharge": 0, "verifiedActiveLoanRecords": 0,
            "releasedLoanRecords": 0, "recordsRequiringVerification": 0,
            "totalVerifiedLoanAmount": 0.0, "bankWiseVerifiedAmount": {}, "bankWiseRecordCount": {},
        }

    with_history = land_records.count_documents({"district": district, "loan_history.0": {"$exists": True}})
    requiring_verification = land_records.count_documents({
        "district": district, "statusVerification.verificationRequired": True,
    })
    active_loan_records = land_records.count_documents({
        "district": district, "currentStatus.hasActiveLoan": True,
    })

    released_pipeline = [
        {"$match": {"district": district}},
        {"$unwind": "$loan_history"},
        {"$match": {"loan_history.instrumentClass": "charge_release", "loan_history.releasedChargeMatched": True}},
        {"$group": {"_id": "$record_key"}},
    ]
    released_loan_records = len(list(land_records.aggregate(released_pipeline)))

    verified_amount_pipeline = [
        {"$match": {"district": district, "verificationRequired": False, "amount": {"$ne": None}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]
    verified_rows = list(loan_events.aggregate(verified_amount_pipeline))
    total_verified_amount = verified_rows[0]["total"] if verified_rows else 0.0

    bank_amount_pipeline = [
        {"$match": {"district": district, "verificationRequired": False, "bank": {"$ne": None}}},
        {"$group": {"_id": "$bank", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
    ]
    bank_rows = list(loan_events.aggregate(bank_amount_pipeline))
    bank_wise_amount = {r["_id"]: round(r["total"], 2) for r in bank_rows}
    bank_wise_count = {r["_id"]: r["count"] for r in bank_rows}

    return {
        "district": district,
        "crawlStatus": "PARTIAL",  # Sec. 28: mark incomplete unless the caller knows the full district is done
        "processedRecords": total_records,
        "recordsWithHistoricalCharge": with_history,
        "verifiedActiveLoanRecords": active_loan_records,
        "releasedLoanRecords": released_loan_records,
        "recordsRequiringVerification": requiring_verification,
        "totalVerifiedLoanAmount": round(total_verified_amount, 2),
        "bankWiseVerifiedAmount": bank_wise_amount,
        "bankWiseRecordCount": bank_wise_count,
    }
