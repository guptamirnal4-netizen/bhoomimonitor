"""
district_monitor_aggregate.py
--------------------------------
Turns district_checks/district_incidents into the views requested:
per-district uptime stats, the "all 31 districts, one checkpoint" grid,
historical checkpoint listings, missed/delayed-check detection, and
district-level + overall reports.

Uptime formula, exactly as specified:
    Uptime % = (Total Monitoring Time - Total Downtime) / Total Monitoring Time * 100

"Total Monitoring Time" is wall-clock time from this district's FIRST
recorded check to now (or to an explicit `until`) - not the count of
checks, matching the instruction that this "should be based on actual
recorded events rather than assumptions". An in-progress (open)
incident's downtime is counted up to `until`/now, not ignored just
because it hasn't closed yet.
"""

from datetime import datetime, timedelta, timezone

from pymongo.collection import Collection

from district_monitor_schema import CHECK_INTERVAL_MINUTES
from district_registry import DISTRICT_PORTAL_TEXT

ALL_DISTRICTS = sorted(DISTRICT_PORTAL_TEXT.keys())


def _now():
    return datetime.now(timezone.utc)


SORTABLE_FIELDS = {"checked_at", "district", "check_type", "status", "scheduled_for", "timestamp"}


def query_checks(
    checks: Collection,
    district: str | None = None,
    check_type: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    sort_field: str = "checked_at",
    sort_dir: int = -1,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Flat, filterable, sortable check-log listing - EVERY check
    (automatic and manual), nothing hidden by default (Sec. "Outage Log
    Visibility"). Distinct from compute_checkpoint_snapshot()/
    compute_historical_checkpoints(), which are checkpoint-grid shaped
    (one 30-min slot x all 31 districts) - manual checks deliberately
    don't participate in that grid (see district_monitor.py's
    run_cycle() docstring), so this is the view that surfaces them."""
    if sort_field not in SORTABLE_FIELDS:
        raise ValueError(f"Unsupported sort_field: {sort_field!r} (must be one of {sorted(SORTABLE_FIELDS)})")

    match = {}
    if district:
        match["district"] = district
    if check_type:
        match["check_type"] = check_type
    if start or end:
        range_filter = {}
        if start:
            range_filter["$gte"] = start
        if end:
            range_filter["$lte"] = end
        match["checked_at"] = range_filter

    total = checks.count_documents(match)
    cursor = checks.find(match).sort(sort_field, sort_dir).skip(offset).limit(limit)
    rows = []
    for c in cursor:
        row = dict(c)
        row.pop("_id", None)
        for field in ("timestamp", "checked_at", "scheduled_for", "downtime_start", "downtime_end"):
            if row.get(field) is not None:
                row[field] = row[field].isoformat()
        rows.append(row)

    return {"total": total, "limit": limit, "offset": offset, "checks": rows}


def compute_district_uptime(checks: Collection, incidents: Collection, district: str,
                             until: datetime | None = None) -> dict:
    """Per-district metrics (Sec. "Uptime Tracking"): total monitoring
    period, expected monitoring time, total uptime, total downtime,
    incident count, avg/max downtime, uptime %, downtime %, last
    successful check, current status."""
    until = until or _now()
    all_checks = list(checks.find({"district": district}).sort("timestamp", 1))
    if not all_checks:
        return {
            "district": district, "totalMonitoringMinutes": 0, "expectedMonitoringMinutes": 0,
            "totalUptimeMinutes": 0, "totalDowntimeMinutes": 0, "incidentCount": 0,
            "averageDowntimeMinutes": None, "longestDowntimeMinutes": None,
            "uptimePercent": None, "downtimePercent": None,
            "lastSuccessfulCheck": None, "currentStatus": "not_checked",
            "totalChecks": 0,
        }

    first_check_time = all_checks[0]["timestamp"]
    total_monitoring_minutes = max((until - first_check_time).total_seconds() / 60, 0.0)
    expected_checks = max(int(total_monitoring_minutes // CHECK_INTERVAL_MINUTES) + 1, 1)

    district_incidents = list(incidents.find({"district": district}))
    durations = []
    for inc in district_incidents:
        if inc["status"] == "resolved":
            durations.append(inc["duration_minutes"])
        else:  # still open - count elapsed time up to `until`, not ignored
            durations.append(max((until - inc["start_timestamp"]).total_seconds() / 60, 0.0))

    total_downtime = sum(durations)
    total_uptime = max(total_monitoring_minutes - total_downtime, 0.0)
    uptime_pct = round(100 * total_uptime / total_monitoring_minutes, 2) if total_monitoring_minutes else None
    downtime_pct = round(100 - uptime_pct, 2) if uptime_pct is not None else None

    last_success = None
    for c in reversed(all_checks):
        if c["status"] == "up":
            last_success = c["timestamp"]
            break

    latest_code = next((c["district_code"] for c in reversed(all_checks) if c.get("district_code")), None)

    return {
        "district": district,
        "district_code": latest_code,
        "totalMonitoringMinutes": round(total_monitoring_minutes, 2),
        "expectedMonitoringMinutes": expected_checks * CHECK_INTERVAL_MINUTES,
        "totalUptimeMinutes": round(total_uptime, 2),
        "totalDowntimeMinutes": round(total_downtime, 2),
        "incidentCount": len(district_incidents),
        "averageDowntimeMinutes": round(sum(durations) / len(durations), 2) if durations else None,
        "longestDowntimeMinutes": round(max(durations), 2) if durations else None,
        "uptimePercent": uptime_pct,
        "downtimePercent": downtime_pct,
        "lastSuccessfulCheck": last_success.isoformat() if last_success else None,
        "currentStatus": all_checks[-1]["status"],
        "totalChecks": len(all_checks),
    }


def compute_all_districts_uptime(checks: Collection, incidents: Collection,
                                  until: datetime | None = None) -> list[dict]:
    """One row per canonical district (Sec. 31-District Dashboard) -
    every one of the 31, even a district with zero checks ever, so a
    silently-unmonitored district shows up as "not_checked" instead of
    just disappearing."""
    return [compute_district_uptime(checks, incidents, d, until=until) for d in ALL_DISTRICTS]


def compute_checkpoint_snapshot(checks: Collection, scheduled_for: datetime) -> list[dict]:
    """Every one of the 31 districts' record for ONE specific 30-min
    checkpoint slot (Sec. "30-Minute Monitoring Record") - districts
    with no check recorded for this exact slot are still included,
    marked not_checked, never silently omitted."""
    rows = {c["district"]: c for c in checks.find({"scheduled_for": scheduled_for})}
    out = []
    for d in ALL_DISTRICTS:
        if d in rows:
            c = rows[d]
            out.append({
                "district": d, "district_code": c.get("district_code"), "status": c["status"],
                "uptime_downtime": c["uptime_downtime"], "downtime_duration_minutes": c.get("downtime_duration_minutes"),
                "reason": c.get("reason"), "action_taken": c.get("action_taken"),
                "timing_status": c.get("timing_status"), "delay_minutes": c.get("delay_minutes"),
                "timestamp": c["timestamp"].isoformat(),
            })
        else:
            out.append({
                "district": d, "district_code": None, "status": "not_checked",
                "uptime_downtime": "Unknown", "downtime_duration_minutes": None,
                "reason": "No check recorded for this checkpoint", "action_taken": None,
                "timing_status": "missed", "delay_minutes": None, "timestamp": None,
            })
    return out


def list_checkpoints(checks: Collection, start: datetime, end: datetime) -> list[datetime]:
    """Every distinct scheduled_for slot that has AT LEAST ONE check
    recorded, within [start, end] - the real, observed checkpoint
    sequence (not assumed from the interval alone)."""
    return sorted(checks.distinct("scheduled_for", {"scheduled_for": {"$gte": start, "$lte": end}}))


def compute_historical_checkpoints(checks: Collection, start: datetime, end: datetime) -> list[dict]:
    """Sec. "Historical Monitoring": one entry per real checkpoint in
    range, each with the full 31-district snapshot - permanently
    available, nothing overwritten (district_checks is insert-only)."""
    return [
        {"scheduled_for": cp.isoformat(), "districts": compute_checkpoint_snapshot(checks, cp)}
        for cp in list_checkpoints(checks, start, end)
    ]


def detect_missed_and_delayed_checks(checks: Collection, start: datetime, end: datetime,
                                      interval_minutes: int = CHECK_INTERVAL_MINUTES) -> dict:
    """Sec. "Missing-Check Detection": compares the theoretical expected
    checkpoint sequence (every `interval_minutes` from `start` to `end`)
    against what was actually recorded.

    Returns:
      missedCheckpoints - expected slots with NO check for ANY district
                          at all (the whole monitor cycle didn't run)
      partialCheckpoints - slots where SOME but not all 31 districts
                          were checked (the cycle started but didn't finish)
      delayedChecks      - individual district checks that ran, but late
                          (delay_minutes > DELAY_THRESHOLD_MINUTES)
    """
    expected = []
    t = start
    while t <= end:
        expected.append(t)
        t += timedelta(minutes=interval_minutes)

    actual = set(list_checkpoints(checks, start, end))
    missed = [t.isoformat() for t in expected if t not in actual]

    partial = []
    for cp in sorted(actual):
        n = checks.count_documents({"scheduled_for": cp})
        if 0 < n < len(ALL_DISTRICTS):
            partial.append({"scheduled_for": cp.isoformat(), "districtsChecked": n, "districtsExpected": len(ALL_DISTRICTS)})

    delayed_cursor = checks.find({
        "scheduled_for": {"$gte": start, "$lte": end},
        "timing_status": "delayed",
    })
    delayed = [
        {"district": c["district"], "scheduled_for": c["scheduled_for"].isoformat(),
         "actual_timestamp": c["timestamp"].isoformat(), "delay_minutes": c["delay_minutes"]}
        for c in delayed_cursor
    ]

    return {"missedCheckpoints": missed, "partialCheckpoints": partial, "delayedChecks": delayed}


def compute_overall_report(checks: Collection, incidents: Collection,
                            until: datetime | None = None) -> dict:
    """Sec. "Reporting" - overall metrics across all 31 districts."""
    until = until or _now()
    per_district = compute_all_districts_uptime(checks, incidents, until=until)
    monitored = [d for d in per_district if d["totalChecks"] > 0]

    total_uptime = sum(d["totalUptimeMinutes"] for d in monitored)
    total_downtime = sum(d["totalDowntimeMinutes"] for d in monitored)
    total_monitoring = total_uptime + total_downtime
    overall_uptime_pct = round(100 * total_uptime / total_monitoring, 2) if total_monitoring else None

    with_uptime = [d for d in monitored if d["uptimePercent"] is not None]
    best = max(with_uptime, key=lambda d: d["uptimePercent"], default=None)
    worst = min(with_uptime, key=lambda d: d["uptimePercent"], default=None)
    # Only name a "highest downtime" district when downtime is actually
    # > 0 somewhere - naming one when every district is at 0 minutes
    # would misleadingly imply a problem that doesn't exist.
    with_downtime = [d for d in monitored if d["totalDowntimeMinutes"] > 0]
    highest_downtime = max(with_downtime, key=lambda d: d["totalDowntimeMinutes"], default=None)

    reason_counts = {}
    for inc in incidents.find({}):
        r = inc.get("reason") or "unknown"
        reason_counts[r] = reason_counts.get(r, 0) + 1
    most_frequent_reason = max(reason_counts.items(), key=lambda kv: kv[1])[0] if reason_counts else None

    return {
        "generatedAt": until.isoformat(),
        "districtsMonitored": len(monitored),
        "districtsNeverChecked": len(per_district) - len(monitored),
        "overallUptimePercent": overall_uptime_pct,
        "overallDowntimePercent": round(100 - overall_uptime_pct, 2) if overall_uptime_pct is not None else None,
        "totalIncidents": sum(d["incidentCount"] for d in monitored),
        "districtWithHighestUptime": best["district"] if best else None,
        "districtWithLowestUptime": worst["district"] if worst else None,
        "districtWithHighestDowntime": highest_downtime["district"] if highest_downtime else None,
        "mostFrequentFailureReason": most_frequent_reason,
        "totalChecksPerformed": checks.count_documents({}),
        "perDistrict": per_district,
    }
