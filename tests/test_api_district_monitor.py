"""GET /api/districts/monitor/* - the 31-district reachability monitor
endpoints. Mocks district_monitor_aggregate so this never touches the
network or a real MongoDB read path (Sec. 31)."""

from datetime import datetime, timezone

import api
import district_monitor_aggregate as dma
from conftest import FakeCollection
from fastapi.testclient import TestClient

client = TestClient(api.app)


def test_snapshot_uses_latest_checkpoint_when_none_given(monkeypatch):
    fake_rows = [{"district": "Bagalkote", "status": "up"}]
    monkeypatch.setattr(dma, "compute_checkpoint_snapshot", lambda checks, cp: fake_rows)

    fake_checks = FakeCollection()
    fake_checks.insert_one({"scheduled_for": datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc),
                             "timestamp": datetime(2026, 8, 15, 4, 0, 5, tzinfo=timezone.utc)})
    monkeypatch.setattr(api, "_district_checks", fake_checks)

    r = client.get("/api/districts/monitor/snapshot")
    assert r.status_code == 200
    assert r.json()["scheduled_for"] == "2026-08-15T04:00:00+00:00"
    assert r.json()["districts"] == fake_rows


def test_snapshot_accepts_explicit_checkpoint(monkeypatch):
    captured = {}
    monkeypatch.setattr(dma, "compute_checkpoint_snapshot", lambda checks, cp: captured.setdefault("cp", cp) or [])
    r = client.get("/api/districts/monitor/snapshot", params={"scheduled_for": "2026-08-14T10:00:00+00:00"})
    assert r.status_code == 200
    assert captured["cp"].isoformat() == "2026-08-14T10:00:00+00:00"


def test_snapshot_rejects_invalid_timestamp():
    r = client.get("/api/districts/monitor/snapshot", params={"scheduled_for": "not-a-date"})
    assert r.status_code == 422


def test_history_requires_start_and_end():
    r = client.get("/api/districts/monitor/history")
    assert r.status_code == 422


def test_history_calls_aggregate_with_parsed_dates(monkeypatch):
    captured = {}

    def fake_hist(checks, start, end):
        captured["start"], captured["end"] = start, end
        return []

    monkeypatch.setattr(dma, "compute_historical_checkpoints", fake_hist)
    r = client.get("/api/districts/monitor/history", params={
        "start": "2026-08-14T00:00:00+00:00", "end": "2026-08-14T23:59:59+00:00",
    })
    assert r.status_code == 200
    assert captured["start"].year == 2026


def test_uptime_all_returns_aggregate_output_directly(monkeypatch):
    monkeypatch.setattr(dma, "compute_all_districts_uptime", lambda checks, incidents: [{"district": "Mandya"}])
    r = client.get("/api/districts/monitor/uptime")
    assert r.status_code == 200
    assert r.json() == [{"district": "Mandya"}]


def test_uptime_one_rejects_unknown_district():
    r = client.get("/api/districts/monitor/uptime/NotARealDistrict")
    assert r.status_code == 404


def test_uptime_one_known_district(monkeypatch):
    monkeypatch.setattr(dma, "compute_district_uptime", lambda checks, incidents, d: {"district": d})
    r = client.get("/api/districts/monitor/uptime/Bagalkote")
    assert r.status_code == 200
    assert r.json()["district"] == "Bagalkote"


def test_report_returns_aggregate_output(monkeypatch):
    monkeypatch.setattr(dma, "compute_overall_report", lambda checks, incidents: {"overallUptimePercent": 99.0})
    r = client.get("/api/districts/monitor/report")
    assert r.status_code == 200
    assert r.json()["overallUptimePercent"] == 99.0


def test_missed_checks_requires_start_and_end():
    r = client.get("/api/districts/monitor/missed-checks")
    assert r.status_code == 422


def test_missed_checks_calls_aggregate(monkeypatch):
    monkeypatch.setattr(
        dma, "detect_missed_and_delayed_checks",
        lambda checks, start, end, interval: {"missedCheckpoints": [], "partialCheckpoints": [], "delayedChecks": []},
    )
    r = client.get("/api/districts/monitor/missed-checks", params={
        "start": "2026-08-14T00:00:00+00:00", "end": "2026-08-14T23:59:59+00:00",
    })
    assert r.status_code == 200


# ---------- GET /api/districts/monitor/checks ----------

def test_checks_endpoint_returns_query_checks_output(monkeypatch):
    monkeypatch.setattr(dma, "query_checks", lambda checks, **kw: {"total": 1, "checks": [{"district": "Bagalkote"}]})
    r = client.get("/api/districts/monitor/checks")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_checks_endpoint_passes_filters_through(monkeypatch):
    captured = {}

    def fake_query(checks, **kw):
        captured.update(kw)
        return {"total": 0, "checks": []}

    monkeypatch.setattr(dma, "query_checks", fake_query)
    r = client.get("/api/districts/monitor/checks", params={
        "district": "Belagavi", "check_type": "manual", "sort_field": "district", "sort_dir": 1,
    })
    assert r.status_code == 200
    assert captured["district"] == "Belagavi"
    assert captured["check_type"] == "manual"
    assert captured["sort_field"] == "district"
    assert captured["sort_dir"] == 1


def test_checks_endpoint_rejects_invalid_sort_field(monkeypatch):
    def fake_query(checks, **kw):
        raise ValueError("Unsupported sort_field")
    monkeypatch.setattr(dma, "query_checks", fake_query)
    r = client.get("/api/districts/monitor/checks", params={"sort_field": "nope"})
    assert r.status_code == 422


def test_checks_endpoint_rejects_invalid_date():
    r = client.get("/api/districts/monitor/checks", params={"start": "not-a-date"})
    assert r.status_code == 422


# ---------- POST /api/districts/monitor/check-now ----------

def test_check_now_calls_run_cycle_tagged_manual(monkeypatch):
    monkeypatch.setattr(api, "_last_manual_check_at", None)
    captured = {}

    def fake_run_cycle(store, check_type="automatic"):
        captured["check_type"] = check_type
        return {"total": 31, "up": 31, "down": 0, "check_error": 0, "check_type": check_type}

    monkeypatch.setattr(api.district_monitor, "run_cycle", fake_run_cycle)
    r = client.post("/api/districts/monitor/check-now")
    assert r.status_code == 200
    assert captured["check_type"] == "manual"
    assert r.json()["check_type"] == "manual"


def test_check_now_cooldown_rejects_rapid_repeat_calls(monkeypatch):
    monkeypatch.setattr(api, "_last_manual_check_at", None)
    monkeypatch.setattr(api.district_monitor, "run_cycle", lambda store, check_type="automatic": {"total": 31})

    r1 = client.post("/api/districts/monitor/check-now")
    assert r1.status_code == 200

    r2 = client.post("/api/districts/monitor/check-now")
    assert r2.status_code == 429


def test_check_now_unexpected_error_returns_502_not_opaque_500(monkeypatch):
    monkeypatch.setattr(api, "_last_manual_check_at", None)

    def boom(store, check_type="automatic"):
        raise RuntimeError("something genuinely unexpected")
    monkeypatch.setattr(api.district_monitor, "run_cycle", boom)

    r = client.post("/api/districts/monitor/check-now")
    assert r.status_code == 502
