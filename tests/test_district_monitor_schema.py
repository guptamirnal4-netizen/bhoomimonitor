"""district_monitor_schema.py - check/incident document builders."""

from datetime import datetime, timedelta, timezone

import pytest

import district_monitor_schema as dms


def test_new_district_check_rejects_unknown_status():
    with pytest.raises(ValueError):
        dms.new_district_check("Bagalkote", "2", "RTC-Reachability", "check", "unreachable",
                                scheduled_for=datetime.now(timezone.utc))


def test_uptime_downtime_label_matches_your_example_wording():
    assert dms.uptime_downtime_label("up") == "Uptime"
    assert dms.uptime_downtime_label("down") == "Downtime"
    assert dms.uptime_downtime_label("check_error") == "Unknown"


def test_check_type_defaults_to_automatic():
    doc = dms.new_district_check("Bagalkote", "2", "s", "c", "up", scheduled_for=datetime.now(timezone.utc))
    assert doc["check_type"] == "automatic"


def test_check_type_rejects_unknown_value():
    with pytest.raises(ValueError):
        dms.new_district_check("Bagalkote", "2", "s", "c", "up", scheduled_for=datetime.now(timezone.utc),
                                check_type="scheduled")  # not "automatic"/"manual"


def test_manual_check_has_no_delay_or_timing_status_penalty():
    # a manual check is on-demand by definition - "delayed relative to a
    # 30-min grid slot" doesn't apply to it, even if scheduled_for is far
    # from the actual timestamp.
    scheduled = datetime(2026, 8, 14, 11, 30, tzinfo=timezone.utc)
    doc = dms.new_district_check("Bagalkote", "2", "s", "c", "up", scheduled_for=scheduled,
                                  timestamp=scheduled + timedelta(hours=3), check_type="manual")
    assert doc["timing_status"] == "on_time"
    assert doc["delay_minutes"] is None


def test_checked_at_alias_matches_timestamp():
    ts = datetime(2026, 8, 14, 11, 30, tzinfo=timezone.utc)
    doc = dms.new_district_check("Bagalkote", "2", "s", "c", "up", scheduled_for=ts, timestamp=ts)
    assert doc["checked_at"] == doc["timestamp"] == ts


def test_on_time_vs_delayed_classification():
    scheduled = datetime(2026, 8, 14, 11, 30, tzinfo=timezone.utc)
    on_time = dms.new_district_check("Bagalkote", "2", "RTC-Reachability", "check", "up",
                                      scheduled_for=scheduled, timestamp=scheduled + timedelta(minutes=1))
    delayed = dms.new_district_check("Bagalkote", "2", "RTC-Reachability", "check", "up",
                                      scheduled_for=scheduled, timestamp=scheduled + timedelta(minutes=7))
    assert on_time["timing_status"] == "on_time"
    assert delayed["timing_status"] == "delayed"
    assert delayed["delay_minutes"] == 7.0


def test_action_taken_never_fabricates_remediation():
    # Sec. zero-assumption: we don't actually restart/remediate anything -
    # the default action_taken text must not imply otherwise.
    doc = dms.new_district_check("Bagalkote", "2", "RTC-Reachability", "check", "down",
                                  scheduled_for=datetime.now(timezone.utc))
    assert "restart" not in doc["action_taken"].lower()
    assert "automated monitoring only" in doc["action_taken"].lower()


def test_incident_lifecycle_open_then_close():
    start = datetime(2026, 8, 14, 10, 15, tzinfo=timezone.utc)
    incident = dms.new_incident("Ghaziabad", "99", start_timestamp=start, detected_timestamp=start,
                                 reason="Network/API failure", error_message="timeout")
    assert incident["status"] == "open"
    assert incident["root_cause"] is None  # never fabricated

    end = start + timedelta(minutes=27)
    closed = dms.close_incident(incident, end_timestamp=end)
    assert closed["status"] == "resolved"
    assert closed["duration_minutes"] == 27.0
    assert closed["start_timestamp"] == start  # original start never altered
    # matches your own worked example almost exactly (Ghaziabad, 27 min)
