"""
district_monitor_schema.py
-----------------------------
Data model for the 31-district reachability monitor - a separate,
additive layer from schema.py's fetch_events (which stays exactly as
it was, still used by health_monitor.py's deeper 2-district RTC-record
checks). Nothing here changes fetch_events or its consumers.

Two collections:

  district_checks   - one document per (district, check attempt),
                       INSERT-ONLY, never updated or deleted. This is
                       the audit trail: every check that ever ran stays
                       exactly as it was recorded.

  district_incidents - one document per downtime incident, opened on a
                       UP->DOWN transition and the SAME document then
                       updated (end_timestamp/duration/status) when the
                       matching DOWN->UP transition closes it. This is
                       the one deliberate exception to "never overwrite":
                       an incident has a real lifecycle, and the closed
                       record still shows its own start untouched - nothing
                       about the audit trail in district_checks is ever
                       modified to make this work.

CHECK_STATUS vs TIMING_STATUS are two independent dimensions, not
collapsed into one field (Sec. "these must never be treated as the same
thing"):
  - CHECK_STATUS answers "what did we observe": up / down / check_error
    (check_error = our own check logic broke, not evidence the portal is
    down - e.g. an unhandled exception talking to Mongo).
  - TIMING_STATUS answers "did this run on schedule": on_time / delayed.
    A "missed" checkpoint is NOT a stored value on any check document -
    it's the ABSENCE of a document for an expected checkpoint slot,
    detected separately by district_monitor_aggregate.py comparing the
    real checkpoint sequence against the expected one.
"""

from datetime import datetime, timezone

SCHEMA_VERSION = "1.1"  # 1.1 adds check_type (automatic/manual) - additive, old docs simply lack the field

CHECK_STATUSES = ["up", "down", "check_error"]
CHECK_TYPES = ["automatic", "manual"]
TIMING_STATUSES = ["on_time", "delayed"]
INCIDENT_STATUSES = ["open", "resolved"]

CHECK_INTERVAL_MINUTES = 30
# A check is "delayed" if it ran more than this many minutes after its
# scheduled checkpoint slot - matching your own example (7 min late).
# Not "missed" until it's late enough there's a whole slot with no data
# at all (detected separately, see district_monitor_aggregate.py).
DELAY_THRESHOLD_MINUTES = 2


def uptime_downtime_label(check_status: str) -> str:
    """Human-readable label matching your example table's own wording."""
    if check_status == "up":
        return "Uptime"
    if check_status == "down":
        return "Downtime"
    return "Unknown"  # check_error - we don't know the district's real state


def new_district_check(
    district: str,
    district_code: str | None,
    service: str,
    check_performed: str,
    status: str,
    scheduled_for: datetime,
    timestamp: datetime | None = None,
    check_type: str = "automatic",
    downtime_start: datetime | None = None,
    downtime_end: datetime | None = None,
    downtime_duration_minutes: float | None = None,
    reason: str | None = None,
    error_message: str | None = None,
    action_taken: str = "None (automated monitoring only - no automated remediation is performed)",
    remarks: str | None = None,
    latency_ms: int | None = None,
) -> dict:
    if status not in CHECK_STATUSES:
        raise ValueError(f"Unknown check status: {status!r}")
    if check_type not in CHECK_TYPES:
        raise ValueError(f"Unknown check_type: {check_type!r}")
    ts = timestamp or datetime.now(timezone.utc)
    delay_minutes = round((ts - scheduled_for).total_seconds() / 60, 2)
    # A manually-triggered check is, by definition, on demand - "delay"
    # relative to a 30-min grid slot is meaningless for it (Sec. these
    # must never be treated as the same thing applies here too: don't
    # let a manual check masquerade as a late automatic one).
    timing_status = (
        "on_time" if check_type == "manual"
        else ("delayed" if delay_minutes > DELAY_THRESHOLD_MINUTES else "on_time")
    )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "date": ts.strftime("%Y-%m-%d"),
        "timestamp": ts,
        "checked_at": ts,  # explicit alias matching the requested column name - same value as `timestamp`,
                            # kept as a separate key rather than renaming `timestamp` (already indexed/tested)
        "scheduled_for": scheduled_for,
        "district": district,
        "district_code": district_code,
        "check_type": check_type,
        "service": service,
        "check_performed": check_performed,
        "status": status,
        "uptime_downtime": uptime_downtime_label(status),
        "downtime_start": downtime_start,
        "downtime_end": downtime_end,
        "downtime_duration_minutes": downtime_duration_minutes,
        "reason": reason,
        "error_message": error_message,
        "action_taken": action_taken,
        "current_status": status,
        "remarks": remarks,
        "timing_status": timing_status,
        "delay_minutes": delay_minutes if check_type == "automatic" else None,
        "latency_ms": latency_ms,
    }


def new_incident(
    district: str,
    district_code: str | None,
    start_timestamp: datetime,
    detected_timestamp: datetime,
    reason: str | None,
    error_message: str | None,
) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "district": district,
        "district_code": district_code,
        "start_timestamp": start_timestamp,
        # In this model, detection IS the check that first observed
        # "down" - we don't have finer-grained signal than our own
        # 30-min cadence, so start_timestamp == detected_timestamp
        # honestly, never inferred to be earlier.
        "detected_timestamp": detected_timestamp,
        "end_timestamp": None,
        "duration_minutes": None,
        "reason": reason,
        "error_message": error_message,
        "root_cause": None,  # never fabricated - stays None unless a human fills it in
        "action_taken": "Logged as an active incident - no automated remediation is performed.",
        "status": "open",
        "check_count_during_incident": 1,
    }


def close_incident(incident: dict, end_timestamp: datetime) -> dict:
    """Returns a new dict (caller writes it back) with the incident
    closed - the only field mutations this whole system ever performs,
    and only on this one document, never on district_checks."""
    duration = round((end_timestamp - incident["start_timestamp"]).total_seconds() / 60, 2)
    updated = dict(incident)
    updated["end_timestamp"] = end_timestamp
    updated["duration_minutes"] = duration
    updated["status"] = "resolved"
    updated["action_taken"] = "Recovered - resolved automatically once an 'up' check was observed."
    return updated
