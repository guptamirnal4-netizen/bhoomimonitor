"""
GET /api/rtc - the live-callable endpoint. Mocks rtc_verifier so this
test never touches the network (Sec. 31), while still proving the
FastAPI wiring (query params -> rtc_verifier call -> JSON response
shape) is correct end to end, including hissa_no being optional now.
Requires a reachable MongoDB for api.py's module-level MongoClient
(lazy-connecting - importing api.py doesn't itself require Mongo to be
up, but if it's genuinely unreachable in your environment, skip this
file rather than let it hang)."""

import api
import aggregate
import rtc_verifier
from fastapi.testclient import TestClient

client = TestClient(api.app)


def test_reliability_timeline_defaults_to_rtc_service(monkeypatch):
    captured = {}

    def fake_timeline(events, hours=48, service=None):
        captured["hours"] = hours
        captured["service"] = service
        return {"events": [], "outages": []}

    monkeypatch.setattr(aggregate, "compute_uptime_timeline", fake_timeline)
    r = client.get("/api/reliability/timeline")
    assert r.status_code == 200
    assert captured["service"] == "RTC"
    assert captured["hours"] == 48
    assert r.json() == {"events": [], "outages": []}


def test_reliability_timeline_accepts_custom_window_and_no_service_filter(monkeypatch):
    captured = {}

    def fake_timeline(events, hours=48, service=None):
        captured["hours"] = hours
        captured["service"] = service
        return {"events": [], "outages": []}

    monkeypatch.setattr(aggregate, "compute_uptime_timeline", fake_timeline)
    r = client.get("/api/reliability/timeline", params={"hours": 72, "service": ""})
    assert r.status_code == 200
    assert captured["hours"] == 72
    assert captured["service"] is None  # empty string -> None, means "every service"


def test_rtc_endpoint_with_hissa_no_returns_one_result(monkeypatch):
    fake_results = [{
        "hissa_no": "1",
        "currentStatus": {"land_id": "5986 1500 1025", "ongoing_mutation": "No", "pyki": "No", "owners": []},
        "statusVerification": {"verificationRequired": False, "reasonCode": "NONE"},
    }]
    monkeypatch.setattr(rtc_verifier, "verify_current_status", lambda *a, **k: fake_results)

    r = client.get("/api/rtc", params={
        "district": "Bagalkote", "taluk": "Mudhol", "hobli": "LOKAPURA",
        "village": "LOKAPURA", "survey_number": "56", "hissa_no": "1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["count"] == 1
    assert body["results"][0]["currentStatus"]["land_id"] == "5986 1500 1025"
    assert body["query"]["hissa_no"] == "1"


def test_rtc_endpoint_without_hissa_no_returns_every_hissa(monkeypatch):
    fake_results = [
        {"hissa_no": "1", "currentStatus": {"land_id": "A"}, "statusVerification": {"reasonCode": "NONE"}},
        {"hissa_no": "2", "currentStatus": {"land_id": "B"}, "statusVerification": {"reasonCode": "NONE"}},
        {"hissa_no": "3", "currentStatus": {"land_id": "C"}, "statusVerification": {"reasonCode": "NONE"}},
    ]
    captured = {}

    def fake_verify(district, taluk, hobli, village, survey_number, hissa_no=None, **kwargs):
        captured["hissa_no"] = hissa_no
        return fake_results

    monkeypatch.setattr(rtc_verifier, "verify_current_status", fake_verify)

    r = client.get("/api/rtc", params={
        "district": "Bagalkote", "taluk": "Mudhol", "hobli": "LOKAPURA",
        "village": "LOKAPURA", "survey_number": "56",
        # hissa_no intentionally omitted
    })
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert [x["hissa_no"] for x in body["results"]] == ["1", "2", "3"]
    assert captured["hissa_no"] is None  # confirms it wasn't defaulted before reaching rtc_verifier


def test_rtc_endpoint_failure_still_returns_200_with_explicit_reason(monkeypatch):
    monkeypatch.setattr(
        rtc_verifier, "verify_current_status",
        lambda *a, **k: [{"hissa_no": "1", "currentStatus": None,
                           "statusVerification": {"verificationRequired": True, "reasonCode": "RTC_REQUEST_FAILED"}}],
    )
    r = client.get("/api/rtc", params={
        "district": "Bagalkote", "taluk": "Mudhol", "hobli": "LOKAPURA",
        "village": "LOKAPURA", "survey_number": "56", "hissa_no": "1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["results"][0]["currentStatus"] is None
    assert body["results"][0]["statusVerification"]["reasonCode"] == "RTC_REQUEST_FAILED"


def test_rtc_endpoint_requires_district_taluk_hobli_village_survey():
    r = client.get("/api/rtc", params={"district": "Bagalkote"})
    assert r.status_code == 422  # FastAPI's own validation - missing required params (hissa_no is NOT required)
