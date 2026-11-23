"""
crawler.py
-----------
Orchestrates one survey number end to end: MR_MutationExtract.aspx
cascade -> Fetch Details -> for EVERY mutation row returned (Sec. 14: the
Preview content is authoritative, not the label - so every row gets
checked, not just ones whose mutationType/acquisitionType look
loan-shaped) -> Select -> Preview -> loan_parser -> group by hissa ->
reconcile -> RTC (optional, see fetch_rtc) -> persist -> checkpoint.

Concurrency (Sec. 22): sequential by default. MAX_WORKERS exists as a
config knob for later, but this module itself makes one request at a
time on one BhoomiSession - correct-first, not fast-first. If/when
concurrency is introduced, each worker must get its OWN BhoomiSession
(never share one across workers - Sec. 22's "independent session per
worker").

Checkpoint granularity (documented trade-off, not an oversight): one
checkpoint per (district, taluk, hobli, village, survey), written after
that survey's land_records + loan_events are persisted. A crash mid-
survey means that survey's Preview fetches are redone on resume - safe
because persistence is idempotent (Sec. 18), just not maximally
efficient. Per-MR checkpointing is a natural next step if wider crawls
show this granularity is too coarse; not built out here to keep Phase 9
(a handful of MRs) easy to verify end-to-end.

Every real HTTP attempt is wrapped in the EXISTING MonitorLogger
(logger.py) under service="Mutation" - both the mutation-grid fetch and
each Preview fetch, since both are steps of the Mutation workflow
business-wise. This is a deliberate choice to avoid touching
schema.py's SERVICES/SERVICE_WEIGHTS (Sec. 34: don't change existing
schema unnecessarily) - it means real crawl activity now shows up in the
existing dashboard's "Mutation" tab instead of only in seed.py's fake
data.
"""

import logging
from datetime import datetime, timezone

import discovery
import district_names
import mutation_crawler as mc
import preview_fetcher
import loan_parser
import reconcile as reconcile_mod
import rtc_verifier
from bhoomi_session import BhoomiSession, BhoomiRequestFailed, BhoomiSessionExpired
from checkpoint_store import CheckpointStore
from land_schema import new_land_record, record_key, short_key
from logger import MonitorLogger
from persistence import LandRecordStore
from preview_fetcher import PreviewParseError

log = logging.getLogger("bhoomi")

BASE_URL = "https://landrecords.karnataka.gov.in/Service11/"


class SurveyCrawlResult:
    def __init__(self):
        self.records_persisted = 0
        self.loan_events_found = 0
        self.previews_ok = 0
        self.previews_failed = 0
        self.failures = []

    def as_dict(self):
        return {
            "records_persisted": self.records_persisted,
            "loan_events_found": self.loan_events_found,
            "previews_ok": self.previews_ok,
            "previews_failed": self.previews_failed,
            "failures": self.failures,
        }


def _find_option_value(options: list[tuple[str, str]], name: str) -> str | None:
    for value, text in options:
        if text.strip().lower() == name.strip().lower():
            return value
    return None


class _NoopTracker:
    """Stand-in for logger.py's _FetchTracker when a district can't be
    mapped into schema.py's DISTRICTS vocabulary (district_names.py) -
    the crawl proceeds and persists data normally, it just doesn't get
    an observability row in fetch_events for this call."""
    def mark_success(self):
        pass

    def error(self, error_type: str):
        pass


class _NoopTrack:
    def __enter__(self):
        return _NoopTracker()

    def __exit__(self, *exc_info):
        return False  # never swallow exceptions


def _track(mon: MonitorLogger, district: str, service: str):
    """mon.track(), but skips dashboard logging (instead of raising)
    when `district` isn't in schema.py's curated DISTRICTS list - a
    known, expected gap (Sec. 16 shakedown), not a crawl failure."""
    dashboard_district = district_names.normalize_for_dashboard(district)
    if dashboard_district is None:
        log.warning("[OBSERVABILITY] %r not in schema.py DISTRICTS - skipping dashboard tracking "
                     "for this call (data extraction is unaffected)", district)
        return _NoopTrack()
    return mon.track(dashboard_district, service)


