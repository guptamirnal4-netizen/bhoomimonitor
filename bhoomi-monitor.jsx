import React, { useState, useMemo, useEffect } from "react";
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell
} from "recharts";

// ---------- Design tokens ----------
// Ink-ledger palette: parchment surface, deep ledger-ink text,
// vermilion "rejected stamp" red, moss "cleared stamp" green,
// brass for cost/warning accents.
const T = {
  paper: "#F3EEDD",
  paperDeep: "#E9E1C7",
  ink: "#20303B",
  inkSoft: "#4A5C63",
  rule: "#C9BE9C",
  ok: "#3C6E4F",
  okBg: "#DCE7D5",
  fail: "#A63B33",
  failBg: "#EFD8CF",
  warn: "#B3862C",
  warnBg: "#EFE1C1",
  navy: "#1F3B4D",
};

// ---------- Mock data: 31 Karnataka districts ----------
const DISTRICTS = [
  "Bagalkote","Ballari","Belagavi","Bengaluru Rural","Bengaluru Urban","Bidar",
  "Chamarajanagara","Chikkaballapura","Chikkamagaluru","Chitradurga",
  "Dakshina Kannada","Davanagere","Dharwad","Gadag","Hassan","Haveri",
  "Kalaburagi","Kodagu","Kolar","Koppal","Mandya","Mysuru","Raichur",
  "Ramanagara","Shivamogga","Tumakuru","Udupi","Uttara Kannada",
  "Vijayapura","Yadgir","Vijayanagara"
];

function seedRandom(seed) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

function buildDistrictData() {
  return DISTRICTS.map((name, i) => {
    const rnd = seedRandom(i * 17 + 3);
    const rtcRate = Math.round((0.7 + rnd() * 0.29) * 100);
    const mutRate = Math.round((0.55 + rnd() * 0.4) * 100);
    const overall = Math.round((rtcRate * 0.55 + mutRate * 0.45));
    let status = "ok";
    if (overall < 70) status = "fail";
    else if (overall < 88) status = "degraded";
    return { name, rtcRate, mutRate, overall, status, fetches: 40 + Math.floor(rnd() * 260) };
  }).sort((a, b) => a.overall - b.overall);
}

function buildReliabilityTrend() {
  const rnd = seedRandom(99);
  const days = [];
  let base = 91;
  for (let i = 29; i >= 0; i--) {
    base += (rnd() - 0.5) * 3;
    base = Math.max(78, Math.min(97, base));
    const d = new Date();
    d.setDate(d.getDate() - i);
    days.push({
      day: d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
      uptime: Math.round(base * 10) / 10,
    });
  }
  return days;
}

function buildCostTrend() {
  const rnd = seedRandom(51);
  const days = [];
  for (let i = 13; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const proxy = 180 + rnd() * 60;
    const captcha = 90 + rnd() * 70;
    const compute = 40 + rnd() * 20;
    days.push({
      day: d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
      proxy: Math.round(proxy),
      captcha: Math.round(captcha),
      compute: Math.round(compute),
    });
  }
  return days;
}

const FAILURE_LOG = [
  { time: "14:32", district: "Yadgir", service: "Mutation", reason: "CAPTCHA solve failed (3x retry)", cost: "₹2.40" },
  { time: "14:19", district: "Kalaburagi", service: "RTC", reason: "Portal timeout (>30s)", cost: "₹0.80" },
  { time: "13:57", district: "Bidar", service: "Mutation", reason: "Schema mismatch — table columns shifted", cost: "₹1.10" },
  { time: "13:41", district: "Raichur", service: "RTC", reason: "Session expired mid-postback", cost: "₹0.60" },
  { time: "13:20", district: "Koppal", service: "Mutation", reason: "CAPTCHA solve failed (3x retry)", cost: "₹2.40" },
  { time: "12:58", district: "Vijayapura", service: "RTC", reason: "Portal 503 — server overloaded", cost: "₹0.40" },
];

const statusColor = (status) =>
  status === "ok" ? T.ok : status === "degraded" ? T.warn : T.fail;
const statusBg = (status) =>
  status === "ok" ? T.okBg : status === "degraded" ? T.warnBg : T.failBg;
const statusLabel = (status) =>
  status === "ok" ? "CLEARED" : status === "degraded" ? "PARTIAL" : "REJECTED";

// 31-district monitor's own status vocabulary (up/down/check_error/
// not_checked) - a DIFFERENT set of values from the district ledger's
// ok/degraded/fail above, so kept as separate helpers rather than
// force-fitting one onto the other.
const districtCheckColor = (status) =>
  status === "up" ? T.ok : status === "down" ? T.fail : status === "check_error" ? T.warn : T.inkSoft;
const districtCheckBg = (status) =>
  status === "up" ? T.okBg : status === "down" ? T.failBg : status === "check_error" ? T.warnBg : T.paperDeep;
const districtCheckLabel = (status) =>
  status === "up" ? "UP" : status === "down" ? "DOWN" : status === "check_error" ? "CHECK ERROR" : "NOT CHECKED";

function Stamp({ status }) {
  return (
    <span
      style={{
        display: "inline-block",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: "0.08em",
        color: statusColor(status),
        border: `1.5px solid ${statusColor(status)}`,
        borderRadius: 3,
        padding: "1px 6px",
        transform: "rotate(-3deg)",
        background: statusBg(status),
      }}
    >
      {statusLabel(status)}
    </span>
  );
}

