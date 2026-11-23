"""
schema.py
---------
Defines the shape of every document the scraper writes, plus the
constant vocabularies (services, statuses, error types) the rest of
the monitor relies on. Nothing here talks to Mongo directly — it's
the single source of truth so logger.py, aggregate.py, and api.py
never disagree about field names.

Collection: fetch_events
One document per fetch attempt (one district + one service = one attempt).

{
    "_id": ObjectId(...),
    "timestamp": datetime(UTC),        # when the attempt finished
    "district": "Yadgir",              # must be in DISTRICTS
    "service": "RTC" | "Mutation",
    "status": "success" | "fail",
    "error_type": None | one of ERROR_TYPES,   # None when status == success
    "latency_ms": 4210,
    "retry_count": 0,
    "cost": {
        "proxy": 0.35,      # INR, cost of the proxy hop(s) used
        "captcha": 0.0,     # INR, cost of CAPTCHA solving (0 if not triggered)
        "compute": 0.02,    # INR, apportioned compute/infra cost for this attempt
    },
    "hissa_key": "BLK-2024-0091" | None,   # set on success, links to the fetched record
}
"""

from datetime import datetime, timezone

DISTRICTS = [
    "Bagalkote", "Ballari", "Belagavi", "Bengaluru Rural", "Bengaluru Urban",
    "Bidar", "Chamarajanagara", "Chikkaballapura", "Chikkamagaluru",
    "Chitradurga", "Dakshina Kannada", "Davanagere", "Dharwad", "Gadag",
    "Hassan", "Haveri", "Kalaburagi", "Kodagu", "Kolar", "Koppal", "Mandya",
    "Mysuru", "Raichur", "Ramanagara", "Shivamogga", "Tumakuru", "Udupi",
    "Uttara Kannada", "Vijayapura", "Yadgir", "Vijayanagara",
]

SERVICES = ["RTC", "Mutation"]

STATUSES = ["success", "fail"]

# Keep this list closed and short — every new error type must be added
# here deliberately, so the dashboard's failure-reason breakdown never
# silently gains an "unknown" bucket that's actually a typo.
ERROR_TYPES = [
    "timeout",              # portal did not respond within the deadline
    "captcha_fail",         # CAPTCHA solve exhausted retries
    "schema_mismatch",      # page structure changed, parser couldn't map fields
    "session_expired",      # ASP.NET session/viewstate died mid-postback
    "server_error",         # portal returned 5xx
    "rate_limited",         # portal or proxy throttled the request
    "not_found",            # valid response, but no record exists for the query
]

# District status thresholds, blended 55% RTC / 45% Mutation.
# Centralized here so the dashboard, alerts, and any future job all
# agree on what "ok" means.
STATUS_THRESHOLDS = {"ok": 88, "degraded": 70}  # below "degraded" = fail
SERVICE_WEIGHTS = {"RTC": 0.55, "Mutation": 0.45}


def new_fetch_event(
    district: str,
    service: str,
    status: str,
    latency_ms: int,
    cost: dict,
    error_type: str | None = None,
    retry_count: int = 0,
    hissa_key: str | None = None,
    timestamp: datetime | None = None,
) -> dict:
    """Build a validated fetch_event document. Raises ValueError on bad input
    so a typo in the scraper fails loudly at write time, not silently at
    dashboard time."""
    if district not in DISTRICTS:
        raise ValueError(f"Unknown district: {district!r}")
    if service not in SERVICES:
        raise ValueError(f"Unknown service: {service!r}")
    if status not in STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    if status == "fail" and error_type not in ERROR_TYPES:
        raise ValueError(f"fail status requires a valid error_type, got {error_type!r}")
    if status == "success" and error_type is not None:
        raise ValueError("success status must not carry an error_type")

    for key in ("proxy", "captcha", "compute"):
        if key not in cost:
            raise ValueError(f"cost dict missing {key!r}")

    return {
        "timestamp": timestamp or datetime.now(timezone.utc),
        "district": district,
        "service": service,
        "status": status,
        "error_type": error_type,
        "latency_ms": latency_ms,
        "retry_count": retry_count,
        "cost": {
            "proxy": round(cost.get("proxy", 0.0), 4),
            "captcha": round(cost.get("captcha", 0.0), 4),
            "compute": round(cost.get("compute", 0.0), 4),
        },
        "hissa_key": hissa_key,
    }
