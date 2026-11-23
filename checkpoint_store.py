"""
checkpoint_store.py
---------------------
Durable, resumable crawl state (Sec. 19), backed by MongoDB (per your
storage decision) alongside logger.py's fetch_events - a separate
`checkpoints` / `failed_requests` collection pair in the same `bhoomi`
database, so this pipeline survives a crash/restart/session-expiry the
same way the rest of the project already assumes Mongo will.

Checkpoints are written AFTER successful persistence of the corresponding
stage's data (crawler.py enforces the ordering; this module just makes
the write itself idempotent so a retried write never creates a
duplicate). Failures are logged separately from checkpoints so "what
succeeded" and "what needs a retry" can be queried independently
(Sec. 20).
"""

from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING
from pymongo.collection import Collection

from land_schema import new_checkpoint, new_failed_request


class CheckpointStore:
    def __init__(self, mongo_uri: str = "mongodb://localhost:27017", db_name: str = "bhoomi",
                 checkpoints: Collection | None = None, failed: Collection | None = None):
        """`checkpoints`/`failed` let tests inject an in-memory fake
        collection (tests/conftest.py's FakeCollection) instead of
        talking to a real MongoDB - passing either skips MongoClient
        construction entirely."""
        if checkpoints is not None or failed is not None:
            self.checkpoints = checkpoints
            self.failed = failed
        else:
            self._client = MongoClient(mongo_uri)
            self._db = self._client[db_name]
            self.checkpoints: Collection = self._db["checkpoints"]
            self.failed: Collection = self._db["failed_requests"]
            self._ensure_indexes()

    def _ensure_indexes(self):
        # Natural key uniqueness -> re-recording the same
        # unit+stage overwrites instead of accumulating duplicates
        # (Sec. 18 applies to checkpoint bookkeeping too, not just data).
        self.checkpoints.create_index(
            [("district", ASCENDING), ("taluk", ASCENDING), ("hobli", ASCENDING),
             ("village", ASCENDING), ("survey", ASCENDING), ("hissa", ASCENDING),
             ("mrNo", ASCENDING), ("stage", ASCENDING)],
            unique=True, name="uniq_checkpoint_unit_stage",
        )
        self.failed.create_index([("timestamp", -1)])
        self.failed.create_index([("district", 1), ("stage", 1)])

    def record(self, district: str, taluk: str | None, hobli: str | None, village: str | None,
               survey: str | None, hissa: str | None, mr_no: str | None, stage: str,
               status: str, detail: dict | None = None):
        doc = new_checkpoint(district, taluk, hobli, village, survey, hissa, mr_no, stage, status, detail)
        key = {k: doc[k] for k in ("district", "taluk", "hobli", "village", "survey", "hissa", "mrNo", "stage")}
        self.checkpoints.replace_one(key, doc, upsert=True)

    def is_complete(self, district: str, taluk: str | None, hobli: str | None, village: str | None,
                     survey: str | None, hissa: str | None, mr_no: str | None, stage: str) -> bool:
        """True if this exact unit+stage already has a success checkpoint
        - lets a resumed crawl skip work it already finished."""
        doc = self.checkpoints.find_one({
            "district": district, "taluk": taluk, "hobli": hobli, "village": village,
            "survey": survey, "hissa": hissa, "mrNo": mr_no, "stage": stage, "status": "success",
        })
        return doc is not None

    def record_failure(self, district: str, stage: str, reason_code: str, detail: dict, retry_count: int = 0):
        self.failed.insert_one(new_failed_request(district, stage, reason_code, detail, retry_count))