// ---------- Live API wiring ----------
// Maps the FastAPI JSON field names (snake_case, matching aggregate.py)
// onto the field names this component already uses (camelCase mock
// shape) so nothing else below has to change.
function mapDistrict(d) {
  return {
    name: d.district,
    rtcRate: d.rtc_rate,
    mutRate: d.mutation_rate,
    overall: d.overall,
    status: d.status,
    fetches: d.fetches,
  };
}
function mapCostDay(d) {
  return { day: d.day, proxy: d.proxy, captcha: d.captcha, compute: d.compute };
}
function mapReliabilityDay(d) {
  return { day: d.day, uptime: d.uptime };
}
function mapFailure(f) {
  return { time: f.time, district: f.district, service: f.service, reason: f.reason, cost: `₹${f.cost}` };
}
// /api/reliability/timeline - fine-grained (per health-check event) history,
// separate from the daily-average chart above: answers "was it down at some
// specific time", not just "what % of today succeeded".
function mapTimelineEvent(e) {
  return { timestamp: e.timestamp, status: e.status, errorType: e.error_type, district: e.district };
}
function mapOutage(o) {
  return { start: o.start, end: o.end, count: o.count, reasons: o.reasons || [], ongoing: !!o.ongoing };
}

async function fetchJson(base, path) {
  const res = await fetch(`${base}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export default function BhoomiMonitor() {
  const [apiBase, setApiBase] = useState("http://127.0.0.1:8000");
  const [connection, setConnection] = useState("idle"); // idle | loading | live | offline
  const [tab, setTab] = useState("districts");

  const [districtData, setDistrictData] = useState(() => buildDistrictData());
  const [reliabilityTrend, setReliabilityTrend] = useState(() => buildReliabilityTrend());
  const [costTrend, setCostTrend] = useState(() => buildCostTrend());
  const [failureLog, setFailureLog] = useState(FAILURE_LOG);
  const [timelineEvents, setTimelineEvents] = useState([]);
  const [outages, setOutages] = useState([]);
  const [timelineHours, setTimelineHours] = useState(48);
  const [timelineLoading, setTimelineLoading] = useState(false);

  // 31-district reachability monitor (district_monitor.py, every 30 min)
  const [districtSnapshot, setDistrictSnapshot] = useState(null); // {scheduled_for, districts: [...]}
  const [districtReport, setDistrictReport] = useState(null);
  const [districtIncidents, setDistrictIncidents] = useState([]);
  const [monitor31Loading, setMonitor31Loading] = useState(false);

  async function fetchMonitor31() {
    setMonitor31Loading(true);
    try {
      const [snapshot, report, incidents] = await Promise.all([
        fetchJson(apiBase, "/api/districts/monitor/snapshot"),
        fetchJson(apiBase, "/api/districts/monitor/report"),
        fetchJson(apiBase, "/api/districts/monitor/incidents?limit=20"),
      ]);
      setDistrictSnapshot(snapshot);
      setDistrictReport(report);
      setDistrictIncidents(incidents.incidents || []);
    } catch (e) {
      // leave whatever was already loaded on screen
    } finally {
      setMonitor31Loading(false);
    }
  }

  // Flat, filterable/sortable check log (EVERY check, automatic AND
  // manual - nothing hidden by default) - a separate dataset/fetch from
  // the live-snapshot grid above, since it's not checkpoint-grid shaped.
  const [checkLog, setCheckLog] = useState({ total: 0, checks: [] });
  const [checkLogLoading, setCheckLogLoading] = useState(false);
  const [checkLogFilters, setCheckLogFilters] = useState({ district: "", checkType: "" });
  const [checkLogSort, setCheckLogSort] = useState({ field: "checked_at", dir: -1 });
  const [checkNowRunning, setCheckNowRunning] = useState(false);
  const [checkNowMessage, setCheckNowMessage] = useState(null);

  async function fetchCheckLog() {
    setCheckLogLoading(true);
    try {
      const params = new URLSearchParams({
        sort_field: checkLogSort.field, sort_dir: String(checkLogSort.dir), limit: "200",
      });
      if (checkLogFilters.district) params.set("district", checkLogFilters.district);
      if (checkLogFilters.checkType) params.set("check_type", checkLogFilters.checkType);
      const data = await fetchJson(apiBase, `/api/districts/monitor/checks?${params.toString()}`);
      setCheckLog(data);
    } catch (e) {
      // leave whatever was already loaded on screen
    } finally {
      setCheckLogLoading(false);
    }
  }

  function toggleCheckLogSort(field) {
    setCheckLogSort((prev) => ({
      field, dir: prev.field === field ? -prev.dir : -1,
    }));
  }

  async function triggerCheckNow() {
    setCheckNowRunning(true);
    setCheckNowMessage(null);
    try {
      const res = await fetch(`${apiBase}/api/districts/monitor/check-now`, { method: "POST" });
      if (res.status === 429) {
        const body = await res.json();
        setCheckNowMessage(`Cooldown: ${body.detail}`);
      } else if (!res.ok) {
        setCheckNowMessage(`Check failed: HTTP ${res.status}`);
      } else {
        const summary = await res.json();
        setCheckNowMessage(`Done - ${summary.up} up, ${summary.down} down, ${summary.check_error} check errors`);
        // refresh everything so the new manual entries show up immediately
        await Promise.all([fetchMonitor31(), fetchCheckLog()]);
      }
    } catch (e) {
      setCheckNowMessage("Check failed: could not reach the backend");
    } finally {
      setCheckNowRunning(false);
    }
  }

  // Near-real-time feel for the background scheduler too, not just the
  // manual trigger - light polling while this tab is actually open.
  useEffect(() => {
    if (tab !== "monitor31") return;
    const id = setInterval(() => {
      fetchMonitor31();
      fetchCheckLog();
    }, 60000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  useEffect(() => {
    if (tab === "monitor31") fetchCheckLog();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, checkLogFilters, checkLogSort]);

  // History range a person can pick to answer "was it working at some
  // point in the past" - independent of the other tabs, so switching
  // ranges doesn't re-fetch everything else on the dashboard.
  const HISTORY_RANGES = [
    { label: "24h", hours: 24 },
    { label: "48h", hours: 48 },
    { label: "7d", hours: 24 * 7 },
    { label: "30d", hours: 24 * 30 },
  ];
  // At 30-min cadence, 30 days is ~1,440 checks - too many to render as
  // individual bars usefully. Cap the BAR display, never the outage log
  // (a table scales fine and is the actual "when was it down" answer).
  const MAX_TIMELINE_BARS = 200;

  async function fetchTimeline(hours) {
    setTimelineLoading(true);
    try {
      const timeline = await fetchJson(apiBase, `/api/reliability/timeline?hours=${hours}`);
      setTimelineEvents((timeline.events || []).map(mapTimelineEvent));
      setOutages((timeline.outages || []).map(mapOutage));
    } catch (e) {
      // leave whatever history was already loaded on screen rather than blanking it
    } finally {
      setTimelineLoading(false);
    }
  }

  function selectHistoryRange(hours) {
    setTimelineHours(hours);
    fetchTimeline(hours);
  }

  async function connect() {
    setConnection("loading");
    try {
      const [districts, cost, reliability, failures, timeline] = await Promise.all([
        fetchJson(apiBase, "/api/districts?days=1"),
        fetchJson(apiBase, "/api/cost?days=14"),
        fetchJson(apiBase, "/api/reliability?days=30"),
        fetchJson(apiBase, "/api/failures/recent?limit=6"),
        fetchJson(apiBase, `/api/reliability/timeline?hours=${timelineHours}`),
      ]);
      setDistrictData(districts.map(mapDistrict));
      setCostTrend(cost.map(mapCostDay));
      setReliabilityTrend(reliability.map(mapReliabilityDay));
      setFailureLog(failures.map(mapFailure));
      setTimelineEvents((timeline.events || []).map(mapTimelineEvent));
      setOutages((timeline.outages || []).map(mapOutage));
      setConnection("live");
    } catch (e) {
      setConnection("offline");
    }
  }

  useEffect(() => {
    connect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Lazy-loaded only when the tab is actually opened - a separate
  // dataset from the rest of the dashboard, no need to fetch it upfront.
  useEffect(() => {
    if (tab === "monitor31" && districtSnapshot === null) {
      fetchMonitor31();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const okCount = districtData.filter((d) => d.status === "ok").length;
  const degradedCount = districtData.filter((d) => d.status === "degraded").length;
  const failCount = districtData.filter((d) => d.status === "fail").length;
  const overallUptime = reliabilityTrend.length ? reliabilityTrend[reliabilityTrend.length - 1].uptime : 0;
  const todayCost = costTrend.length ? costTrend[costTrend.length - 1] : { proxy: 0, captcha: 0, compute: 0 };
  const todayCostTotal = todayCost.proxy + todayCost.captcha + todayCost.compute;
  const totalFetches = districtData.reduce((a, d) => a + d.fetches, 0);
  const avgRtc = districtData.length ? Math.round(districtData.reduce((a, d) => a + d.rtcRate, 0) / districtData.length) : 0;
  const avgMut = districtData.length ? Math.round(districtData.reduce((a, d) => a + d.mutRate, 0) / districtData.length) : 0;

  return (
    <div
      style={{
        background: T.paper,
        color: T.ink,
        minHeight: "100%",
        fontFamily: "'Inter', sans-serif",
        padding: "28px 24px 40px",
      }}
    >
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Zilla+Slab:wght@500;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap');
        .bm-tab { cursor: pointer; }
        .bm-tab:focus-visible, .bm-district:focus-visible { outline: 2px solid ${T.navy}; outline-offset: 2px; }
        @media (max-width: 720px) {
          .bm-grid { grid-template-columns: 1fr !important; }
          .bm-districts { grid-template-columns: repeat(2, 1fr) !important; }
        }
      `}</style>

      {/* Header */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "flex-end",
        flexWrap: "wrap", gap: 16, borderBottom: `2px solid ${T.ink}`, paddingBottom: 16, marginBottom: 20,
      }}>
        <div>
          <div style={{ fontSize: 11, letterSpacing: "0.18em", color: T.inkSoft, fontFamily: "'JetBrains Mono', monospace", marginBottom: 4 }}>
            KARNATAKA LAND RECORDS · SCRAPE REGISTER
          </div>
          <h1 style={{
            fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 30, margin: 0, color: T.navy,
          }}>
            Bhoomi Monitor
          </h1>
        </div>
        <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          {[
            { label: "Portal uptime (30d)", value: `${overallUptime}%`, color: overallUptime > 88 ? T.ok : T.warn },
            { label: "Today's cost", value: `₹${todayCostTotal}`, color: T.navy },
            { label: "Fetches (30d)", value: totalFetches.toLocaleString("en-IN"), color: T.navy },
          ].map((s) => (
            <div key={s.label} style={{ textAlign: "right" }}>
              <div style={{ fontSize: 10, color: T.inkSoft, textTransform: "uppercase", letterSpacing: "0.06em" }}>{s.label}</div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: 22, color: s.color }}>{s.value}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Connection status */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10, marginBottom: 18,
        fontSize: 12, color: T.inkSoft, flexWrap: "wrap",
      }}>
        <span style={{
          width: 8, height: 8, borderRadius: "50%",
          background: connection === "live" ? T.ok : connection === "loading" ? T.warn : T.fail,
          display: "inline-block",
        }} />
        <span>
          {connection === "live" && "Live — connected to backend"}
          {connection === "loading" && "Connecting to backend…"}
          {connection === "offline" && "Backend unreachable — showing sample data"}
          {connection === "idle" && "Not connected"}
        </span>
        <input
          value={apiBase}
          onChange={(e) => setApiBase(e.target.value)}
          style={{
            fontFamily: "'JetBrains Mono', monospace", fontSize: 11, padding: "3px 6px",
            border: `1px solid ${T.rule}`, borderRadius: 4, background: T.paperDeep, color: T.ink, width: 200,
          }}
        />
        <button
          onClick={connect}
          style={{
            fontSize: 11, padding: "4px 10px", borderRadius: 4, border: `1px solid ${T.navy}`,
            background: "none", color: T.navy, cursor: "pointer", fontWeight: 600,
          }}
        >
          Retry
        </button>
      </div>

      {/* District status summary strip */}
      <div style={{ display: "flex", gap: 10, marginBottom: 24, flexWrap: "wrap" }}>
        {[
          { label: "Cleared", count: okCount, status: "ok" },
          { label: "Partial", count: degradedCount, status: "degraded" },
          { label: "Rejected", count: failCount, status: "fail" },
        ].map((s) => (
          <div key={s.label} style={{
            background: statusBg(s.status), border: `1px solid ${statusColor(s.status)}`,
            borderRadius: 6, padding: "8px 14px", display: "flex", alignItems: "baseline", gap: 8,
          }}>
            <span style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: 18, color: statusColor(s.status) }}>{s.count}</span>
            <span style={{ fontSize: 12, color: T.ink }}>{s.label} districts</span>
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div role="tablist" style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: `1px solid ${T.rule}` }}>
        {[
          { id: "districts", label: "District Ledger" },
          { id: "services", label: "RTC / Mutation" },
          { id: "cost", label: "Fetch Cost" },
          { id: "reliability", label: "Portal Reliability" },
          { id: "monitor31", label: "31-District Monitor" },
        ].map((tb) => (
          <button
            key={tb.id}
            role="tab"
            className="bm-tab"
            aria-selected={tab === tb.id}
            onClick={() => setTab(tb.id)}
            style={{
              background: "none", border: "none", cursor: "pointer",
              padding: "10px 14px", fontSize: 13, fontWeight: 600,
              fontFamily: "'Inter', sans-serif",
              color: tab === tb.id ? T.navy : T.inkSoft,
              borderBottom: tab === tb.id ? `2px solid ${T.navy}` : "2px solid transparent",
              marginBottom: -1,
            }}
          >
            {tb.label}
          </button>
        ))}
      </div>

      {/* ---------- District Ledger ---------- */}
      {tab === "districts" && (
        <div>
          <div style={{ fontSize: 12, color: T.inkSoft, marginBottom: 12 }}>
            Each tile is one district's stamp for the last sync — sorted worst to best.
          </div>
          <div className="bm-districts" style={{
            display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10,
          }}>
            {districtData.map((d) => (
              <div
                key={d.name}
                className="bm-district"
                tabIndex={0}
                style={{
                  background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 6,
                  padding: "10px 12px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
                  <span style={{ fontWeight: 600, fontSize: 13, color: T.ink }}>{d.name}</span>
                  <Stamp status={d.status} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: T.inkSoft }}>
                  <span>RTC {d.rtcRate}%</span>
                  <span>Mut {d.mutRate}%</span>
                  <span>{d.fetches} fetches</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ---------- RTC / Mutation ---------- */}
      {tab === "services" && (
        <div className="bm-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
          <div style={{ background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 8, padding: 16 }}>
            <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 4 }}>
              RTC fetch success
            </div>
            <div style={{ fontSize: 11, color: T.inkSoft, marginBottom: 12 }}>
              Average across all districts: <strong style={{ color: T.ink }}>{avgRtc}%</strong>
            </div>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={districtData.slice(0, 12)} layout="vertical" margin={{ left: 10 }}>
                <CartesianGrid stroke={T.rule} horizontal={false} />
                <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 10, fill: T.inkSoft }} />
                <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 10, fill: T.ink }} />
                <Tooltip contentStyle={{ background: T.paper, border: `1px solid ${T.rule}`, fontSize: 12 }} />
                <Bar dataKey="rtcRate" radius={[0, 3, 3, 0]}>
                  {districtData.slice(0, 12).map((d, i) => (
                    <Cell key={i} fill={d.rtcRate < 70 ? T.fail : d.rtcRate < 88 ? T.warn : T.ok} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div style={{ background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 8, padding: 16 }}>
            <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 4 }}>
              Mutation fetch success
            </div>
            <div style={{ fontSize: 11, color: T.inkSoft, marginBottom: 12 }}>
              Average across all districts: <strong style={{ color: T.ink }}>{avgMut}%</strong> — lower than RTC; mutation pages carry heavier CAPTCHA gating.
            </div>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={districtData.slice(0, 12)} layout="vertical" margin={{ left: 10 }}>
                <CartesianGrid stroke={T.rule} horizontal={false} />
                <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 10, fill: T.inkSoft }} />
                <YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 10, fill: T.ink }} />
                <Tooltip contentStyle={{ background: T.paper, border: `1px solid ${T.rule}`, fontSize: 12 }} />
                <Bar dataKey="mutRate" radius={[0, 3, 3, 0]}>
                  {districtData.slice(0, 12).map((d, i) => (
                    <Cell key={i} fill={d.mutRate < 70 ? T.fail : d.mutRate < 88 ? T.warn : T.ok} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div style={{ gridColumn: "1 / -1", background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 8, padding: 16 }}>
            <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 10 }}>
              Recent failures
            </div>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${T.rule}`, textAlign: "left", color: T.inkSoft }}>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>Time</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>District</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>Service</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>Reason</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600, textAlign: "right" }}>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {failureLog.map((f, i) => (
                    <tr key={i} style={{ borderBottom: `1px solid ${T.rule}` }}>
                      <td style={{ padding: "6px 8px", fontFamily: "'JetBrains Mono', monospace" }}>{f.time}</td>
                      <td style={{ padding: "6px 8px" }}>{f.district}</td>
                      <td style={{ padding: "6px 8px" }}>{f.service}</td>
                      <td style={{ padding: "6px 8px", color: T.inkSoft }}>{f.reason}</td>
                      <td style={{ padding: "6px 8px", textAlign: "right", fontFamily: "'JetBrains Mono', monospace" }}>{f.cost}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ---------- Cost ---------- */}
      {tab === "cost" && (
        <div>
          <div style={{ display: "flex", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
            {[
              { label: "Proxy", value: todayCost.proxy, color: T.navy },
              { label: "CAPTCHA solve", value: todayCost.captcha, color: T.warn },
              { label: "Compute", value: todayCost.compute, color: T.inkSoft },
            ].map((c) => (
              <div key={c.label} style={{ background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 6, padding: "10px 16px" }}>
                <div style={{ fontSize: 10, color: T.inkSoft, textTransform: "uppercase" }}>{c.label} · today</div>
                <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: 18, color: c.color }}>₹{c.value}</div>
              </div>
            ))}
            <div style={{ background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 6, padding: "10px 16px" }}>
              <div style={{ fontSize: 10, color: T.inkSoft, textTransform: "uppercase" }}>Cost per fetch (avg)</div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: 18, color: T.ink }}>
                ₹{(todayCostTotal / (totalFetches / 30)).toFixed(2)}
              </div>
            </div>
          </div>
          <div style={{ background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 8, padding: 16 }}>
            <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 12 }}>
              Daily cost breakdown — last 14 days
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={costTrend}>
                <CartesianGrid stroke={T.rule} vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 10, fill: T.inkSoft }} />
                <YAxis tick={{ fontSize: 10, fill: T.inkSoft }} />
                <Tooltip contentStyle={{ background: T.paper, border: `1px solid ${T.rule}`, fontSize: 12 }} />
                <Bar dataKey="proxy" stackId="c" fill={T.navy} name="Proxy" />
                <Bar dataKey="captcha" stackId="c" fill={T.warn} name="CAPTCHA solve" />
                <Bar dataKey="compute" stackId="c" fill={T.inkSoft} name="Compute" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* ---------- Reliability ---------- */}
      {tab === "reliability" && (
        <div style={{ background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 8, padding: 16 }}>
          <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 4 }}>
            Portal uptime — last 30 days
          </div>
          <div style={{ fontSize: 11, color: T.inkSoft, marginBottom: 12 }}>
            Share of fetch attempts (any district, any service) that returned valid data without retry.
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={reliabilityTrend}>
              <defs>
                <linearGradient id="upt" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={T.navy} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={T.navy} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={T.rule} vertical={false} />
              <XAxis dataKey="day" tick={{ fontSize: 10, fill: T.inkSoft }} interval={2} />
              <YAxis domain={[70, 100]} tick={{ fontSize: 10, fill: T.inkSoft }} />
              <Tooltip contentStyle={{ background: T.paper, border: `1px solid ${T.rule}`, fontSize: 12 }} />
              <Area type="monotone" dataKey="uptime" stroke={T.navy} strokeWidth={2} fill="url(#upt)" />
            </AreaChart>
          </ResponsiveContainer>

          {/* History range picker - a person checking at 10pm can look
              back 24h/48h/7d/30d to see exactly when it was up or down,
              not just the fixed recent window. */}
          <div style={{ marginTop: 24, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy }}>
              History
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              {HISTORY_RANGES.map((r) => (
                <button
                  key={r.hours}
                  onClick={() => selectHistoryRange(r.hours)}
                  disabled={timelineLoading}
                  style={{
                    fontSize: 11, padding: "4px 10px", borderRadius: 4,
                    border: `1px solid ${timelineHours === r.hours ? T.navy : T.rule}`,
                    background: timelineHours === r.hours ? T.navy : "none",
                    color: timelineHours === r.hours ? "white" : T.inkSoft,
                    cursor: timelineLoading ? "wait" : "pointer", fontWeight: 600,
                  }}
                >
                  {r.label}
                </button>
              ))}
            </div>
            {timelineLoading && <span style={{ fontSize: 11, color: T.inkSoft }}>loading…</span>}
          </div>

          {/* Fine-grained status timeline - answers "was it down at some
              specific time", which the daily-average chart above can't.
              One segment per health check (health_monitor.py, ~every 30
              min); hover a segment for the exact time. */}
          <div style={{ marginTop: 16 }}>
            <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 14, color: T.navy, marginBottom: 4 }}>
              Status timeline
            </div>
            <div style={{ fontSize: 11, color: T.inkSoft, marginBottom: 10 }}>
              Each bar is one automated health check. Green = up, red = down. Hover for the exact time.
              {timelineEvents.length > MAX_TIMELINE_BARS &&
                ` Showing the most recent ${MAX_TIMELINE_BARS} of ${timelineEvents.length} checks in this range — the outage log below covers all of them.`}
            </div>
            {timelineEvents.length === 0 ? (
              <div style={{ fontSize: 12, color: T.inkSoft, fontStyle: "italic" }}>
                No health-check history yet for this range — once health_monitor.py has been running a
                while, its results show up here.
              </div>
            ) : (
              <div style={{ display: "flex", gap: 2, flexWrap: "wrap", alignItems: "flex-end" }}>
                {timelineEvents.slice(-MAX_TIMELINE_BARS).map((e, i) => (
                  <div
                    key={i}
                    title={`${new Date(e.timestamp).toLocaleString("en-IN")} — ${
                      e.status === "success" ? "UP" : `DOWN (${e.errorType || "unknown"})`
                    }`}
                    style={{
                      width: 9, height: 24, borderRadius: 2,
                      background: e.status === "success" ? T.ok : T.fail,
                    }}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Outage log - the direct, no-hovering-required answer to
              "when exactly was it down", derived server-side (aggregate.py)
              from consecutive failed checks so every consumer sees the
              same windows. */}
          <div style={{ marginTop: 24 }}>
            <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 8 }}>
              Outage log — last {HISTORY_RANGES.find((r) => r.hours === timelineHours)?.label || `${timelineHours}h`}
            </div>
            {outages.length === 0 ? (
              <div style={{ fontSize: 12, color: T.ok }}>No outages detected in this window.</div>
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${T.rule}`, textAlign: "left", color: T.inkSoft }}>
                      <th style={{ padding: "6px 8px", fontWeight: 600 }}>From</th>
                      <th style={{ padding: "6px 8px", fontWeight: 600 }}>To</th>
                      <th style={{ padding: "6px 8px", fontWeight: 600 }}>Checks failed</th>
                      <th style={{ padding: "6px 8px", fontWeight: 600 }}>Reason(s)</th>
                      <th style={{ padding: "6px 8px", fontWeight: 600 }}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {outages.map((o, i) => (
                      <tr key={i} style={{ borderBottom: `1px solid ${T.rule}` }}>
                        <td style={{ padding: "6px 8px", fontFamily: "'JetBrains Mono', monospace" }}>
                          {new Date(o.start).toLocaleString("en-IN")}
                        </td>
                        <td style={{ padding: "6px 8px", fontFamily: "'JetBrains Mono', monospace" }}>
                          {new Date(o.end).toLocaleString("en-IN")}
                        </td>
                        <td style={{ padding: "6px 8px" }}>{o.count}</td>
                        <td style={{ padding: "6px 8px", color: T.inkSoft }}>{o.reasons.join(", ")}</td>
                        <td style={{ padding: "6px 8px", color: T.fail, fontWeight: 700, fontSize: 10 }}>
                          {o.ongoing ? "● ONGOING" : ""}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Check log - every single health check, success AND failure,
              not just the collapsed outage windows above. Newest first,
              in a scrollable panel (not truncated) so a long history
              range doesn't blow up page height or drop rows. */}
          <div style={{ marginTop: 24 }}>
            <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 8 }}>
              Check log — {timelineEvents.length} check{timelineEvents.length === 1 ? "" : "s"} in the last{" "}
              {HISTORY_RANGES.find((r) => r.hours === timelineHours)?.label || `${timelineHours}h`}
            </div>
            {timelineEvents.length === 0 ? (
              <div style={{ fontSize: 12, color: T.inkSoft, fontStyle: "italic" }}>
                No health checks recorded yet in this window.
              </div>
            ) : (
              <div style={{ overflowX: "auto", maxHeight: 360, overflowY: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${T.rule}`, textAlign: "left", color: T.inkSoft }}>
                      <th style={{ padding: "6px 8px", fontWeight: 600, position: "sticky", top: 0, background: T.paperDeep }}>Time</th>
                      <th style={{ padding: "6px 8px", fontWeight: 600, position: "sticky", top: 0, background: T.paperDeep }}>District</th>
                      <th style={{ padding: "6px 8px", fontWeight: 600, position: "sticky", top: 0, background: T.paperDeep }}>Result</th>
                      <th style={{ padding: "6px 8px", fontWeight: 600, position: "sticky", top: 0, background: T.paperDeep }}>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...timelineEvents].reverse().map((e, i) => (
                      <tr key={i} style={{ borderBottom: `1px solid ${T.rule}` }}>
                        <td style={{ padding: "6px 8px", fontFamily: "'JetBrains Mono', monospace" }}>
                          {new Date(e.timestamp).toLocaleString("en-IN")}
                        </td>
                        <td style={{ padding: "6px 8px" }}>{e.district}</td>
                        <td style={{ padding: "6px 8px" }}>
                          <span style={{
                            fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: 10,
                            color: e.status === "success" ? T.ok : T.fail,
                          }}>
                            {e.status === "success" ? "UP" : "DOWN"}
                          </span>
                        </td>
                        <td style={{ padding: "6px 8px", color: T.inkSoft }}>{e.errorType || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ---------- 31-District Monitor ---------- */}
      {tab === "monitor31" && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: T.inkSoft }}>
              Lightweight reachability check (district selected → taluk list responds), all 31 canonical
              districts, every 30 minutes. Separate from the deeper RTC-record checks on the Reliability tab.
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginLeft: 12, flexShrink: 0 }}>
              {checkNowMessage && (
                <span style={{ fontSize: 11, color: checkNowMessage.startsWith("Done") ? T.ok : T.warn, whiteSpace: "nowrap" }}>
                  {checkNowMessage}
                </span>
              )}
              <button
                onClick={triggerCheckNow}
                disabled={checkNowRunning}
                title="Runs the same 31-district sweep the 30-min scheduler uses, right now, on demand"
                style={{
                  fontSize: 11, padding: "4px 10px", borderRadius: 4, border: `1px solid ${T.navy}`,
                  background: checkNowRunning ? T.paperDeep : T.navy, color: checkNowRunning ? T.navy : "white",
                  cursor: checkNowRunning ? "wait" : "pointer", fontWeight: 700, whiteSpace: "nowrap",
                }}
              >
                {checkNowRunning ? "Checking all 31…" : "Check Now"}
              </button>
              <button
                onClick={fetchMonitor31}
                disabled={monitor31Loading}
                style={{
                  fontSize: 11, padding: "4px 10px", borderRadius: 4, border: `1px solid ${T.navy}`,
                  background: "none", color: T.navy, cursor: monitor31Loading ? "wait" : "pointer", fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
              >
                {monitor31Loading ? "Loading…" : "Refresh"}
              </button>
            </div>
          </div>

          {districtReport && (
            <div style={{ display: "flex", gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
              {[
                { label: "Overall uptime", value: districtReport.overallUptimePercent != null ? `${districtReport.overallUptimePercent}%` : "—",
                  color: districtReport.overallUptimePercent >= 88 ? T.ok : districtReport.overallUptimePercent >= 70 ? T.warn : T.fail },
                { label: "Districts monitored", value: `${districtReport.districtsMonitored}/31`, color: T.navy },
                { label: "Total incidents", value: districtReport.totalIncidents, color: districtReport.totalIncidents > 0 ? T.warn : T.ok },
                { label: "Best uptime", value: districtReport.districtWithHighestUptime || "—", color: T.ok },
                { label: "Worst uptime", value: districtReport.districtWithLowestUptime || "—", color: T.inkSoft },
              ].map((s) => (
                <div key={s.label} style={{ background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 6, padding: "8px 14px" }}>
                  <div style={{ fontSize: 10, color: T.inkSoft, textTransform: "uppercase" }}>{s.label}</div>
                  <div style={{ fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, fontSize: 16, color: s.color }}>{s.value}</div>
                </div>
              ))}
            </div>
          )}

          <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 4 }}>
            Live snapshot{districtSnapshot?.scheduled_for ? ` — checkpoint ${new Date(districtSnapshot.scheduled_for).toLocaleString("en-IN")}` : ""}
          </div>
          <div style={{ fontSize: 11, color: T.inkSoft, marginBottom: 12 }}>
            Every one of the 31 districts, always - a district with no check at this checkpoint shows as
            "NOT CHECKED", never silently omitted.
          </div>

          {!districtSnapshot ? (
            <div style={{ fontSize: 12, color: T.inkSoft, fontStyle: "italic" }}>
              {monitor31Loading ? "Loading…" : "No data yet - click Refresh once district_monitor.py has run at least once."}
            </div>
          ) : (
            <div className="bm-districts" style={{
              display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginBottom: 24,
            }}>
              {districtSnapshot.districts.map((d) => (
                <div
                  key={d.district}
                  style={{ background: T.paperDeep, border: `1px solid ${T.rule}`, borderRadius: 6, padding: "10px 12px" }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
                    <span style={{ fontWeight: 600, fontSize: 13, color: T.ink }}>{d.district}</span>
                    <span style={{
                      fontFamily: "'JetBrains Mono', monospace", fontSize: 9, fontWeight: 700,
                      color: districtCheckColor(d.status), border: `1px solid ${districtCheckColor(d.status)}`,
                      borderRadius: 3, padding: "1px 5px", background: districtCheckBg(d.status),
                    }}>
                      {districtCheckLabel(d.status)}
                    </span>
                  </div>
                  <div style={{ fontSize: 10, color: T.inkSoft }}>
                    {d.district_code != null && <span>code {d.district_code} · </span>}
                    {d.status === "down" && d.downtime_duration_minutes != null
                      ? `down ${d.downtime_duration_minutes}m`
                      : d.reason || (d.timing_status === "delayed" ? `delayed ${d.delay_minutes}m` : "on time")}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy, marginBottom: 8 }}>
            Recent incidents
          </div>
          {districtIncidents.length === 0 ? (
            <div style={{ fontSize: 12, color: T.ok }}>No incidents recorded.</div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${T.rule}`, textAlign: "left", color: T.inkSoft }}>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>District</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>Start</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>End</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>Duration</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>Reason</th>
                    <th style={{ padding: "6px 8px", fontWeight: 600 }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {districtIncidents.map((inc, i) => (
                    <tr key={i} style={{ borderBottom: `1px solid ${T.rule}` }}>
                      <td style={{ padding: "6px 8px" }}>{inc.district}</td>
                      <td style={{ padding: "6px 8px", fontFamily: "'JetBrains Mono', monospace" }}>
                        {new Date(inc.start_timestamp).toLocaleString("en-IN")}
                      </td>
                      <td style={{ padding: "6px 8px", fontFamily: "'JetBrains Mono', monospace" }}>
                        {inc.end_timestamp ? new Date(inc.end_timestamp).toLocaleString("en-IN") : "—"}
                      </td>
                      <td style={{ padding: "6px 8px" }}>{inc.duration_minutes != null ? `${inc.duration_minutes}m` : "—"}</td>
                      <td style={{ padding: "6px 8px", color: T.inkSoft }}>{inc.reason || "—"}</td>
                      <td style={{ padding: "6px 8px", fontWeight: 700, color: inc.status === "open" ? T.fail : T.ok }}>
                        {inc.status === "open" ? "● OPEN" : "RESOLVED"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Full check log - EVERY check, automatic AND manual, nothing
              hidden by default. Sortable (click a header) and
              filterable by District/Check Type - polls every 60s while
              this tab is open so background scheduler activity shows up
              without a manual refresh, same as a Check Now click does
              immediately. */}
          <div style={{ marginTop: 24 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
              <div style={{ fontFamily: "'Zilla Slab', serif", fontWeight: 700, fontSize: 16, color: T.navy }}>
                Check log — {checkLog.total} check{checkLog.total === 1 ? "" : "s"}
                {checkLogLoading && <span style={{ fontSize: 11, color: T.inkSoft, fontWeight: 400 }}> (loading…)</span>}
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <select
                  value={checkLogFilters.district}
                  onChange={(e) => setCheckLogFilters((f) => ({ ...f, district: e.target.value }))}
                  style={{ fontSize: 11, padding: "3px 6px", border: `1px solid ${T.rule}`, borderRadius: 4, background: T.paper, color: T.ink }}
                >
                  <option value="">All districts</option>
                  {(districtSnapshot?.districts || []).map((d) => (
                    <option key={d.district} value={d.district}>{d.district}</option>
                  ))}
                </select>
                <select
                  value={checkLogFilters.checkType}
                  onChange={(e) => setCheckLogFilters((f) => ({ ...f, checkType: e.target.value }))}
                  style={{ fontSize: 11, padding: "3px 6px", border: `1px solid ${T.rule}`, borderRadius: 4, background: T.paper, color: T.ink }}
                >
                  <option value="">All check types</option>
                  <option value="automatic">Automatic</option>
                  <option value="manual">Manual</option>
                </select>
              </div>
            </div>

            {checkLog.checks.length === 0 ? (
              <div style={{ fontSize: 12, color: T.inkSoft, fontStyle: "italic" }}>
                No checks match the current filters.
              </div>
            ) : (
              <div style={{ overflowX: "auto", maxHeight: 400, overflowY: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead>
                    <tr style={{ borderBottom: `1px solid ${T.rule}`, textAlign: "left", color: T.inkSoft }}>
                      {[
                        { key: "district", label: "District" },
                        { key: "check_type", label: "Check Type" },
                        { key: "checked_at", label: "Checked At" },
                        { key: "status", label: "Status" },
                      ].map((col) => (
                        <th
                          key={col.key}
                          onClick={() => toggleCheckLogSort(col.key)}
                          style={{ padding: "6px 8px", fontWeight: 600, cursor: "pointer", position: "sticky", top: 0, background: T.paperDeep, userSelect: "none" }}
                          title="Click to sort"
                        >
                          {col.label}{checkLogSort.field === col.key ? (checkLogSort.dir === 1 ? " ▲" : " ▼") : ""}
                        </th>
                      ))}
                      <th style={{ padding: "6px 8px", fontWeight: 600, position: "sticky", top: 0, background: T.paperDeep }}>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {checkLog.checks.map((c, i) => (
                      <tr key={i} style={{ borderBottom: `1px solid ${T.rule}` }}>
                        <td style={{ padding: "6px 8px" }}>{c.district}</td>
                        <td style={{ padding: "6px 8px" }}>
                          <span style={{
                            fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700,
                            color: c.check_type === "manual" ? T.navy : T.inkSoft,
                          }}>
                            {c.check_type === "manual" ? "MANUAL" : "AUTOMATIC"}
                          </span>
                        </td>
                        <td style={{ padding: "6px 8px", fontFamily: "'JetBrains Mono', monospace" }}>
                          {new Date(c.checked_at).toLocaleString("en-IN")}
                        </td>
                        <td style={{ padding: "6px 8px" }}>
                          <span style={{
                            fontFamily: "'JetBrains Mono', monospace", fontSize: 10, fontWeight: 700,
                            color: districtCheckColor(c.status),
                          }}>
                            {districtCheckLabel(c.status)}
                          </span>
                        </td>
                        <td style={{ padding: "6px 8px", color: T.inkSoft }}>{c.reason || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
