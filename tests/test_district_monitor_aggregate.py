"""district_monitor_aggregate.py - uptime formula, the always-31-rows
checkpoint snapshot, missed/delayed-check detection, overall report."""

from datetime import datetime, timedelta, timezone

import pytest

import district_monitor_aggregate as dma
from conftest import FakeCollection
from district_monitor_store import DistrictMonitorStore

T0 = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)


def _store():
    return DistrictMonitorStore(checks=FakeCollection(), incidents=FakeCollection())


def test_uptime_formula_matches_the_spec_exactly():
    # District monitored for exactly 120 minutes, with one 30-minute
    # downtime incident in the middle -> uptime = (120-30)/120*100 = 75%
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    down_start = T0 + timedelta(minutes=30)
    down_end = T0 + timedelta(minutes=60)
    store.record_check("Bagalkote", "2", "s", "c", "down", scheduled_for=down_start, timestamp=down_start,
                        reason="timeout")
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=down_end, timestamp=down_end)

    until = T0 + timedelta(minutes=120)
    stats = dma.compute_district_uptime(store.checks, store.incidents, "Bagalkote", until=until)
    assert stats["totalMonitoringMinutes"] == 120.0
    assert stats["totalDowntimeMinutes"] == 30.0
    assert stats["totalUptimeMinutes"] == 90.0
    assert stats["uptimePercent"] == 75.0
    assert stats["downtimePercent"] == 25.0


def test_ongoing_open_incident_counts_elapsed_time_not_ignored():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    down_start = T0 + timedelta(minutes=30)
    store.record_check("Bagalkote", "2", "s", "c", "down", scheduled_for=down_start, timestamp=down_start,
                        reason="timeout")
    # no recovery yet - still open at query time
    until = T0 + timedelta(minutes=60)
    stats = dma.compute_district_uptime(store.checks, store.incidents, "Bagalkote", until=until)
    assert stats["totalDowntimeMinutes"] == 30.0  # 30 min elapsed on the still-open incident
    assert stats["currentStatus"] == "down"


def test_never_checked_district_reports_honestly_not_zero_uptime():
    store = _store()
    stats = dma.compute_district_uptime(store.checks, store.incidents, "Kodagu")
    assert stats["currentStatus"] == "not_checked"
    assert stats["uptimePercent"] is None  # not 0% or 100% - genuinely unknown, never guessed
    assert stats["totalChecks"] == 0


def test_all_31_districts_always_present_even_with_zero_checks():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    rows = dma.compute_all_districts_uptime(store.checks, store.incidents, until=T0)
    assert len(rows) == 31
    districts = {r["district"] for r in rows}
    assert "Kodagu" in districts  # never checked in this test, still present


def test_checkpoint_snapshot_includes_all_31_marks_missing_as_not_checked():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    # only 1 of 31 districts actually checked at T0
    snapshot = dma.compute_checkpoint_snapshot(store.checks, T0)
    assert len(snapshot) == 31
    bagalkote_row = next(r for r in snapshot if r["district"] == "Bagalkote")
    assert bagalkote_row["status"] == "up"
    other_row = next(r for r in snapshot if r["district"] != "Bagalkote")
    assert other_row["status"] == "not_checked"
    assert other_row["timing_status"] == "missed"


def test_missed_checkpoint_detected_when_no_district_checked_at_all():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    # T0+30min never happened at all (e.g. laptop asleep) - T0+60min resumes
    t2 = T0 + timedelta(minutes=60)
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=t2, timestamp=t2)

    result = dma.detect_missed_and_delayed_checks(store.checks, T0, t2, interval_minutes=30)
    missed_times = result["missedCheckpoints"]
    assert (T0 + timedelta(minutes=30)).isoformat() in missed_times


def test_partial_checkpoint_detected_when_some_but_not_all_districts_checked():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    store.record_check("Belagavi", "1", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    # only 2 of 31 districts checked at T0 - the cycle started but didn't finish
    result = dma.detect_missed_and_delayed_checks(store.checks, T0, T0, interval_minutes=30)
    assert len(result["partialCheckpoints"]) == 1
    assert result["partialCheckpoints"][0]["districtsChecked"] == 2
    assert result["partialCheckpoints"][0]["districtsExpected"] == 31


def test_delayed_check_detected_and_reported():
    store = _store()
    late = T0 + timedelta(minutes=8)
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=late)
    result = dma.detect_missed_and_delayed_checks(store.checks, T0, T0, interval_minutes=30)
    assert len(result["delayedChecks"]) == 1
    assert result["delayedChecks"][0]["district"] == "Bagalkote"
    assert result["delayedChecks"][0]["delay_minutes"] == 8.0


def test_highest_downtime_district_not_named_when_everything_is_zero():
    # every checked district has 0 downtime - naming one as "highest"
    # would misleadingly imply a problem that doesn't exist.
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    store.record_check("Belagavi", "1", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    report = dma.compute_overall_report(store.checks, store.incidents, until=T0)
    assert report["districtWithHighestDowntime"] is None


def test_overall_report_aggregates_across_districts():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    store.record_check("Belagavi", "1", "s", "c", "down", scheduled_for=T0, timestamp=T0, reason="timeout")

    report = dma.compute_overall_report(store.checks, store.incidents, until=T0 + timedelta(minutes=30))
    assert report["districtsMonitored"] == 2
    assert report["districtsNeverChecked"] == 29
    assert report["totalIncidents"] == 1
    assert report["mostFrequentFailureReason"] == "timeout"
    assert report["totalChecksPerformed"] == 2


# ---------- query_checks() - the flat, filterable/sortable check log ----------

def test_query_checks_returns_every_check_nothing_hidden_by_default():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0, check_type="automatic")
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0 + timedelta(minutes=5),
                        check_type="manual")
    result = dma.query_checks(store.checks)
    assert result["total"] == 2
    assert len(result["checks"]) == 2


def test_query_checks_filters_by_district():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    store.record_check("Belagavi", "1", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    result = dma.query_checks(store.checks, district="Belagavi")
    assert result["total"] == 1
    assert result["checks"][0]["district"] == "Belagavi"


def test_query_checks_filters_by_check_type():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0, check_type="automatic")
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0 + timedelta(minutes=1),
                        check_type="manual")
    result = dma.query_checks(store.checks, check_type="manual")
    assert result["total"] == 1
    assert result["checks"][0]["check_type"] == "manual"


def test_query_checks_filters_by_date_range():
    store = _store()
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=T0, timestamp=T0)
    later = T0 + timedelta(days=1)
    store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=later, timestamp=later)
    result = dma.query_checks(store.checks, start=T0 + timedelta(hours=1), end=T0 + timedelta(days=2))
    assert result["total"] == 1


def test_query_checks_sorts_and_paginates():
    store = _store()
    for i in range(5):
        t = T0 + timedelta(minutes=i)
        store.record_check("Bagalkote", "2", "s", "c", "up", scheduled_for=t, timestamp=t)
    page1 = dma.query_checks(store.checks, sort_field="checked_at", sort_dir=1, limit=2, offset=0)
    page2 = dma.query_checks(store.checks, sort_field="checked_at", sort_dir=1, limit=2, offset=2)
    assert page1["total"] == 5
    assert len(page1["checks"]) == 2
    assert page1["checks"][0]["checked_at"] < page1["checks"][1]["checked_at"]
    assert page1["checks"][-1]["checked_at"] < page2["checks"][0]["checked_at"]  # no overlap between pages


def test_query_checks_rejects_unsortable_field():
    store = _store()
    with pytest.raises(ValueError):
        dma.query_checks(store.checks, sort_field="not_a_real_field")
