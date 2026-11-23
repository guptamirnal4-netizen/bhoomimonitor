"""
run_test_case.py
-------------------
Phase 9: a small, controlled crawl against the live portal - the known
validation case only (Sec. 33), never a wider crawl. Confirms the whole
pipeline end to end: MR_MutationExtract.aspx navigation -> Preview
fetch/parse -> loan extraction -> reconciliation -> RTC stub -> Mongo
persistence -> checkpointing -> observability logging.

Usage:
    python run_test_case.py                       # BAGALKOTE/MUDHOL/LOKAPURA/LOKAPURA/56
    python run_test_case.py --debug                # + request-level debug logs (no cookies logged)
    python run_test_case.py --mongo-uri mongodb://localhost:27017 --db bhoomi
"""

import argparse
import json
import logging

from checkpoint_store import CheckpointStore
from crawler import crawl_survey
from land_schema import record_key
from logger import MonitorLogger
from persistence import LandRecordStore

TEST_CASE = {
    "district": "Bagalkote",   # dropdown text, case-insensitive match in crawler._find_option_value
    "taluk": "Mudhol",
    "hobli": "LOKAPURA",
    "village": "LOKAPURA",
    "survey_number": "56",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mongo-uri", default="mongodb://localhost:27017")
    parser.add_argument("--db", default="bhoomi")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    mon = MonitorLogger(mongo_uri=args.mongo_uri, db_name=args.db)
    ckpt = CheckpointStore(mongo_uri=args.mongo_uri, db_name=args.db)
    store = LandRecordStore(mongo_uri=args.mongo_uri, db_name=args.db)

    print(f"\n=== Crawling test case: {TEST_CASE} ===\n")
    result = crawl_survey(
        district=TEST_CASE["district"], taluk=TEST_CASE["taluk"],
        hobli=TEST_CASE["hobli"], village=TEST_CASE["village"],
        survey_number=TEST_CASE["survey_number"],
        mon=mon, ckpt=ckpt, store=store, debug=args.debug,
    )

    print("\n=== Crawl stats ===")
    print(json.dumps(result.as_dict(), indent=2, default=str))

    print("\n=== Persisted land_records for this survey ===")
    for hissa in ["1", "2", "3", "4"]:
        key = record_key(TEST_CASE["district"], TEST_CASE["taluk"], TEST_CASE["hobli"],
                          TEST_CASE["village"], TEST_CASE["survey_number"], hissa)
        doc = store.get_land_record(key)
        if doc:
            doc.pop("_id", None)
            print(f"\n--- {key} ---")
            print(json.dumps(doc, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
