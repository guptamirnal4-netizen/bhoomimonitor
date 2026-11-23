"""
GET /api/options, /api/mutation, /api/loan - the Atlas live-lookup
endpoints. The portal is never contacted: atlas_service's low-level
cascade is stubbed, so these prove input validation, status
classification (ok / no_records / error), year/hissa filtering, API-key
enforcement and HTTP status mapping."""

import atlas_service
import mutation_crawler as mc
from bhoomi_session import BhoomiRequestFailed
from fastapi.testclient import TestClient

import api

client = TestClient(api.app)
Q = {"district": "Bagalkote", "taluk": "Mudhol", "hobli": "LOKAPURA", "village": "LOKAPURA"}

ROWS = [
    {"surveyNo": "56/*/1", "hissaNumber": "1", "transactionYear": "2003-2004", "transactionNo": "1",
     "mrNo": "3760", "mutationType": "x", "acquisitionType": "y", "approvedDate": "23/02/2004"},
    {"surveyNo": "56/*/1", "hissaNumber": "1", "transactionYear": "2015-2016", "transactionNo": "2",
     "mrNo": "120", "mutationType": "x", "acquisitionType": "y", "approvedDate": "29/02/2016"},
    {"surveyNo": "56/*/2", "hissaNumber": "2", "transactionYear": "2005-2006", "transactionNo": "3",
     "mrNo": "55", "mutationType": "x", "acquisitionType": "y", "approvedDate": "not-a-date"},
]


def _stub_grid(monkeypatch, rows):
    monkeypatch.setattr(atlas_service, "_open_mutation_grid", lambda *a, **k: (object(), 1, 2, 3, 4))
    monkeypatch.setattr(mc, "parse_mutation_grid", lambda soup: [dict(r) for r in rows])


def test_mutation_rejects_non_numeric_survey():
    r = client.get("/api/mutation", params={**Q, "survey_number": "abc"})
    assert r.status_code == 422


def test_mutation_ok_sorted_and_undated_rows_kept(monkeypatch):
    _stub_grid(monkeypatch, ROWS)
    body = client.get("/api/mutation", params={**Q, "survey_number": "56"}).json()
    assert body["status"] == "ok" and body["count"] == 3 and body["totalOnPortalForSurvey"] == 3
    assert [r["mrNo"] for r in body["records"]] == ["3760", "120", "55"]  # undated row last, not dropped
    assert body["records"][2]["approvedDateIso"] is None


def test_mutation_filters_by_hissa_and_year(monkeypatch):
    _stub_grid(monkeypatch, ROWS)
    body = client.get("/api/mutation", params={**Q, "survey_number": "56", "hissa_no": "1", "from_year": 2010}).json()
    assert [r["mrNo"] for r in body["records"]] == ["120"]
    assert body["totalOnPortalForSurvey"] == 3


def test_mutation_empty_grid_is_no_records_not_error(monkeypatch):
    _stub_grid(monkeypatch, [])
    body = client.get("/api/mutation", params={**Q, "survey_number": "99999"}).json()
    assert body["status"] == "no_records" and body["error"] is None and body["success"] is True


def test_mutation_portal_failure_is_error_not_no_records(monkeypatch):
    def boom(*a, **k):
        raise BhoomiRequestFailed("timeout")
    monkeypatch.setattr(atlas_service, "_open_mutation_grid", boom)
    body = client.get("/api/mutation", params={**Q, "survey_number": "56"}).json()
    assert body["status"] == "error" and body["success"] is False
    assert body["error"]["reasonCode"] == "PORTAL_UNREACHABLE"


def test_mutation_unknown_location_reports_location_not_found(monkeypatch):
    def nf(*a, **k):
        raise atlas_service.LocationNotFound("Taluk 'X' not found")
    monkeypatch.setattr(atlas_service, "_open_mutation_grid", nf)
    body = client.get("/api/mutation", params={**Q, "survey_number": "56"}).json()
    assert body["status"] == "error" and body["error"]["reasonCode"] == "LOCATION_NOT_FOUND"


def test_loan_requires_hissa():
    r = client.get("/api/loan", params={**Q, "survey_number": "56"})
    assert r.status_code == 422


def test_loan_no_charges_is_no_records(monkeypatch):
    _stub_grid(monkeypatch, ROWS)
    monkeypatch.setattr(mc, "grid_row_index", lambda soup, mr, tx: 0)
    monkeypatch.setattr(mc, "select_mutation_row", lambda *a, **k: object())
    monkeypatch.setattr(mc, "trigger_preview", lambda *a, **k: "http://x")
    monkeypatch.setattr(atlas_service.preview_fetcher, "fetch_preview", lambda *a, **k: {})
    monkeypatch.setattr(atlas_service.loan_parser, "build_loan_event", lambda row, prev: None)
    body = client.get("/api/loan", params={**Q, "survey_number": "56", "hissa_no": "1"}).json()
    assert body["status"] == "no_records" and body["loanHistory"] == []
    assert body["mutationsChecked"] == 2 and body["previewsOk"] == 2


def test_loan_all_previews_failing_is_error(monkeypatch):
    _stub_grid(monkeypatch, ROWS)
    monkeypatch.setattr(mc, "grid_row_index", lambda soup, mr, tx: 0)

    def boom(*a, **k):
        raise BhoomiRequestFailed("portal down")
    monkeypatch.setattr(mc, "select_mutation_row", boom)
    body = client.get("/api/loan", params={**Q, "survey_number": "56", "hissa_no": "1"}).json()
    assert body["status"] == "error" and body["success"] is False
    assert body["previewsFailed"] == 2 and body["error"]["reasonCode"] == "PORTAL_UNREACHABLE"


def test_options_requires_parents():
    assert client.get("/api/options", params={"level": "taluk"}).status_code == 422
    assert client.get("/api/options", params={"level": "bogus"}).status_code == 422


def test_api_key_enforced_only_when_configured(monkeypatch):
    monkeypatch.setenv("ATLAS_API_KEY", "secret")
    assert client.get("/api/options", params={"level": "taluk"}).status_code == 401
    assert client.get("/api/options", params={"level": "taluk"}, headers={"X-API-Key": "wrong"}).status_code == 401
    # right key gets past auth to normal validation (422 = missing district)
    assert client.get("/api/options", params={"level": "taluk"}, headers={"X-API-Key": "secret"}).status_code == 422


def test_api_key_middleware_covers_monitoring_and_post_routes(monkeypatch):
    monkeypatch.setenv("ATLAS_API_KEY", "secret")
    assert client.get("/api/districts").status_code == 401
    assert client.post("/api/districts/monitor/check-now").status_code == 401
    assert client.get("/api/summary", headers={"X-API-Key": "wrong"}).status_code == 401
