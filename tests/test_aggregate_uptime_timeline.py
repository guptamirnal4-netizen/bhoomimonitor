"""aggregate.compute_uptime_timeline - the fine-grained (per-event, not
daily-rollup) uptime history that answers "was it down at some specific
time", plus outage-window detection. Uses conftest.FakeCollection - no
real MongoDB needed."""

from datetime import datetime, timedelta, timezone

import aggregate
from conftest import FakeCollection

NOW = datetime(2026, 8, 14, 22, 0, tzinfo=timezone.utc)  # "10pm today", matching your own example


def _event(hours_ago, status, service="RTC", district="Bagalkote", error_type=None):
    return {
        "timestamp": NOW - timedelta(hours=hours_ago),
        "district": district, "service": service, "status": status,
        "error_type": error_type, "latency_ms": 5000, "retry_count": 0,
        "cost": {"proxy": 0.0, "captcha": 0.0, "compute": 0.0},
    }


def _seed(events, *docs):
    for d in docs:
        events.insert_one(d)


def test_events_returned_chronologically_within_window(monkeypatch):
    monkeypatch.setattr(aggregate, "_since_hours", lambda hours: NOW - timedelta(hours=hours))
    events = FakeCollection()
    _seed(events, _event(10, "success"), _event(5, "fail"), _event(1, "success"))

    result = aggregate.compute_uptime_timeline(events, hours=48)
    timestamps = [e["timestamp"] for e in result["events"]]
    assert timestamps == sorted(timestamps)  # oldest first
    assert len(result["events"]) == 3


def test_events_outside_window_excluded(monkeypatch):
    monkeypatch.setattr(aggregate, "_since_hours", lambda hours: NOW - timedelta(hours=hours))
    events = FakeCollection()
    _seed(events, _event(100, "success"), _event(1, "success"))  # 100h ago is outside a 48h window

    result = aggregate.compute_uptime_timeline(events, hours=48)
    assert len(result["events"]) == 1


def test_service_filter_excludes_unrelated_activity(monkeypatch):
    monkeypatch.setattr(aggregate, "_since_hours", lambda hours: NOW - timedelta(hours=hours))
    events = FakeCollection()
    _seed(events, _event(2, "success", service="RTC"), _event(1, "fail", service="Mutation"))

    result = aggregate.compute_uptime_timeline(events, hours=48, service="RTC")
    assert len(result["events"]) == 1
    assert result["events"][0]["service"] == "RTC"


def test_this_is_the_exact_scenario_you_described(monkeypatch):
    # "at 10pm today a person can know that yesterday at 4am this
    # website was down" - a single fail sandwiched between successes,
    # ~18 hours before NOW.
    monkeypatch.setattr(aggregate, "_since_hours", lambda hours: NOW - timedelta(hours=hours))
    events = FakeCollection()
    _seed(
        events,
        _event(18.5, "success"),
        _event(18, "fail", error_type="timeout"),
        _event(17.5, "success"),
    )

    result = aggregate.compute_uptime_timeline(events, hours=48, service="RTC")
    assert len(result["outages"]) == 1
    outage = result["outages"][0]
    assert outage["count"] == 1
    assert outage["reasons"] == ["timeout"]
    assert "ongoing" not in outage  # it recovered - not still down


def test_ongoing_outage_flagged_when_the_latest_event_is_still_failing(monkeypatch):
    monkeypatch.setattr(aggregate, "_since_hours", lambda hours: NOW - timedelta(hours=hours))
    events = FakeCollection()
    _seed(events, _event(1, "success"), _event(0.5, "fail", error_type="session_expired"))

    result = aggregate.compute_uptime_timeline(events, hours=48)
    assert len(result["outages"]) == 1
    assert result["outages"][0]["ongoing"] is True


def test_consecutive_failures_collapse_into_one_outage_window(monkeypatch):
    monkeypatch.setattr(aggregate, "_since_hours", lambda hours: NOW - timedelta(hours=hours))
    events = FakeCollection()
    _seed(
        events,
        _event(3, "fail", error_type="timeout"),
        _event(2.5, "fail", error_type="timeout"),
        _event(2, "fail", error_type="session_expired"),
        _event(1, "success"),
    )
    result = aggregate.compute_uptime_timeline(events, hours=48)
    assert len(result["outages"]) == 1
    outage = result["outages"][0]
    assert outage["count"] == 3
    assert outage["reasons"] == ["session_expired", "timeout"]  # both reasons captured, sorted


def test_no_outages_when_everything_succeeded(monkeypatch):
    monkeypatch.setattr(aggregate, "_since_hours", lambda hours: NOW - timedelta(hours=hours))
    events = FakeCollection()
    _seed(events, _event(2, "success"), _event(1, "success"))
    result = aggregate.compute_uptime_timeline(events, hours=48)
    assert result["outages"] == []