def crawl_survey(district: str, taluk: str, hobli: str, village: str, survey_number: str,
                  mon: MonitorLogger, ckpt: CheckpointStore, store: LandRecordStore,
                  debug: bool = False, fetch_rtc: bool = False) -> SurveyCrawlResult:
    result = SurveyCrawlResult()

    if ckpt.is_complete(district, taluk, hobli, village, survey_number, None, None, "record_persisted"):
        log.info("[SKIP] %s/%s/%s/%s survey=%s already checkpointed - resuming past it",
                  district, taluk, hobli, village, survey_number)
        return result

    session = BhoomiSession(BASE_URL, debug=debug)

    try:
        soup = mc.start(session)
        d_val = _find_option_value(mc.list_options(soup, "district"), district)
        if not d_val:
            raise mc.MutationWorkflowError(f"District {district!r} not found in dropdown")
        soup = mc.select_district(session, soup, d_val)

        t_val = _find_option_value(mc.list_options(soup, "taluk"), taluk)
        if not t_val:
            raise mc.MutationWorkflowError(f"Taluk {taluk!r} not found under {district!r}")
        soup = mc.select_taluk(session, soup, d_val, t_val)

        h_val = _find_option_value(mc.list_options(soup, "hobli"), hobli)
        if not h_val:
            raise mc.MutationWorkflowError(f"Hobli {hobli!r} not found under {district!r}/{taluk!r}")
        soup = mc.select_hobli(session, soup, d_val, t_val, h_val)

        v_val = _find_option_value(mc.list_options(soup, "village"), village)
        if not v_val:
            raise mc.MutationWorkflowError(f"Village {village!r} not found under {district!r}/{taluk!r}/{hobli!r}")
        soup = mc.select_village(session, soup, d_val, t_val, h_val, v_val)

        with _track(mon, district, "Mutation") as t:
            soup = mc.enter_survey_and_fetch(session, soup, d_val, t_val, h_val, v_val, survey_number)
            t.mark_success()

    except BhoomiSessionExpired as exc:
        ckpt.record_failure(district, "mutation_fetched", "SESSION_EXPIRED",
                             {"taluk": taluk, "hobli": hobli, "village": village,
                              "survey": survey_number, "error": str(exc)})
        result.failures.append({"stage": "mutation_fetched", "reasonCode": "SESSION_EXPIRED", "error": str(exc)})
        return result
    except BhoomiRequestFailed as exc:
        ckpt.record_failure(district, "mutation_fetched", "MUTATION_REQUEST_FAILED",
                             {"taluk": taluk, "hobli": hobli, "village": village,
                              "survey": survey_number, "error": str(exc)})
        result.failures.append({"stage": "mutation_fetched", "reasonCode": "MUTATION_REQUEST_FAILED", "error": str(exc)})
        return result
    except mc.MutationWorkflowError as exc:
        ckpt.record_failure(district, "mutation_fetched", "SOURCE_PARSE_FAILED",
                             {"taluk": taluk, "hobli": hobli, "village": village,
                              "survey": survey_number, "error": str(exc)})
        result.failures.append({"stage": "mutation_fetched", "reasonCode": "SOURCE_PARSE_FAILED", "error": str(exc)})
        return result

    raw_mutations = mc.parse_mutation_grid(soup)
    log.info("[MUTATION] %s/%s/%s/%s survey=%s rows=%d", district, taluk, hobli, village,
              survey_number, len(raw_mutations))

    by_hissa: dict[str, list[dict]] = {}
    for row in raw_mutations:
        by_hissa.setdefault(row["hissaNumber"], []).append(row)

    for hissa, rows in by_hissa.items():
        loan_events = []
        hissa_previews_ok = 0
        hissa_previews_failed = 0
        for row in rows:
            row_idx = mc.grid_row_index(soup, row["mrNo"], row["transactionNo"])
            if row_idx is None:
                result.previews_failed += 1
                result.failures.append({
                    "stage": "preview_complete", "reasonCode": "SOURCE_PARSE_FAILED",
                    "mrNo": row["mrNo"], "error": "row not found for Select$N lookup",
                })
                continue
            try:
                sel_soup = mc.select_mutation_row(session, soup, d_val, t_val, h_val, v_val, survey_number, row_idx)
                with _track(mon, district, "Mutation") as t:
                    preview_url = mc.trigger_preview(session, sel_soup, d_val, t_val, h_val, v_val, survey_number)
                    preview_data = preview_fetcher.fetch_preview(session, preview_url, referer=BASE_URL + mc.PATH)
                    t.mark_success()
                event = loan_parser.build_loan_event(row, preview_data)
                result.previews_ok += 1
                hissa_previews_ok += 1
                ckpt.record(district, taluk, hobli, village, survey_number, hissa, row["mrNo"],
                            "preview_complete", "success", {"sourceUrl": preview_url})
                log.info("[PREVIEW] MR=%s status=200", row["mrNo"])
                if event:
                    loan_events.append(event)
                    for charge in event["charges"]:
                        log.info("[LOAN] record=%s|%s|%s MR=%s bank=%s amount=%s classification=%s",
                                 village, survey_number, hissa, row["mrNo"],
                                 charge.get("bankRaw"), charge.get("amountRaw"), event["instrumentClass"])
                    if event["sourceVerification"]["verificationRequired"]:
                        log.info("[VERIFY_REQUIRED] record=%s|%s|%s MR=%s reason=%s",
                                 village, survey_number, hissa, row["mrNo"],
                                 event["sourceVerification"]["reasonCode"])
            except (BhoomiRequestFailed, BhoomiSessionExpired, mc.MutationWorkflowError, PreviewParseError) as exc:
                reason = ("SESSION_EXPIRED" if isinstance(exc, BhoomiSessionExpired)
                          else "SOURCE_PARSE_FAILED" if isinstance(exc, PreviewParseError)
                          else "PREVIEW_REQUEST_FAILED")
                result.previews_failed += 1
                hissa_previews_failed += 1
                ckpt.record_failure(district, "preview_complete", reason,
                                     {"mrNo": row["mrNo"], "hissa": hissa, "error": str(exc)})
                result.failures.append({"stage": "preview_complete", "reasonCode": reason,
                                         "mrNo": row["mrNo"], "error": str(exc)})
                log.warning("[VERIFY_REQUIRED] MR=%s reason=%s error=%s", row["mrNo"], reason, exc)
                continue

        loan_history = reconcile_mod.reconcile(loan_events)
        result.loan_events_found += len(loan_history)

        if fetch_rtc:
            # Opt-in (Sec. 22: don't silently double the request volume
            # of every mutation crawl just because RTC fetching is no
            # longer CAPTCHA-blocked). The primary way to get a live RTC
            # lookup is the standalone GET /api/rtc endpoint (api.py),
            # which calls rtc_verifier directly - this flag exists for
            # crawls that specifically want currentStatus baked into
            # every persisted land_record too.
            # verify_current_status always returns a list (Sec. per your instruction: never guess
            # which hissa was meant when unspecified) - here we always ask for THIS specific hissa,
            # so the list has exactly one entry.
            rtc_result = rtc_verifier.verify_current_status(
                district, taluk, hobli, village, survey_number, hissa_no=hissa, loan_history=loan_history
            )[0]
            current_status, status_verification = rtc_result["currentStatus"], rtc_result["statusVerification"]
        else:
            current_status, status_verification = None, {
                "verificationRequired": True, "reasonCode": "RTC_NOT_FETCHED",
                "checkedAt": None, "detail": "fetch_rtc=False for this crawl - RTC lookup skipped by design.",
            }

        rec_key = record_key(district, taluk, hobli, village, survey_number, hissa)
        s_key = short_key(village, survey_number, hissa)
        doc = new_land_record(
            district=district, taluk=taluk, hobli=hobli, village=village,
            survey_number=survey_number, hissa_no=hissa,
            raw_mutations=rows, loan_history=loan_history,
            current_status=current_status, status_verification=status_verification,
            crawl_metadata={
                "http_status": 200,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "previewsOkThisHissa": hissa_previews_ok,
                "previewFailuresThisHissa": hissa_previews_failed,
            },
        )
        store.upsert_land_record(doc)
        store.upsert_loan_events(rec_key, s_key, district, loan_history)
        result.records_persisted += 1
        log.info("[VERIFY] record=%s status=persisted loan_history_events=%d", s_key, len(loan_history))

    ckpt.record(district, taluk, hobli, village, survey_number, None, None, "record_persisted", "success",
                {"hissas": list(by_hissa.keys()), "previews_ok": result.previews_ok,
                 "previews_failed": result.previews_failed})
    return result
