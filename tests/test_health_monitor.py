"""health_monitor.py's per-probe logic - mocked network/Mongo (Sec. 31),
proving: a probe failure is recorded, not raised; the reasonCode->
error_type mapping matches schema.py's closed ERROR_TYPES vocabulary."""

from unittest.mock import MagicMock

import health_monitor
import rtc_verifier
from schema import ERROR_TYPES


def test_successful_probe_marks_success(monkeypatch):
    monkeypatch.setattr(
        rtc_verifier, "verify_current_status",
        lambda *a, **k: [{"hissa_no": "1", "currentStatus": {"land_id": "X"},
                           "statusVerification": {"reasonCode": "NONE"}}],
    )
    mon = MagicMock()
    tracker = MagicMock()
    mon.track.return_value.__enter__.return_value = tracker
    mon.track.return_value.__exit__.return_value = False

    result = health_monitor.check_one_probe(mon, health_monitor.PROBES[0])

    assert result["status"] == "success"
    tracker.mark_success.assert_called_once()
    tracker.error.assert_not_called()


def test_failed_probe_records_failure_not_raised(monkeypatch):
    monkeypatch.setattr(
        rtc_verifier, "verify_current_status",
        lambda *a, **k: [{"hissa_no": "1", "currentStatus": None,
                           "statusVerification": {"reasonCode": "RTC_REQUEST_FAILED"}}],
    )
    mon = MagicMock()
    tracker = MagicMock()
    mon.track.return_value.__enter__.return_value = tracker
    mon.track.return_value.__exit__.return_value = False

    result = health_monitor.check_one_probe(mon, health_monitor.PROBES[0])

    assert result["status"] == "fail"
    assert result["reasonCode"] == "RTC_REQUEST_FAILED"
    tracker.mark_success.assert_not_called()
    tracker.error.assert_called_once()
    # the mapped error_type must be one schema.py actually accepts
    mapped = tracker.error.call_args[0][0]
    assert mapped in ERROR_TYPES


def test_probe_never_raises_even_on_unexpected_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("something genuinely unexpected")
    monkeypatch.setattr(rtc_verifier, "verify_current_status", boom)
    mon = MagicMock()
    mon.track.return_value.__enter__.side_effect = RuntimeError("something genuinely unexpected")

    result = health_monitor.check_one_probe(mon, health_monitor.PROBES[0])
    assert result["status"] == "fail"  # never propagates - a probe failure is data, not a crash


def test_run_cycle_summarizes_all_probes(monkeypatch):
    monkeypatch.setattr(
        health_monitor, "check_one_probe",
        lambda mon, probe: {"district": probe["district"], "status": "success"},
    )
    monkeypatch.setattr(health_monitor, "notify", lambda summary: None)
    summary = health_monitor.run_cycle(MagicMock())
    assert summary["probesRun"] == len(health_monitor.PROBES)
    assert summary["succeeded"] == len(health_monitor.PROBES)
    assert summary["failed"] == 0


def test_default_probe_count_is_at_most_two():
    # Sec. per your instruction: at most 1-2 live requests per cycle.
    assert 1 <= len(health_monitor.PROBES) <= 2
