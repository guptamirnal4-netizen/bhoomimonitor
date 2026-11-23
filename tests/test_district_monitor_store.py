"""district_monitor_store.py - insert-only check log + incident open/
close lifecycle, using conftest.FakeCollection (no real MongoDB)."""

from datetime import datetime, timedelta, timezone

from conftest import FakeCollection
from district_monitor_store import DistrictMonitorStore

SCHED = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)


def _store():
    return DistrictMonitorStore(checks=FakeCollection(), incidents=FakeCollection())


def test_every_check_is_logged_even_when_up():
    store = _store()
    store.record_check("Bagalkote", "2", "RTC-Reachability", "check", "up", scheduled_for=SCHED)
    assert store.checks.count_documents({}) == 1


def test_down_check_opens_a_new_incident():
    store = _store()
    store.record_check("Bagalkote", "2", "RTC-Reachability", "check", "down", scheduled_for=SCHED,
                        reason="timeout", timestamp=SCHED)
    assert store.incidents.count_documents({"district": "Bagalkote", "status": "open"}) == 1


def test_consecutive_down_checks_do_not_open_a_second_incident():
    store = _store()
    t1 = SCHED
    t2 = SCHED + timedelta(minutes=30)
    store.record_check("Bagalkote", "2", "RTC-Reachability", "check", "down",
                        scheduled_for=t1, timestamp=t1, reason="timeout")
    store.record_check("Bagalkote", "2", "RTC-Reachability", "check", "down",
                        scheduled_for=t2, timestamp=t2, reason="timeout")
    assert store.incidents.count_documents({"district": "Bagalkote"}) == 1
    incident = store.incidents.find_one({"district": "Bagalkote"})
    assert incident["check_count_during_incident"] == 2
    assert incident["status"] == "open"


def test_up_after_down_closes_the_incident_with_correct_duration():
    store = _store()
    start = SCHED
    recovered = SCHED + timedelta(minutes=27)
    store.record_check("Ghaziabad", "99", "RTC-Reachability", "check", "down",
                        scheduled_for=start, timestamp=start, reason="Network/API failure")
    store.record_check("Ghaziabad", "99", "RTC-Reachability", "check", "up",
                        scheduled_for=recovered, timestamp=recovered)

    incident = store.incidents.find_one({"district": "Ghaziabad"})
    assert incident["status"] == "resolved"
    assert incident["duration_minutes"] == 27.0

    # the recovery check itself carries the downtime summary (your worked example)
    recovery_check = list(store.checks.find({"district": "Ghaziabad"}))[-1]
    assert recovery_check["downtime_start"] == start
    assert recovery_check["downtime_end"] == recovered
    assert recovery_check["downtime_duration_minutes"] == 27.0


def test_check_error_neither_opens_nor_closes_an_incident():
    store = _store()
    store.record_check("Bagalkote", "2", "RTC-Reachability", "check", "down",
                        scheduled_for=SCHED, timestamp=SCHED, reason="timeout")
    store.record_check("Bagalkote", "2", "RTC-Reachability", "check", "check_error",
                        scheduled_for=SCHED + timedelta(minutes=30), timestamp=SCHED + timedelta(minutes=30),
                        error_message="our code broke")
    # incident stays open - a check_error is not evidence of recovery
    incident = store.incidents.find_one({"district": "Bagalkote"})
    assert incident["status"] == "open"


def test_up_with_no_open_incident_reports_nothing_to_close():
    store = _store()
    doc = store.record_check("Bagalkote", "2", "RTC-Reachability", "check", "up", scheduled_for=SCHED)
    assert doc["downtime_start"] is None
    assert doc["downtime_end"] is None
