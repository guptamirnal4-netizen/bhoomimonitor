"""district_check.py - the lightweight reachability check. Mocks
BhoomiSession's HTTP layer (Sec. 31: no live requests in unit tests)."""

from unittest.mock import MagicMock

import bhoomi_session
import district_check


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


DISTRICT_PAGE = '''
<select id="ctl00_MainContent_ddlCDistrict">
  <option value="0">Select District</option>
  <option value="2">BAGALKOTE</option>
  <option value="1">BELAGAVI</option>
</select>
'''

TALUK_PAGE_WITH_TALUKS = DISTRICT_PAGE + '''
<select id="ctl00_MainContent_ddlCTaluk">
  <option value="0">Select Taluk</option>
  <option value="1">Mudhol</option>
  <option value="2">Bagalkote</option>
</select>
'''

TALUK_PAGE_EMPTY = DISTRICT_PAGE + '''
<select id="ctl00_MainContent_ddlCTaluk">
  <option value="0">Select Taluk</option>
</select>
'''


def _session(get_html, post_html):
    session = MagicMock(spec=bhoomi_session.BhoomiSession)
    from bs4 import BeautifulSoup
    session.get_page.return_value = BeautifulSoup(get_html, "lxml")
    session.postback.return_value = (BeautifulSoup(post_html, "lxml"), FakeResp(post_html))
    return session


def test_district_up_when_taluks_populate():
    session = _session(DISTRICT_PAGE, TALUK_PAGE_WITH_TALUKS)
    result = district_check.check_district_reachability("Bagalkote", session=session)
    assert result["status"] == "up"
    assert result["district_code"] == "2"
    assert result["taluk_count"] == 2


def test_district_down_when_taluks_come_back_empty():
    session = _session(DISTRICT_PAGE, TALUK_PAGE_EMPTY)
    result = district_check.check_district_reachability("Bagalkote", session=session)
    assert result["status"] == "down"
    assert "empty" in result["reason"].lower()


def test_district_check_error_when_registry_has_no_mapping():
    session = _session(DISTRICT_PAGE, TALUK_PAGE_WITH_TALUKS)
    result = district_check.check_district_reachability("Not A Real District", session=session)
    assert result["status"] == "check_error"
    assert "district_registry" in result["error_message"]


def test_district_check_error_when_portal_dropdown_no_longer_has_the_mapped_text():
    # e.g. district_registry.py's mapping goes stale - a bug on OUR side,
    # not evidence the portal itself is down.
    session = _session('<select id="ctl00_MainContent_ddlCDistrict"><option value="0">Select District</option></select>', "")
    result = district_check.check_district_reachability("Bagalkote", session=session)
    assert result["status"] == "check_error"


def test_session_expired_reported_as_down_not_check_error():
    session = MagicMock(spec=bhoomi_session.BhoomiSession)
    session.get_page.side_effect = bhoomi_session.BhoomiSessionExpired("dead session")
    result = district_check.check_district_reachability("Bagalkote", session=session)
    assert result["status"] == "down"  # portal-side evidence, not our bug


def test_request_failed_reported_as_down_not_check_error():
    session = MagicMock(spec=bhoomi_session.BhoomiSession)
    session.get_page.side_effect = bhoomi_session.BhoomiRequestFailed("timeout after retries")
    result = district_check.check_district_reachability("Bagalkote", session=session)
    assert result["status"] == "down"


def test_unexpected_exception_reported_as_check_error_not_down():
    session = MagicMock(spec=bhoomi_session.BhoomiSession)
    session.get_page.side_effect = RuntimeError("a genuine bug in our own code")
    result = district_check.check_district_reachability("Bagalkote", session=session)
    assert result["status"] == "check_error"
