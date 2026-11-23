"""
aggregate.py
------------
Turns raw fetch_events into the shapes the dashboard tabs need.
Each function returns plain dicts/lists ready to JSON-serialize —
api.py just calls these and returns the result.

Nothing here is precomputed/cached: with proper indexes (see
logger.py) these pipelines stay fast into the low millions of rows.
If fetch_events outgrows that, the place to add a rollup collection
is compute_district_status() and compute_portal_uptime(), run on a
schedule (e.g. every 15 min) instead of per-request — the query
shapes below wouldn't need to change, only where they're called from.
"""

from datetime import datetime, timedelta, timezone

from pymongo.collection import Collection

from schema import SERVICE_WEIGHTS, STATUS_THRESHOLDS, DISTRICTS


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _since_hours(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _status_for(overall: float) -> str:
    if overall >= STATUS_THRESHOLDS["ok"]:
        return "ok"
    if overall >= STATUS_THRESHOLDS["degraded"]:
        return "degraded"
    return "fail"


def compute_district_status(events: Collection, days: int = 1) -> list[dict]:
    """One row per district: RTC%, Mutation%, blended overall%, status,
    and fetch count, for the window ending now. Districts with zero
    fetches in the window are included at 0% so a silently-dead
    district shows up as REJECTED instead of just disappearing."""
    pipeline = [
        {"$match": {"timestamp": {"$gte": _since(days)}}},
        {"$group": {
            "_id": {"district": "$district", "service": "$service"},
            "total": {"$sum": 1},
            "successes": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
        }},
    ]
    rows = list(events.aggregate(pipeline))

    # rows[i] -> rate lookup: {(district, service): (rate_pct, count)}
    rates = {}
    for r in rows:
        d, s = r["_id"]["district"], r["_id"]["service"]
        rate = round(100 * r["successes"] / r["total"], 1) if r["total"] else 0.0
        rates[(d, s)] = (rate, r["total"])

    out = []
    for district in DISTRICTS:
        rtc_rate, rtc_n = rates.get((district, "RTC"), (0.0, 0))
        mut_rate, mut_n = rates.get((district, "Mutation"), (0.0, 0))
        overall = round(
            rtc_rate * SERVICE_WEIGHTS["RTC"] + mut_rate * SERVICE_WEIGHTS["Mutation"], 1
        )
        out.append({
            "district": district,
            "rtc_rate": rtc_rate,
            "mutation_rate": mut_rate,
            "overall": overall,
            "status": _status_for(overall),
            "fetches": rtc_n + mut_n,
        })
    out.sort(key=lambda r: r["overall"])
    return out


def compute_service_reliability(events: Collection, days: int = 7) -> dict:
    """Success rate per service, plus a breakdown of failure reasons
    per service — this is what explains *why* Mutation usually trails
    RTC (heavier CAPTCHA gating shows up as a captcha_fail spike)."""
    pipeline = [
        {"$match": {"timestamp": {"$gte": _since(days)}}},
        {"$group": {
            "_id": {"service": "$service", "status": "$status", "error_type": "$error_type"},
            "count": {"$sum": 1},
        }},
    ]
    rows = list(events.aggregate(pipeline))

    result = {s: {"total": 0, "successes": 0, "failure_reasons": {}} for s in SERVICE_WEIGHTS}
    for r in rows:
        service = r["_id"]["service"]
        status = r["_id"]["status"]
        count = r["count"]
        result[service]["total"] += count
        if status == "success":
            result[service]["successes"] += count
        else:
            reason = r["_id"]["error_type"] or "unknown"
            result[service]["failure_reasons"][reason] = (
                result[service]["failure_reasons"].get(reason, 0) + count
            )

    for service, data in result.items():
        data["success_rate"] = (
            round(100 * data["successes"] / data["total"], 1) if data["total"] else 0.0
        )
    return result


def compute_cost_trend(events: Collection, days: int = 14) -> list[dict]:
    """One row per calendar day: cost split by proxy / captcha / compute,
    in ascending date order — matches what the dashboard's stacked bar
    chart expects."""
    pipeline = [
        {"$match": {"timestamp": {"$gte": _since(days)}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "proxy": {"$sum": "$cost.proxy"},
            "captcha": {"$sum": "$cost.captcha"},
            "compute": {"$sum": "$cost.compute"},
            "fetches": {"$sum": 1},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = list(events.aggregate(pipeline))
    return [
        {
            "day": r["_id"],
            "proxy": round(r["proxy"], 2),
            "captcha": round(r["captcha"], 2),
            "compute": round(r["compute"], 2),
            "total": round(r["proxy"] + r["captcha"] + r["compute"], 2),
            "fetches": r["fetches"],
            "cost_per_fetch": round((r["proxy"] + r["captcha"] + r["compute"]) / r["fetches"], 3)
            if r["fetches"] else 0.0,
        }
        for r in rows
    ]


def compute_portal_uptime(events: Collection, days: int = 30) -> list[dict]:
    """One row per calendar day: share of all fetch attempts (any
    district, any service) that succeeded without needing a retry.
    This is the 'is the portal itself healthy today' line, separate
    from any single district's status."""
    pipeline = [
        {"$match": {"timestamp": {"$gte": _since(days)}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
            "total": {"$sum": 1},
            "clean_successes": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$eq": ["$status", "success"]},
                            {"$eq": ["$retry_count", 0]},
                        ]},
                        1, 0,
                    ]
                }
            },
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = list(events.aggregate(pipeline))
    return [
        {
            "day": r["_id"],
            "uptime": round(100 * r["clean_successes"] / r["total"], 1) if r["total"] else 0.0,
            "fetches": r["total"],
        }
        for r in rows
    ]


def compute_uptime_timeline(events: Collection, hours: int = 48, service: str | None = None) -> dict:
    """Per-EVENT (not daily-rollup) history for the last `hours` hours —
    answers "was the portal down at some specific time", which
    compute_portal_uptime()'s one-row-per-calendar-day average can't
    (a day that's 50% down still just shows "50%", not when).

    `service` filters to one service (e.g. "RTC", the health-check
    cadence health_monitor.py writes every 30 min) — recommended,
    since mixing in irregular ad-hoc crawl activity (e.g. a Mutation
    batch crawl run at 2am) would create gaps/clusters in the timeline
    unrelated to actual portal uptime. Pass None for every service.

    Returns:
        {
          "events": [{"timestamp", "district", "service", "status",
                       "error_type", "latency_ms"}, ...],  # chronological
          "outages": [{"start", "end", "count", "reasons", "ongoing"?}, ...]
        }
    `outages` is every contiguous run of "fail" events collapsed into a
    start/end window — the direct answer to "when was it down", derived
    here (not left to the frontend) so every consumer sees the same
    windows.
    """
    match = {"timestamp": {"$gte": _since_hours(hours)}}
    if service:
        match["service"] = service
    cursor = events.find(match).sort("timestamp", 1)

    rows = [
        {
            "timestamp": r["timestamp"].isoformat(),
            "district": r["district"],
            "service": r["service"],
            "status": r["status"],
            "error_type": r.get("error_type"),
            "latency_ms": r["latency_ms"],
        }
        for r in cursor
    ]

    outages = []
    current = None
    for row in rows:
        if row["status"] == "fail":
            if current is None:
                current = {"start": row["timestamp"], "end": row["timestamp"], "count": 1,
                           "reasons": {row["error_type"] or "unknown"}}
            else:
                current["end"] = row["timestamp"]
                current["count"] += 1
                current["reasons"].add(row["error_type"] or "unknown")
        elif current is not None:
            current["reasons"] = sorted(current["reasons"])
            outages.append(current)
            current = None
    if current is not None:
        current["reasons"] = sorted(current["reasons"])
        current["ongoing"] = True
        outages.append(current)

    return {"events": rows, "outages": outages}


def recent_failures(events: Collection, limit: int = 20) -> list[dict]:
    """Latest failures across all districts/services, newest first —
    feeds the failure log table."""
    cursor = (
        events.find({"status": "fail"})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return [
        {
            "time": r["timestamp"].strftime("%H:%M"),
            "district": r["district"],
            "service": r["service"],
            "reason": r.get("error_type", "unknown"),
            "cost": round(sum(r["cost"].values()), 2),
        }
        for r in cursor
    ]
