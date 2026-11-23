"""
bhoomi_session.py
-------------------
The ASP.NET WebForms postback engine every Bhoomi crawler module is built
on. Confirmed live against MR_MutationExtract.aspx before writing this
(see the district->taluk->hobli->village->survey->Fetch Details->Select->
Preview chain) - not guessed from the manual browser notes alone:

  - Cascading dropdowns (district/taluk/hobli/village) each fire a
    partial __doPostBack on `onchange`.
  - "Fetch Details" / "Preview" are plain <input type="submit"> controls
    - clicking them means POSTing their name=value pair, not setting
    __EVENTTARGET.
  - Selecting a mutation row fires
    __doPostBack('ctl00$MainContent$gv_MutationDetails','Select$N').
  - The actual Preview URL (?Type=...&MutType=...&Phodi_Process=...) is
    NOT a static link anywhere in the DOM - it's emitted as a
    `window.open('ReportPreview.aspx?...','_blank')` script in the HTML
    returned by the Preview button's postback. MutType therefore MUST be
    read out of that response per mutation row; it is never hardcoded
    here.
  - The Preview page itself is then a plain same-session GET to that
    extracted URL.

One BhoomiSession = one ASP.NET session (one cookie jar). Do not share a
BhoomiSession across concurrent workers (Sec. 22) - each worker gets its
own instance/session.
"""

import logging
import time

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("bhoomi")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}

# Text fragments that indicate the ASP.NET session/ViewState died mid
# workflow, observed on this class of government WebForms portal. Kept
# as a short, explicit list (not a broad regex) so it fails closed:
# something genuinely new goes to BhoomiRequestFailed (retryable),
# not silently treated as a session expiry.
SESSION_EXPIRED_MARKERS = (
    "the state information is invalid",  # ASP.NET ViewState MAC/validation failure
    "session expired",
    "your session has expired",
)


class BhoomiSessionExpired(Exception):
    """The ASP.NET session/ViewState died mid-workflow. Caller must
    start a fresh BhoomiSession and replay the workflow from the last
    successful checkpoint stage - never patch over it with the dead
    session's state."""


class BhoomiRequestFailed(Exception):
    """A request failed after exhausting retries (timeout, connection
    error, repeated 5xx). Distinguish from BhoomiSessionExpired: this is
    "the portal/network is having trouble", not "our session died"."""


class BhoomiSession:
    def __init__(self, base_url: str, timeout: int = 30, max_retries: int = 3,
                 backoff_base: float = 2.0, debug: bool = False):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.debug = debug
        self.http = requests.Session()
        self.http.headers.update(DEFAULT_HEADERS)

    # ---------- low-level request with retry/backoff ----------

    def _request(self, method: str, path: str, referer: str | None = None, **kwargs) -> requests.Response:
        url = path if path.startswith("http") else self.base_url + path
        headers = {"Referer": referer} if referer else {}
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.debug:
                    # Never log cookies/session id - just method/url/attempt (Sec. 23/35).
                    log.debug("[HTTP] %s %s attempt=%d", method, url, attempt)
                resp = self.http.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
                if resp.status_code >= 500:
                    raise BhoomiRequestFailed(f"{method} {url} -> HTTP {resp.status_code}")
                lowered = resp.text[:4000].lower() if resp.text else ""
                if any(marker in lowered for marker in SESSION_EXPIRED_MARKERS):
                    raise BhoomiSessionExpired(f"{method} {url} -> session-expired marker in response")
                if self.debug:
                    log.debug("[HTTP] %s %s -> %d (%d bytes)", method, url, resp.status_code, len(resp.content))
                return resp
            except BhoomiSessionExpired:
                raise  # not retryable within this session - caller must rebuild the session
            except (requests.Timeout, requests.ConnectionError, BhoomiRequestFailed) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    sleep_s = self.backoff_base ** attempt
                    log.warning("[RETRY] %s %s attempt=%d error=%s sleeping=%.1fs",
                                method, url, attempt, exc, sleep_s)
                    time.sleep(sleep_s)
                else:
                    break
        raise BhoomiRequestFailed(f"{method} {url} failed after {self.max_retries} attempts: {last_exc}")

    # ---------- ASP.NET WebForms helpers ----------

    @staticmethod
    def hidden_fields(soup: BeautifulSoup) -> dict:
        return {
            inp.get("name"): inp.get("value", "")
            for inp in soup.find_all("input", {"type": "hidden"})
            if inp.get("name")
        }

    @staticmethod
    def select_options(soup: BeautifulSoup, select_id: str) -> list[tuple[str, str]]:
        sel = soup.find("select", {"id": select_id})
        if not sel:
            return []
        return [(o.get("value"), o.text.strip()) for o in sel.find_all("option")]

    def get_page(self, path: str) -> BeautifulSoup:
        """Fresh GET of a page - use once per workflow to seed hidden
        fields, then drive everything else via postback()."""
        resp = self._request("GET", path)
        return BeautifulSoup(resp.text, "lxml")

    def get_raw(self, path: str, referer: str | None = None) -> requests.Response:
        """Fresh GET returning the raw response (e.g. for a Preview URL
        that isn't itself a WebForms page needing hidden-field replay)."""
        return self._request("GET", path, referer=referer)

    def postback(self, path: str, current_soup: BeautifulSoup, fields: dict,
                 event_target: str | None = None, event_argument: str = "",
                 referer: str | None = None) -> tuple[BeautifulSoup, requests.Response]:
        """Perform one ASP.NET postback: replay current_soup's hidden
        fields (__VIEWSTATE etc.), overlay the caller-supplied field
        values, optionally set __EVENTTARGET/__EVENTARGUMENT for a
        __doPostBack-style event (dropdown change, GridView Select),
        and return the freshly parsed response soup + raw response.

        For plain <input type="submit"> buttons (Fetch Details,
        Preview), don't pass event_target - just include the button's
        name=value in `fields` instead, matching what a real browser
        form-submit sends.
        """
        payload = self.hidden_fields(current_soup)
        payload.update(fields)
        if event_target is not None:
            payload["__EVENTTARGET"] = event_target
            payload["__EVENTARGUMENT"] = event_argument
        resp = self._request("POST", path, referer=referer or (self.base_url + path), data=payload)
        return BeautifulSoup(resp.text, "lxml"), resp
