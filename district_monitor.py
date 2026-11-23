"""
district_monitor.py
----------------------
The 31-district reachability sweep, run every 30 minutes. Separate from
health_monitor.py (which keeps doing its deeper, validated-record RTC
checks for the 2 districts with a known-good survey/hissa) - this one
runs district_check.py's lightweight check (select district, confirm
taluk list populates) for all 31 canonical districts, sequentially, and
logs EVERY one via district_monitor_store.py - no monitoring activity
happens without a corresponding district_checks entry, even for a
district with nothing wrong.

Sequential, one fresh BhoomiSession per district (Sec. 22: no shared
session across independent checks, one district's failure can't corrupt
another's) - a full cycle is ~31 lightweight GET+POST pairs, expected to
take roughly 1-3 minutes under normal portal conditions, well inside the
30-minute cadence.

Run it:
    python district_monitor.py              # runs forever, one cycle every 30 min
    python district_monitor.py --once        # a single cycle, then exit (for testing/cron)

`scheduled_for` is the cycle's start time floored to the nearest 30-min
boundary (:00 or :30) - every district checked in the same cycle shares
the same scheduled_for value, so district_monitor_aggregate.py can group
"all 31 districts, one checkpoint" correctly regardless of the few
minutes the sweep itself takes to run.
"""

import argparse
import logging
import time
from datetime import datetime, timezone, timedelta

import district_check
from bhoomi_session import BhoomiSession
from district_monitor_aggregate import ALL_DISTRICTS
from district_monitor_schema import CHECK_INTERVAL_MINUTES
from district_monitor_store import DistrictMonitorStore

log = logging.getLogger("bhoomi.district_monitor")

SERVICE = "RTC-Reachability"
CHECK_PERFORMED = "district_dropdown_cascade (select district, confirm taluk list populates)"


def _floor_to_interval(dt: datetime, minutes: int = CHECK_INTERVAL_MINUTES) -> datetime:
    discard = timedelta(minutes=dt.minute % minutes, seconds=dt.second, microseconds=dt.microsecond)
    return dt - discard


def run_cycle(store: DistrictMonitorStore, check_type: str = "automatic") -> dict:
    """Sweeps all 31 districts once. Used by BOTH the scheduled launchd
    job (check_type="automatic") and the manual "Check Now" API endpoint
    (check_type="manual") - one shared implementation, never duplicated.

    scheduled_for for a manual check is its own exact timestamp, NOT the
    floor-to-30-min grid automatic checks use - an on-demand check
    doesn't belong to a pre-existing schedule slot, and reusing the grid
    could silently collide with (and hide, in compute_checkpoint_snapshot's
    one-doc-per-district-per-slot view) an automatic check that landed in
    the same 30-min window."""
    now = datetime.now(timezone.utc)
    scheduled_for = now if check_type == "manual" else _floor_to_interval(now)
    results = []
    for district in ALL_DISTRICTS:
        session = BhoomiSession(district_check.BASE_URL)  # fresh, isolated session per district
        r = district_check.check_district_reachability(district, session=session)
        store.record_check(
            district=district, district_code=r["district_code"], service=SERVICE,
            check_performed=CHECK_PERFORMED, status=r["status"], scheduled_for=scheduled_for,
            check_type=check_type, reason=r["reason"], error_message=r["error_message"],
            latency_ms=r["latency_ms"],
        )
        results.append(r["status"])
        log.info("[DISTRICT] %s -> %s%s", district, r["status"], f" ({r['reason']})" if r["reason"] else "")

    summary = {
        "scheduled_for": scheduled_for.isoformat(),
        "check_type": check_type,
        "total": len(results),
        "up": results.count("up"),
        "down": results.count("down"),
        "check_error": results.count("check_error"),
    }
    log.info("[DISTRICT_MONITOR] cycle summary: %s", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL_MINUTES * 60,
                         help=f"Seconds between cycles (default {CHECK_INTERVAL_MINUTES * 60} = 30 min)")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--db", default="bhoomi")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = DistrictMonitorStore(mongo_uri=args.mongo_uri, db_name=args.db)

    log.info("[DISTRICT_MONITOR] starting - %d districts per cycle, every %ds", len(ALL_DISTRICTS), args.interval)
    if args.once:
        run_cycle(store)
        return

    while True:
        try:
            run_cycle(store)
        except Exception:  # noqa: BLE001 - the scheduler loop itself must never die
            log.exception("[DISTRICT_MONITOR] cycle failed unexpectedly - continuing on schedule")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
