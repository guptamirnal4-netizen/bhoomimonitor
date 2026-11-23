"""
logger.py
---------
What the scraper actually imports and calls. Two ways to use it:

1. Manual — you already know status/cost/latency:

    from logger import MonitorLogger

    mon = MonitorLogger()
    mon.log(
        district="Yadgir", service="Mutation", status="fail",
        error_type="captcha_fail", latency_ms=6100,
        cost={"proxy": 0.35, "captcha": 2.40, "compute": 0.05},
    )

2. Context manager — wraps the fetch call, times it, and infers
   success/fail from whether an exception was raised:

    with mon.track("Yadgir", "Mutation") as t:
        result = fetch_mutation_record(district="Yadgir", ...)
        t.cost(proxy=0.35, captcha=2.40, compute=0.05)
        t.hissa_key(result.hissa_key)

   If the wrapped block raises, log it as a failure. Tag the specific
   error with t.error("captcha_fail") before raising, or the tracker
   falls back to "server_error" as a safe default — the block still
   needs its own except clause to actually classify the error, this
   is just to make sure something honest gets written to the log
   even when it's not the one you expect.
"""

import time
from contextlib import contextmanager
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.collection import Collection

from schema import new_fetch_event


class MonitorLogger:
    def __init__(self, mongo_uri: str = "mongodb://localhost:27017", db_name: str = "bhoomi"):
        self._client = MongoClient(mongo_uri)
        self._db = self._client[db_name]
        self.events: Collection = self._db["fetch_events"]
        self._ensure_indexes()

    def _ensure_indexes(self):
        # These indexes are what make the aggregation queries in
        # aggregate.py fast instead of full-collection scans once
        # fetch_events grows past a few hundred thousand rows.
        self.events.create_index([("timestamp", -1)])
        self.events.create_index([("district", 1), ("timestamp", -1)])
        self.events.create_index([("service", 1), ("status", 1), ("timestamp", -1)])

    def log(
        self,
        district: str,
        service: str,
        status: str,
        latency_ms: int,
        cost: dict,
        error_type: str | None = None,
        retry_count: int = 0,
        hissa_key: str | None = None,
        timestamp: datetime | None = None,
    ) -> str:
        doc = new_fetch_event(
            district=district, service=service, status=status,
            latency_ms=latency_ms, cost=cost, error_type=error_type,
            retry_count=retry_count, hissa_key=hissa_key, timestamp=timestamp,
        )
        result = self.events.insert_one(doc)
        return str(result.inserted_id)

    @contextmanager
    def track(self, district: str, service: str, retry_count: int = 0):
        tracker = _FetchTracker(district, service, retry_count)
        start = time.monotonic()
        try:
            yield tracker
        except Exception:
            tracker._error_type = tracker._error_type or "server_error"
            tracker._status = "fail"
            raise
        else:
            tracker._status = tracker._status or "success"
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            self.log(
                district=district,
                service=service,
                status=tracker._status,
                latency_ms=latency_ms,
                cost=tracker._cost,
                error_type=tracker._error_type if tracker._status == "fail" else None,
                retry_count=retry_count,
                hissa_key=tracker._hissa_key,
                timestamp=datetime.now(timezone.utc),
            )


class _FetchTracker:
    """Small mutable bag the `with mon.track(...) as t:` block fills in."""

    def __init__(self, district: str, service: str, retry_count: int):
        self.district = district
        self.service = service
        self.retry_count = retry_count
        self._cost = {"proxy": 0.0, "captcha": 0.0, "compute": 0.0}
        self._hissa_key = None
        self._error_type = None
        self._status = None  # set explicitly, or inferred by track()

    def cost(self, proxy: float = 0.0, captcha: float = 0.0, compute: float = 0.0):
        self._cost = {"proxy": proxy, "captcha": captcha, "compute": compute}

    def hissa_key(self, key: str):
        self._hissa_key = key

    def error(self, error_type: str):
        self._error_type = error_type
        self._status = "fail"

    def mark_success(self):
        self._status = "success"
