"""
seed.py
-------
Populates fetch_events with realistic sample data so you can run the
API and aggregation against a real Mongo instance before the actual
scraper is wired in. Not meant for production — just a fast way to
sanity-check api.py end to end.

Usage:
    python seed.py                      # 30 days, default district skew
    python seed.py --days 7 --wipe      # 7 days, clear collection first
"""

import argparse
import random
from datetime import datetime, timedelta, timezone

from pymongo import MongoClient

from schema import DISTRICTS, ERROR_TYPES, new_fetch_event


def gen_events(days: int, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    events = []
    now = datetime.now(timezone.utc)

    # Give each district a fixed "difficulty" so the data has a stable
    # shape across runs instead of pure noise — some districts are
    # just harder to scrape (heavier CAPTCHA, flakier regional servers).
    difficulty = {d: rng.uniform(0.0, 0.4) for d in DISTRICTS}

    for day_offset in range(days):
        day = now - timedelta(days=day_offset)
        for district in DISTRICTS:
            base_fail_rate = 0.06 + difficulty[district]
            n_rtc = rng.randint(15, 60)
            n_mut = rng.randint(10, 45)

            for service, n, extra_fail in (("RTC", n_rtc, 0.0), ("Mutation", n_mut, 0.08)):
                for _ in range(n):
                    ts = day - timedelta(
                        hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
                    )
                    fail_rate = min(0.9, base_fail_rate + extra_fail)
                    is_fail = rng.random() < fail_rate

                    if is_fail:
                        error_type = rng.choices(
                            ERROR_TYPES,
                            weights=[3, 4 if service == "Mutation" else 1, 2, 2, 2, 1, 1],
                        )[0]
                        cost = {
                            "proxy": round(rng.uniform(0.2, 0.5), 2),
                            "captcha": round(rng.uniform(1.5, 3.0), 2) if error_type == "captcha_fail" else 0.0,
                            "compute": round(rng.uniform(0.02, 0.08), 3),
                        }
                        events.append(new_fetch_event(
                            district=district, service=service, status="fail",
                            error_type=error_type, latency_ms=rng.randint(2000, 12000),
                            cost=cost, retry_count=rng.randint(0, 3), timestamp=ts,
                        ))
                    else:
                        cost = {
                            "proxy": round(rng.uniform(0.15, 0.35), 2),
                            "captcha": round(rng.uniform(0.0, 1.2), 2),
                            "compute": round(rng.uniform(0.02, 0.06), 3),
                        }
                        events.append(new_fetch_event(
                            district=district, service=service, status="success",
                            latency_ms=rng.randint(800, 4000), cost=cost,
                            retry_count=0, hissa_key=f"BLK-{ts.year}-{rng.randint(1000, 9999)}",
                            timestamp=ts,
                        ))
    return events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--db", default="bhoomi")
    parser.add_argument("--wipe", action="store_true", help="Clear fetch_events before seeding")
    args = parser.parse_args()

    client = MongoClient(args.mongo_uri)
    events = client[args.db]["fetch_events"]

    if args.wipe:
        deleted = events.delete_many({}).deleted_count
        print(f"Wiped {deleted} existing events")

    docs = gen_events(args.days)
    events.insert_many(docs)
    print(f"Inserted {len(docs)} events across {args.days} days into {args.db}.fetch_events")


if __name__ == "__main__":
    main()
