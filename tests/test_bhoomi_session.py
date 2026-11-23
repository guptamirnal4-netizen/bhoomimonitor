"""
Scenario 13/14: HTTP 500 retry-with-backoff, and session-expiry
detection. No live requests - requests.Session.request is monkeypatched.
"""

import pytest

import bhoomi_session
from bhoomi_session import BhoomiRequestFailed, BhoomiSession, BhoomiSessionExpired


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()


def _session(monkeypatch, responses):
    """responses: list of FakeResponse, consumed in order per call."""
    session = BhoomiSession("https://landrecords.karnataka.gov.in/Service11/", max_retries=3, backoff_base=1.01)
    calls = {"n": 0}

    def fake_request(method, url, headers=None, timeout=None, **kwargs):
        resp = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return resp

    monkeypatch.setattr(session.http, "request", fake_request)
    monkeypatch.setattr(bhoomi_session.time, "sleep", lambda s: None)  # don't actually wait in tests
    return session, calls


# ---------- Scenario 13: HTTP 500 - retry then succeed ----------

def test_http_500_then_success_recovers_via_retry(monkeypatch):
    session, calls = _session(monkeypatch, [FakeResponse(500), FakeResponse(200, "<html>ok</html>")])
    resp = session._request("GET", "MR_MutationExtract.aspx")
    assert resp.status_code == 200
    assert calls["n"] == 2


def test_http_500_exhausts_retries_and_raises(monkeypatch):
    session, calls = _session(monkeypatch, [FakeResponse(500)])
    with pytest.raises(BhoomiRequestFailed):
        session._request("GET", "MR_MutationExtract.aspx")
    assert calls["n"] == 3  # max_retries


# ---------- Scenario 14: session expiration - not retried, raised immediately ----------

def test_session_expired_marker_raises_immediately_not_retried(monkeypatch):
    session, calls = _session(monkeypatch, [FakeResponse(200, "The state information is invalid for this page.")])
    with pytest.raises(BhoomiSessionExpired):
        session._request("GET", "MR_MutationExtract.aspx")
    assert calls["n"] == 1  # no retry attempted - a dead session won't fix itself by retrying


def test_healthy_response_passes_through(monkeypatch):
    session, calls = _session(monkeypatch, [FakeResponse(200, "<html>fine</html>")])
    resp = session._request("GET", "MR_MutationExtract.aspx")
    assert resp.status_code == 200
    assert calls["n"] == 1


def test_hidden_fields_extraction():
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(
        '<input type="hidden" name="__VIEWSTATE" value="abc" />'
        '<input type="hidden" name="__EVENTVALIDATION" value="def" />',
        "lxml",
    )
    fields = BhoomiSession.hidden_fields(soup)
    assert fields == {"__VIEWSTATE": "abc", "__EVENTVALIDATION": "def"}
