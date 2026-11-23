"""
persistence.py
----------------
Durable output for extracted land records, backed by MongoDB (per your
storage decision): a `land_records` collection (Sec. 27 "detailed record
JSON") and a `loan_events` collection (Sec. 27 "loan/charge event JSON",
flattened for the district-level analytics in aggregate_loans.py).

Both writes are idempotent (Sec. 18): land_records is keyed on
record_key, loan_events on a deterministic id built from
record_key+mrNo+transactionNo+charge-index, so re-crawling the same
survey/hissa overwrites instead of multiplying rows.
"""

from pymongo import MongoClient, ASCENDING
from pymongo.collection import Collection

from land_schema import new_loan_event


class LandRecordStore:
    def __init__(self, mongo_uri: str = "mongodb://localhost:27017", db_name: str = "bhoomi",
                 land_records: Collection | None = None, loan_events: Collection | None = None):
        """`land_records`/`loan_events` let tests inject an in-memory
        fake collection (tests/conftest.py's FakeCollection) instead of
        talking to a real MongoDB - passing either skips MongoClient
        construction entirely."""
        if land_records is not None or loan_events is not None:
            self.land_records = land_records
            self.loan_events = loan_events
        else:
            self._client = MongoClient(mongo_uri)
            self._db = self._client[db_name]
            self.land_records: Collection = self._db["land_records"]
            self.loan_events: Collection = self._db["loan_events"]
            self._ensure_indexes()

    def _ensure_indexes(self):
        self.land_records.create_index([("record_key", ASCENDING)], unique=True)
        self.land_records.create_index([("district", 1), ("taluk", 1)])
        self.loan_events.create_index([("district", 1), ("instrumentClass", 1)])
        self.loan_events.create_index([("bank", 1)])

    def upsert_land_record(self, doc: dict):
        self.land_records.replace_one({"record_key": doc["record_key"]}, doc, upsert=True)

    def upsert_loan_events(self, record_key: str, short_key: str, district: str, loan_history: list[dict]):
        """Flattens loan_history's charges[] into individual loan_events
        rows. One row per charge, not per MR - a single MR with 3
        separately-recorded charges (Sec. 25) becomes 3 rows here, each
        independently traceable back to its MR/transaction/source URL."""
        for event in loan_history:
            charges = event.get("charges") or [{
                "bank": None, "bankRaw": None, "amount": None, "amountRaw": None,
            }]
            sv = event.get("sourceVerification", {})
            for idx, charge in enumerate(charges):
                doc_id = f"{record_key}::{event['mrNo']}::{event['transactionNo']}::{idx}"
                doc = new_loan_event(
                    record_key_=record_key,
                    short_key_=short_key,
                    district=district,
                    mr_no=event["mrNo"],
                    transaction_year=event["transactionYear"],
                    transaction_no=event["transactionNo"],
                    instrument_class=event["instrumentClass"],
                    bank=charge.get("bank"),
                    bank_raw=charge.get("bankRaw"),
                    amount=charge.get("amount"),
                    amount_raw=charge.get("amountRaw"),
                    verification_required=sv.get("verificationRequired", True),
                    reason_code=sv.get("reasonCode", "UNKNOWN"),
                    source_url=sv.get("sourceUrl"),
                )
                doc["_id"] = doc_id
                self.loan_events.replace_one({"_id": doc_id}, doc, upsert=True)

    def get_land_record(self, record_key: str) -> dict | None:
        return self.land_records.find_one({"record_key": record_key})
