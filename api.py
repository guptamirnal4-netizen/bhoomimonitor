"""
api.py
------
Thin FastAPI wrapper around aggregate.py. One endpoint per dashboard
tab, plus a summary endpoint for the header strip.

Run locally:
    uvicorn api:app --reload --port 8000

Endpoints:
    GET /api/districts?days=1        -> district ledger tab
    GET /api/services?days=7         -> RTC/Mutation tab
    GET /api/cost?days=14            -> fetch cost tab
    GET /api/reliability?days=30     -> portal reliability tab
    GET /api/failures/recent?limit=20
    GET /api/summary                 -> header strip (uptime, cost today, fetch count)
    GET /api/rtc                     -> LIVE current RTC lookup (district/taluk/hobli/village/survey/hissa)

    -- 31-district reachability monitor (district_monitor.py, every 30 min) --
    GET /api/districts/monitor/snapshot?scheduled_for=<iso>  -> all 31 districts at one checkpoint (default: latest)
    GET /api/districts/monitor/history?start=<iso>&end=<iso> -> every checkpoint in range, each with all 31 districts
    GET /api/districts/monitor/uptime                        -> per-district uptime stats, all 31 always present
    GET /api/districts/monitor/uptime/{district}              -> one district's uptime stats
    GET /api/districts/monitor/report                         -> overall + per-district report
    GET /api/districts/monitor/missed-checks?start=<iso>&end=<iso> -> missed/partial checkpoints, delayed checks
    GET /api/districts/monitor/incidents?district=&status=    -> incident list (open+resolved), filterable
    GET /api/districts/monitor/checks?district=&check_type=&start=&end=&sort_field=&sort_dir=&limit=&offset=
                                                                -> flat, filterable/sortable check log - EVERY
                                                                   check (automatic + manual), nothing hidden
    POST /api/districts/monitor/check-now                      -> triggers an on-demand 31-district sweep NOW,
                                                                   reusing the exact same run_cycle() the
                                                                   scheduled job uses, tagged check_type="manual".
                                                                   Blocks until done (~20-100s) - cooldown-guarded.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient

import aggregate
import atlas_service
import district_monitor
import district_monitor_aggregate as dma
import rtc_verifier
from district_monitor_store import DistrictMonitorStore
from district_registry import DISTRICT_PORTAL_TEXT

log = logging.getLogger("bhoomi")

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "bhoomi")
CHECK_NOW_COOLDOWN_SECONDS = 60  # not requested, added anyway: a button a user can click
                                  # repeatedly shouldn't be able to spam the government
                                  # portal with redundant 31-district sweeps back to back.

app = FastAPI(title="Bhoomi Monitor API")

# Dashboard runs on a different origin during development (e.g. a
# Vite dev server or the Claude artifact sandbox) — loosen this to
# your actual frontend origin before deploying.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# tz_aware=True: see district_monitor_store.py's comment - without it,
# pymongo hands back naive UTC datetimes even though we write
# timezone-aware ones, breaking the aggregation layer's arithmetic.
_client = MongoClient(MONGO_URI, tz_aware=True)
_events = _client[DB_NAME]["fetch_events"]
_district_checks = _client[DB_NAME]["district_checks"]
_district_incidents = _client[DB_NAME]["district_incidents"]
# Reuses the same tz_aware collections above - one Mongo connection, not two.
_district_store = DistrictMonitorStore(checks=_district_checks, incidents=_district_incidents)
_last_manual_check_at: float | None = None  # monotonic seconds, for the cooldown guard


@app.get("/api/districts")
def districts(days: int = Query(1, ge=1, le=90)):
    return aggregate.compute_district_status(_events, days=days)


@app.get("/api/services")
def services(days: int = Query(7, ge=1, le=90)):
    return aggregate.compute_service_reliability(_events, days=days)


@app.get("/api/cost")
def cost(days: int = Query(14, ge=1, le=90)):
    return aggregate.compute_cost_trend(_events, days=days)


@app.get("/api/reliability")
def reliability(days: int = Query(30, ge=1, le=180)):
    return aggregate.compute_portal_uptime(_events, days=days)


@app.get("/api/reliability/timeline")
def reliability_timeline(
    hours: int = Query(48, ge=1, le=24 * 30, description="Lookback window in hours (max 30 days)"),
    service: str | None = Query(
        "RTC", description="Filter to one service (default 'RTC' - health_monitor.py's regular "
                            "30-min cadence). Pass an empty value to include every service."
    ),
):
    """Fine-grained (per-event, not daily-average) uptime history -
    answers "was the portal down at some specific time", e.g. checking
    at 10pm whether it was down at 4am yesterday - which /api/reliability's
    one-row-per-day average can't show. `outages` is the pre-computed
    list of contiguous down-windows (start/end/reasons), so the frontend
    doesn't have to re-derive them from the raw event list.
    """
    return aggregate.compute_uptime_timeline(_events, hours=hours, service=service or None)


@app.get("/api/failures/recent")
def failures(limit: int = Query(20, ge=1, le=200)):
    return aggregate.recent_failures(_events, limit=limit)


@app.get("/api/summary")
def summary():
    uptime = aggregate.compute_portal_uptime(_events, days=30)
    cost_trend = aggregate.compute_cost_trend(_events, days=1)
    districts_today = aggregate.compute_district_status(_events, days=1)

    return {
        "uptime_30d": uptime[-1]["uptime"] if uptime else 0.0,
        "cost_today": cost_trend[-1]["total"] if cost_trend else 0.0,
        "fetches_30d": sum(d["fetches"] for d in districts_today),
        "districts_ok": sum(1 for d in districts_today if d["status"] == "ok"),
        "districts_degraded": sum(1 for d in districts_today if d["status"] == "degraded"),
        "districts_fail": sum(1 for d in districts_today if d["status"] == "fail"),
    }


def require_api_key(x_api_key: str | None = Header(None)):
    """Guards the live-lookup routes used by the Atlas portal. Enforced
    only when ATLAS_API_KEY is set (always set it in production); left
    open for local development exactly as before."""
    expected = os.environ.get("ATLAS_API_KEY")
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def _atlas_call(fn, *args, **kwargs):
    """Maps atlas_service's input/capacity errors onto HTTP status codes.
    Portal-level failures are NOT HTTP errors - they come back as a 200
    with status="error" and a reasonCode, matching /api/rtc."""
    try:
        return fn(*args, **kwargs)
    except atlas_service.InvalidInput as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except atlas_service.LocationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except atlas_service.PortalBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": "5"})
    except Exception as exc:  # noqa: BLE001 - last-resort guard, same policy as /api/rtc
        log.exception("[ATLAS API] unexpected error in %s", getattr(fn, "__name__", fn))
        raise HTTPException(status_code=502, detail=f"Unexpected error calling the live portal: {exc}")


@app.get("/api/options", dependencies=[Depends(require_api_key)])
def options(
    level: str = Query(..., description="district | taluk | hobli | village | hissa"),
    district: str | None = None, taluk: str | None = None, hobli: str | None = None,
    village: str | None = None, survey_number: str | None = None,
):
    """Dropdown values for the cascading search form, exactly as the
    live portal lists them (district/taluk/hobli/village are cached 6h;
    hissa is live because it depends on the survey number)."""
    return _atlas_call(atlas_service.list_options, level, district, taluk, hobli, village, survey_number)


@app.get("/api/mutation", dependencies=[Depends(require_api_key)])
def mutation(
    district: str = Query(...), taluk: str = Query(...), hobli: str = Query(...),
    village: str = Query(...), survey_number: str = Query(..., description="digits only"),
    hissa_no: str | None = Query(None, description="Omit for every hissa under the survey"),
    from_year: int | None = Query(None, ge=1900, le=2100, description="Approved-date year, inclusive"),
    to_year: int | None = Query(None, ge=1900, le=2100, description="Approved-date year, inclusive"),
):
    """LIVE mutation (legal history) lookup: drives MR_MutationExtract.aspx
    and returns the mutation grid rows as the portal lists them. Always
    HTTP 200 for portal outcomes; `status` is ok | no_records | error."""
    return _atlas_call(atlas_service.fetch_mutation_history, district, taluk, hobli, village,
                       survey_number, hissa_no, from_year, to_year)


@app.get("/api/loan", dependencies=[Depends(require_api_key)])
def loan(
    district: str = Query(...), taluk: str = Query(...), hobli: str = Query(...),
    village: str = Query(...), survey_number: str = Query(..., description="digits only"),
    hissa_no: str = Query(..., description="Required: each mutation row costs 3 portal round-trips"),
):
    """LIVE loan / financial-charge lookup for one hissa: opens every
    mutation Preview for that hissa, extracts charges with loan_parser,
    reconciles creations against releases. `status` is
    ok | partial | no_records | error."""
    return _atlas_call(atlas_service.fetch_loan_history, district, taluk, hobli, village,
                       survey_number, hissa_no)


@app.get("/api/rtc", dependencies=[Depends(require_api_key)])
def rtc(
    district: str = Query(..., description="Dropdown text as it appears on the live portal, e.g. 'Bagalkote'"),
    taluk: str = Query(..., description="e.g. 'Mudhol'"),
    hobli: str = Query(..., description="e.g. 'LOKAPURA'"),
    village: str = Query(..., description="e.g. 'LOKAPURA'"),
    survey_number: str = Query(..., description="e.g. '56'"),
    hissa_no: str | None = Query(
        None, description="e.g. '1'. Omit to get every hissa under this survey instead of guessing one."
    ),
):
    """LIVE lookup - synchronously drives the Service2/ RTC form
    (district -> taluk -> hobli -> village -> survey -> Go -> Surnoc ->
    Hissa -> Period -> Year -> Fetch details, rtc_crawler.py/
    rtc_parser.py) and returns whatever the portal has right now. No
    CAPTCHA in this flow (confirmed live) - unlike
    rtc.karnataka.gov.in/service78/, which this API does NOT use.

    `hissa_no` is optional: give it and you get exactly that hissa back;
    omit it and you get every hissa under the survey (one "Fetch
    details" postback each) - this endpoint never silently picks one
    for you.

    `results` is always a list, one entry per hissa fetched:
        [{"hissa_no": "1", "currentStatus": {...}|null, "statusVerification": {...}}, ...]
    `success` is true if at least one hissa's fetch actually got data.
    Always returns HTTP 200 - a failed hissa carries `currentStatus:
    null` and an explicit reasonCode (SESSION_EXPIRED, RTC_REQUEST_FAILED,
    SOURCE_PARSE_FAILED, ...) in its own statusVerification rather than
    an opaque HTTP error (see reason_codes.py) - this project never
    hides uncertainty behind a status code alone.

    This is a synchronous, blocking call to a live government portal -
    each request takes several seconds per hissa (multiple sequential
    postbacks). Fine for one-off Postman lookups; omitting hissa_no on a
    survey with many hissas will take proportionally longer.

    Examples (Postman / curl):
        GET /api/rtc?district=Bagalkote&taluk=Mudhol&hobli=LOKAPURA&village=LOKAPURA&survey_number=56&hissa_no=1
        GET /api/rtc?district=Bagalkote&taluk=Mudhol&hobli=LOKAPURA&village=LOKAPURA&survey_number=56
    """
    try:
        results = rtc_verifier.verify_current_status(district, taluk, hobli, village, survey_number, hissa_no)
    except Exception as exc:  # noqa: BLE001 - last-resort guard; verify_current_status already
        # catches every known Bhoomi-specific failure mode itself. This only
        # catches a genuinely unexpected bug, and still answers honestly
        # rather than letting FastAPI turn it into an opaque 500.
        log.exception("[RTC API] unexpected error for %s/%s/%s/%s survey=%s hissa=%s",
                       district, taluk, hobli, village, survey_number, hissa_no)
        raise HTTPException(status_code=502, detail=f"Unexpected error calling the live RTC portal: {exc}")

    return {
        "success": any(r["currentStatus"] is not None for r in results),
        "query": {
            "district": district, "taluk": taluk, "hobli": hobli, "village": village,
            "survey_number": survey_number, "hissa_no": hissa_no,
        },
        "count": len(results),
        "results": results,
    }


# ---------- 31-district reachability monitor (district_monitor.py) ----------

@app.get("/api/districts/monitor/snapshot")
def district_monitor_snapshot(scheduled_for: str | None = Query(
    None, description="ISO timestamp of a specific checkpoint. Omit for the latest one recorded.")
):
    """All 31 canonical districts' record for ONE checkpoint - every
    district always present, even one with no check at that exact slot
    (marked not_checked, never silently omitted)."""
    if scheduled_for:
        try:
            cp = datetime.fromisoformat(scheduled_for)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid scheduled_for: {scheduled_for!r}")
    else:
        latest = list(_district_checks.find().sort("timestamp", -1).limit(1))
        if not latest:
            return {"scheduled_for": None, "districts": dma.compute_checkpoint_snapshot(_district_checks,
                     datetime.now(timezone.utc))}
        cp = latest[0]["scheduled_for"]
    return {"scheduled_for": cp.isoformat(), "districts": dma.compute_checkpoint_snapshot(_district_checks, cp)}


@app.get("/api/districts/monitor/history")
def district_monitor_history(
    start: str = Query(..., description="ISO start timestamp"),
    end: str = Query(..., description="ISO end timestamp"),
):
    """Every real checkpoint in [start, end], each with the full
    31-district snapshot - permanently available, nothing overwritten
    (district_checks is insert-only)."""
    try:
        start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=422, detail="start/end must be ISO timestamps")
    return {"checkpoints": dma.compute_historical_checkpoints(_district_checks, start_dt, end_dt)}


@app.get("/api/districts/monitor/uptime")
def district_monitor_uptime_all():
    """Per-district uptime stats for all 31 canonical districts, always -
    a district never checked still appears, marked not_checked."""
    return dma.compute_all_districts_uptime(_district_checks, _district_incidents)


@app.get("/api/districts/monitor/uptime/{district}")
def district_monitor_uptime_one(district: str):
    if district not in DISTRICT_PORTAL_TEXT:
        raise HTTPException(status_code=404, detail=f"Unknown district: {district!r}")
    return dma.compute_district_uptime(_district_checks, _district_incidents, district)


@app.get("/api/districts/monitor/report")
def district_monitor_report():
    """District-level + overall metrics (Sec. "Reporting")."""
    return dma.compute_overall_report(_district_checks, _district_incidents)


@app.get("/api/districts/monitor/missed-checks")
def district_monitor_missed_checks(
    start: str = Query(..., description="ISO start timestamp"),
    end: str = Query(..., description="ISO end timestamp"),
    interval_minutes: int = Query(30, ge=1, le=1440),
):
    """Missed checkpoints (no district checked at all), partial
    checkpoints (some but not all 31), and individually delayed checks."""
    try:
        start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=422, detail="start/end must be ISO timestamps")
    return dma.detect_missed_and_delayed_checks(_district_checks, start_dt, end_dt, interval_minutes)


@app.get("/api/districts/monitor/incidents")
def district_monitor_incidents(
    district: str | None = Query(None),
    status: str | None = Query(None, description="'open' or 'resolved'"),
    limit: int = Query(100, ge=1, le=1000),
):
    """Incident list, newest first - open (still down) and resolved
    (with the exact recovery time and duration)."""
    match = {}
    if district:
        match["district"] = district
    if status:
        match["status"] = status
    cursor = _district_incidents.find(match).sort("start_timestamp", -1).limit(limit)
    out = []
    for inc in cursor:
        row = dict(inc)
        row.pop("_id", None)
        row["start_timestamp"] = row["start_timestamp"].isoformat()
        row["detected_timestamp"] = row["detected_timestamp"].isoformat()
        row["end_timestamp"] = row["end_timestamp"].isoformat() if row["end_timestamp"] else None
        out.append(row)
    return {"incidents": out, "count": len(out)}


@app.get("/api/districts/monitor/checks")
def district_monitor_checks(
    district: str | None = Query(None),
    check_type: str | None = Query(None, description="'automatic' or 'manual'"),
    start: str | None = Query(None, description="ISO start timestamp, filters on checked_at"),
    end: str | None = Query(None, description="ISO end timestamp, filters on checked_at"),
    sort_field: str = Query("checked_at", description=f"one of {sorted(dma.SORTABLE_FIELDS)}"),
    sort_dir: int = Query(-1, description="1 = ascending, -1 = descending"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """The flat, sortable/filterable check log - EVERY check (automatic
    AND manual), nothing hidden by default (Sec. "Outage Log
    Visibility"). Distinct from /snapshot and /history, which are
    checkpoint-grid shaped - manual checks live here, not in that grid
    (see district_monitor.py's run_cycle() docstring for why)."""
    start_dt = end_dt = None
    try:
        if start:
            start_dt = datetime.fromisoformat(start)
        if end:
            end_dt = datetime.fromisoformat(end)
    except ValueError:
        raise HTTPException(status_code=422, detail="start/end must be ISO timestamps")

    try:
        return dma.query_checks(_district_checks, district=district, check_type=check_type,
                                 start=start_dt, end=end_dt, sort_field=sort_field, sort_dir=sort_dir,
                                 limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/api/districts/monitor/check-now")
def district_monitor_check_now():
    """Triggers an on-demand sweep of all 31 districts RIGHT NOW - calls
    the exact same district_monitor.run_cycle() the scheduled launchd
    job uses (never a duplicated/parallel implementation), tagged
    check_type="manual" so it's clearly distinguishable in the check log
    and never collides with the automatic 30-min checkpoint grid.

    This is a SYNCHRONOUS, blocking call - it does not return until all
    31 districts have been checked (typically ~20-30s, more under portal
    slowness), matching this project's existing pattern for other live
    portal calls (e.g. GET /api/rtc). The frontend's "Check Now" button
    should show a loading state for the duration of this request.

    Cooldown-guarded (not requested, added anyway): rejects a second
    trigger within CHECK_NOW_COOLDOWN_SECONDS of the last one, so a
    user clicking repeatedly can't queue up redundant 31-district
    sweeps against a live government portal.
    """
    global _last_manual_check_at
    now = time.monotonic()
    if _last_manual_check_at is not None and (now - _last_manual_check_at) < CHECK_NOW_COOLDOWN_SECONDS:
        wait_more = round(CHECK_NOW_COOLDOWN_SECONDS - (now - _last_manual_check_at), 1)
        raise HTTPException(
            status_code=429,
            detail=f"A manual check ran too recently - wait {wait_more}s before triggering another.",
        )
    _last_manual_check_at = now

    try:
        summary = district_monitor.run_cycle(_district_store, check_type="manual")
    except Exception as exc:  # noqa: BLE001 - run_cycle already isolates per-district failures;
        # this only catches a genuinely unexpected bug in the cycle itself.
        log.exception("[CHECK_NOW] unexpected error running manual district sweep")
        raise HTTPException(status_code=502, detail=f"Unexpected error running the check: {exc}")

    return summary
