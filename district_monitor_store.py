"""
district_monitor_store.py
----------------------------
Persistence for the 31-district monitor: `district_checks` (insert-only
audit log - every check, no exceptions) and `district_incidents`
(opened on UP->DOWN, closed on DOWN->UP - the one place this system
updates an existing document, and only that document, never a
district_checks row).
"""

from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING
from pymongo.collection import Collection

from district_monitor_schema import new_district_check, new_incident, close_incident


class DistrictMonitorStore:
    def __init__(self, mongo_uri: str = "mongodb://localhost:27017", db_name: str = "bhoomi",
                 checks: Collection | None = None, incidents: Collection | None = None):
        """`checks`/`incidents` let tests inject an in-memory fake
        collection instead of talking to a real MongoDB."""
        if checks is not None or incidents is not None:
            self.checks = checks
            self.incidents = incidents
        else:
            # tz_aware=True: without it, pymongo hands back NAIVE UTC
            # datetimes even though we insert timezone-aware ones (a real
            # bug found live - district_monitor_aggregate.py's arithmetic
            # against a fresh datetime.now(timezone.utc) throws
            # "can't subtract offset-naive and offset-aware datetimes"
            # otherwise). Keeps reads consistent with what's written.
            client = MongoClient(mongo_uri, tz_aware=True)
            db = client[db_name]
            self.checks: Collection = db["district_checks"]
            self.incidents: Collection = db["district_incidents"]
            self._ensure_indexes()

    def _ensure_indexes(self):
        self.checks.create_index([("timestamp", -1)])
        self.checks.create_index([("district", 1), ("timestamp", -1)])
        self.checks.create_index([("scheduled_for", 1)])
        self.incidents.create_index([("district", 1), ("status", 1)])
        self.incidents.create_index([("start_timestamp", -1)])

    def get_open_incident(self, district: str) -> dict | None:
        return self.incidents.find_one({"district": district, "status": "open"})

    def record_check(
        self,
        district: str,
        district_code: str | None,
        service: str,
        check_performed: str,
        status: str,
        scheduled_for: datetime,
        check_type: str = "automatic",
        reason: str | None = None,
        error_message: str | None = None,
        latency_ms: int | None = None,
        timestamp: datetime | None = None,
    ) -> dict:
        """Records exactly one check, always (Sec. "no monitoring
        activity should happen without generating a corresponding log
        entry") - and opens/closes an incident as needed based on the
        transition. Returns the written district_checks document."""
        ts = timestamp or datetime.now(timezone.utc)
        open_incident = self.get_open_incident(district)

        downtime_start = downtime_end = downtime_duration = None

        if status == "down":
            if open_incident is None:
                incident = new_incident(district, district_code, start_timestamp=ts,
                                         detected_timestamp=ts, reason=reason, error_message=error_message)
                self.incidents.insert_one(incident)
                downtime_start = ts
            else:
                # continuation of the same incident - don't open a second one
                self.incidents.replace_one(
                    {"district": district, "status": "open"},
                    {**open_incident, "check_count_during_incident": open_incident.get("check_count_during_incident", 1) + 1},
                )
                downtime_start = open_incident["start_timestamp"]

        elif status == "up" and open_incident is not None:
            closed = close_incident(open_incident, end_timestamp=ts)
            self.incidents.replace_one({"district": district, "status": "open"}, closed)
            downtime_start = closed["start_timestamp"]
            downtime_end = closed["end_timestamp"]
            downtime_duration = closed["duration_minutes"]

        # status == "check_error" (or "up" with no open incident): no
        # incident is opened or closed - inconclusive/nothing-to-report,
        # never guessed either way.

        doc = new_district_check(
            district=district, district_code=district_code, service=service,
            check_performed=check_performed, status=status, scheduled_for=scheduled_for,
            timestamp=ts, check_type=check_type, downtime_start=downtime_start, downtime_end=downtime_end,
            downtime_duration_minutes=downtime_duration, reason=reason, error_message=error_message,
            latency_ms=latency_ms,
        )
        self.checks.insert_one(doc)
        return doc
