"""
health_monitor.py
--------------------
"Is the Bhoomi portal working right now?" - a small, conservative
background check, run on a schedule (default every 30 minutes), NOT a
data-collection crawl. Per your instruction: at most 1-2 live requests
per cycle, never more.

Every check is logged through the EXISTING MonitorLogger (logger.py)
into fetch_events under service="RTC" - the same collection
aggregate.py/api.py already read for the dashboard's district status,
portal uptime trend, and failure log. That means this script is the
thing that finally makes those tabs show real signal instead of
seed.py's fake data, with zero changes to the dashboard/aggregation
code itself.

Run it:
    python health_monitor.py              # runs forever, one cycle every 30 min
    python health_monitor.py --once        # a single cycle, then exit (for testing/cron)
    python health_monitor.py --interval 60 # override the interval (seconds) - testing only,
                                            # don't run this against the live portal in production

Deploying it continuously is up to you - a systemd/launchd unit, a
`nohup python health_monitor.py &`, or a cron entry calling it with
`--once` every 30 minutes are all reasonable; this script doesn't
assume which.

FUTURE EXTENSION POINT (not built yet, per your note about Slack/
WhatsApp/Telegram later): `notify()` below is called once per cycle
with a structured summary dict. Right now it only logs. Wiring a chat
integration later means filling in THIS function - nothing else in the
script needs to change.
"""

import argparse
import logging
import time
from datetime import datetime, timezone

import rtc_verifier
from crawler import _track  # same district_names normalization crawler.py already uses for mon.track()
from logger import MonitorLogger

log = logging.getLogger("bhoomi.health")

DEFAULT_INTERVAL_SECONDS = 30 * 60

# Two probes, two different districts (Sec. 22/your instruction: 1-2
# requests max per cycle, and check more than one district) - each was
# live-validated to resolve cleanly before being added here (zero-
# assumption: never wire an unverified record into a recurring job).
# A second, geographically distinct district catches a regional outage
# a single-district probe would miss.
PROBES = [
    {"district": "Bagalkote", "taluk": "Mudhol", "hobli": "LOKAPURA", "village": "LOKAPURA",
     "survey_number": "56", "hissa_no": "1"},
    {"district": "BELAGAVI", "taluk": "Chikkodi", "hobli": "CHIKKODI", "village": "CHIKKODI",
     "survey_number": "1", "hissa_no": "3"},
]

# Best-effort mapping from this project's reasonCodes (reason_codes.py)
# to schema.py's closed ERROR_TYPES vocabulary - approximate by nature,
# since the two vocabularies were designed for different layers (this
# project's extraction-outcome codes vs. the dashboard's fetch-attempt
# codes), but good enough to make failures show up correctly bucketed
# in the existing "RTC/Mutation" failure-reason breakdown.
REASON_TO_ERROR_TYPE = {
    "SESSION_EXPIRED": "session_expired",
    "SOURCE_PARSE_FAILED": "schema_mismatch",
    "RTC_REQUEST_FAILED": "timeout",
}


def notify(summary: dict):
    """Extension point for Slack/Telegram/WhatsApp/another dashboard
    later - today it just logs. Replace/extend this function when
    that's ready; run_cycle() doesn't need to change."""
    log.info("[HEALTH] cycle summary: %s", summary)


def check_one_probe(mon: MonitorLogger, probe: dict) -> dict:
    """Runs exactly one live RTC lookup for `probe`, logs it via
    MonitorLogger, and returns a small result dict for the cycle
    summary. Never raises - a probe failure is data, not a crash.

    Uses crawler.py's `_track()` (not mon.track() directly) so the same
    district_names.py normalization applies here too - the live portal's
    dropdown text ("BELAGAVI") and schema.py's curated DISTRICTS
    vocabulary ("Belagavi") don't always match casing, and mon.track()
    raises ValueError on an unrecognized district otherwise."""
    district = probe["district"]
    try:
        with _track(mon, district, "RTC") as t:
            results = rtc_verifier.verify_current_status(
                probe["district"], probe["taluk"], probe["hobli"], probe["village"],
                probe["survey_number"], probe.get("hissa_no"),
            )
            result = results[0]
            if result["currentStatus"] is None:
                reason = result["statusVerification"].get("reasonCode", "UNKNOWN")
                t.error(REASON_TO_ERROR_TYPE.get(reason, "server_error"))
                return {"district": district, "status": "fail", "reasonCode": reason}
            t.mark_success()
            t.hissa_key(f"{probe['village']}|{probe['survey_number']}|{result['hissa_no']}")
            return {"district": district, "status": "success"}
    except Exception as exc:  # noqa: BLE001 - a probe must never take down the whole monitor loop
        log.exception("[HEALTH] unexpected error probing %s", district)
        return {"district": district, "status": "fail", "reasonCode": "UNKNOWN", "error": str(exc)}


def run_cycle(mon: MonitorLogger):
    started = datetime.now(timezone.utc)
    outcomes = [check_one_probe(mon, probe) for probe in PROBES]
    summary = {
        "startedAt": started.isoformat(),
        "probesRun": len(outcomes),
        "succeeded": sum(1 for o in outcomes if o["status"] == "success"),
        "failed": sum(1 for o in outcomes if o["status"] == "fail"),
        "outcomes": outcomes,
    }
    notify(summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS,
                         help=f"Seconds between cycles (default {DEFAULT_INTERVAL_SECONDS} = 30 min)")
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--db", default="bhoomi")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    mon = MonitorLogger(mongo_uri=args.mongo_uri, db_name=args.db)

    log.info("[HEALTH] starting - %d probe(s) per cycle, every %ds", len(PROBES), args.interval)
    if args.once:
        run_cycle(mon)
        return

    while True:
        try:
            run_cycle(mon)
        except Exception:  # noqa: BLE001 - the scheduler loop itself must never die
            log.exception("[HEALTH] cycle failed unexpectedly - continuing on schedule")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
