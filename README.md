# Bhoomi Monitor — backend

Logs every scrape attempt to MongoDB, aggregates it into the numbers
the dashboard shows, and serves them over a small FastAPI.

## Files

- `schema.py` — the one source of truth for the `fetch_events` document
  shape, the district/service/error-type vocabularies, and the status
  thresholds (88% = ok, 70–87% = degraded, <70% = fail, blended 55%
  RTC / 45% Mutation). Change thresholds here, nowhere else.
- `logger.py` — what the scraper imports. `MonitorLogger.log(...)` for
  manual logging, or `with mon.track(district, service) as t:` to time
  a fetch and auto-classify success/fail.
- `aggregate.py` — MongoDB aggregation pipelines that turn raw events
  into: district status table, RTC/Mutation reliability + failure
  reasons, daily cost trend, portal uptime trend, recent failures.
- `api.py` — FastAPI app, one endpoint per dashboard tab.
- `seed.py` — generates realistic fake data so you can test the API
  before the real scraper is wired in.

## Wiring it into the scraper

Wherever the scraper currently does something like:

```python
result = fetch_rtc(district, survey_no)
```

wrap it:

```python
from logger import MonitorLogger
mon = MonitorLogger(mongo_uri="mongodb://localhost:27017")

with mon.track(district, "RTC") as t:
    result = fetch_rtc(district, survey_no)
    t.cost(proxy=0.30, compute=0.03)
    t.hissa_key(result.hissa_key)
```

If `fetch_rtc` raises, the block still logs a failure automatically
(defaulting to `error_type="server_error"` unless you catch the
specific exception and call `t.error("timeout")` etc. before
re-raising — see the docstring in `logger.py`).

## Running it locally

```bash
pip install -r requirements.txt

# start mongo however you normally do, then:
python seed.py --days 30 --wipe

uvicorn api:app --reload --port 8000
# http://localhost:8000/api/districts
# http://localhost:8000/api/summary
```

## Wiring the React dashboard to this API

Swap the `buildDistrictData()` / `buildCostTrend()` / etc. mock
generators for `fetch("http://localhost:8000/api/districts")` calls
inside a `useEffect`. The JSON shapes were designed to match the
dashboard's field names 1:1:

| API field                  | dashboard mock field |
|-----------------------------|-----------------------|
| `overall`, `status`         | same                  |
| `rtc_rate`, `mutation_rate` | `rtcRate`, `mutRate`  |
| `day`, `proxy/captcha/compute` | same in cost trend |
| `uptime`                    | same in reliability   |

## What's not done yet

- No auth on the API — fine for local/internal use, not for a public
  deploy.
- No scheduled rollup job. At current expected volume (a few hundred
  districts × services per day) the live aggregation pipelines in
  `aggregate.py` are fast enough to query directly. If `fetch_events`
  grows past ~1–2M documents, add a cron/APScheduler job that writes
  `compute_district_status()` / `compute_portal_uptime()` output into
  a `daily_rollups` collection every 15 min, and point `api.py` at
  that instead.
