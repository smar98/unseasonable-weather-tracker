"use strict";

/* Unseasonable — India seasonal anomaly tracker (frontend v2)
   Data contract: public/data/index.json + public/data/cities/<id>.json,
   produced by scripts/build_dataset.py (schema 2.x). All charts are
   hand-built SVG in the Field Brief palette. */

const INDEX_URL = "./public/data/index.json";
const CITY_URL = (id) => `./public/data/cities/${id}.json`;
const LIVE_REFRESH_MS = 10 * 60 * 1000;

const COLORS = {
  heat: "#c25541",
  cold: "#4478b8",
  precip: "#159b7e",
  snow: "#9b6fc7",
  ochre: "#c77d1e",
  charcoal: "#20262e",
  slate: "#5f6d75",
  mist: "#8ea0a8",
  band: "#e7ecef",
  navy: "#17324d",
};

/* Labels are season-relative on purpose: a flag means "unusual FOR THIS
   place and season", not an absolute extreme. Calling a 5.7°C Himalayan
   day "extreme heat" (it cleared the local summer p95) reads as absurd and
   discredits the tool; "unusually warm for the season" is what it means. */
const FLAG_META = {
  hot_extreme: { label: "Unusually warm", cls: "heat", color: COLORS.heat, rank: 0 },
  cold_extreme: { label: "Unusually cold", cls: "cold", color: COLORS.cold, rank: 1 },
  rare_snow: { label: "Rare-season snow", cls: "snow", color: COLORS.snow, rank: 2 },
  exceptional_snow: { label: "Exceptional snowfall", cls: "snow", color: COLORS.snow, rank: 3 },
  heavy_precip: { label: "Unusually wet", cls: "precip", color: COLORS.precip, rank: 4 },
  warm_day: { label: "Warm day", cls: "heat", color: COLORS.heat, rank: 5 },
  cold_night: { label: "Cold night", cls: "cold", color: COLORS.cold, rank: 6 },
};

const VALIDATION_META = {
  validated: { label: "Validated", cls: "v-validated" },
  corroborated: { label: "Corroborated", cls: "v-corroborated" },
  unverified: { label: "No public record", cls: "v-unverified" }, // searched, nothing found
  not_checked: { label: "Not yet checked", cls: "v-unverified" }, // default, not researched
  contradicted: { label: "Contradicted", cls: "v-contradicted" },
};

// map/list dot colour = the city's overall state (its verdict tone), NOT the
// type of its most recent flag — so a near-normal city reads grey, not red
const TONE_COLOR = {
  warm: COLORS.heat,
  cool: COLORS.cold,
  swing: COLORS.ochre,
  calm: "#aeb9bf",
  neutral: "#cdd5da",
};
const TONE_LABEL = {
  warm: "Running warmer",
  cool: "Running cooler",
  swing: "Both extremes",
  calm: "Quieter than normal",
  neutral: "Near normal",
};

const WEATHER_CODES = {
  0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
  45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
  55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
  71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
  80: "Light showers", 81: "Showers", 82: "Violent showers",
  85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
  96: "Thunderstorm w/ hail", 99: "Severe thunderstorm w/ hail",
};

const state = {
  index: null,
  cities: new Map(),
  selectedId: null,
  map: null,
  markers: new Map(),
  liveTimer: null,
};

/* ---------- null-safe DOM helpers (a render bug must never look like a
   data failure — lesson from v1) ---------- */

function byId(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = value;
}

function setHtml(id, value) {
  const node = byId(id);
  if (node) node.innerHTML = value;
}

function esc(value) {
  return String(value == null ? "" : value)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fmtDate(iso) {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

function pctLabel(p) {
  if (p == null) return "–";
  return `${(p * 100).toFixed(1)}th`;
}

function mean(values) {
  const clean = values.filter((v) => v != null && !Number.isNaN(v));
  return clean.length ? clean.reduce((a, b) => a + b, 0) / clean.length : null;
}

/* ---------- plain-language "so-what" generation ----------
   Every takeaway is a deterministic template with numeric cutoffs, so it is
   auditable and fails predictably. Statistical anchor: warm-day count over
   365d is ~Binomial(365, 0.10), sigma ~= 5.7, so +/-9 days ~= 1.5 sigma is a
   defensible "notable" threshold. The sign of every gap drives the wording:
   BELOW expected means calmer-than-normal, never "alarming". */

const NOTABLE_TEMP = 9;   // warm/cold day count departure (~1.5 sigma)
const NOTABLE_PRECIP = 4; // heavy-rain day departure

function cityStats(city) {
  const k = city.kpis;
  const warmExcess = (k.warm_days ?? 0) - (k.warm_days_expected ?? 0);
  const coldExcess = (k.cold_nights ?? 0) - (k.cold_nights_expected ?? 0);
  const qual = city.annual.filter((a) => a.coverage_days >= 350 && a.warm_day_fraction != null);
  const last10 = qual.slice(-10);
  const recentWarm = last10.length >= 8 ? mean(last10.map((a) => a.warm_day_fraction)) : null;
  const recentCold = last10.length >= 8 ? mean(last10.map((a) => a.cold_night_fraction)) : null;
  const recentAnom = last10.length >= 8 ? mean(last10.map((a) => a.mean_tmax_anom)) : null;
  const snowDays = (k.rare_snow_days || 0) + (k.exceptional_snow_days || 0);
  return { k, warmExcess, coldExcess, recentWarm, recentCold, recentAnom, snowDays };
}

// tone drives the banner tint, the KPI colours and the map dots. Works off
// the same kpis object present in both index entries and city files.
function cityTone(kpis) {
  const we = (kpis.warm_days ?? 0) - (kpis.warm_days_expected ?? 0);
  const ce = (kpis.cold_nights ?? 0) - (kpis.cold_nights_expected ?? 0);
  if (we >= NOTABLE_TEMP && ce >= NOTABLE_TEMP) return "swing";
  if (we >= NOTABLE_TEMP) return "warm";
  if (ce >= NOTABLE_TEMP) return "cool";
  if (we <= -NOTABLE_TEMP && ce <= -NOTABLE_TEMP) return "calm";
  return "neutral";
}

const TONE_HEADLINE = {
  swing: "has been swinging between unusual heat and unusual cold",
  warm: "has been running warmer than its seasonal normal",
  cool: "has been running cooler than its seasonal normal",
  calm: "has been quieter than its seasonal normal",
  neutral: "has stayed close to its seasonal normal",
};

function cityVerdict(city) {
  const s = cityStats(city);
  const tone = cityTone(city.kpis);
  const headline = TONE_HEADLINE[tone];

  const counts =
    `${s.k.warm_days} warm days against about ${s.k.warm_days_expected} expected, ` +
    `and ${s.k.cold_nights} cold nights against about ${s.k.cold_nights_expected}, over the past year`;

  let trend = "";
  if (s.recentWarm != null) {
    if (s.recentWarm >= 0.13) {
      const mult = (s.recentWarm / 0.1).toFixed(1);
      trend = ` Over the last decade, warm days have come about ${mult}× as often as across the 1991–2020 baseline`;
      if (s.recentAnom != null && s.recentAnom >= 0.4) {
        const rounded = Math.round(s.recentAnom * 2) / 2;
        trend += `, and typical daily highs now run roughly ${rounded}°C above it`;
      }
      trend += ".";
    } else if (s.recentWarm <= 0.07) {
      trend = " Over the last decade, warm days have become rarer than across the 1991–2020 baseline.";
    } else {
      trend = " Over the last decade the long-run rate has stayed close to its 1991–2020 baseline.";
    }
  }

  let snow = "";
  if (city.meta.snow_capable && s.snowDays >= 1) {
    snow = ` It also logged ${s.snowDays} snow ${s.snowDays === 1 ? "day" : "days"} outside the seasonal norm.`;
  }

  return {
    tone,
    text: `${city.meta.name} ${headline} — ${counts}.${trend}${snow}`,
  };
}

// one KPI's meaning: sign-correct gap text + which class colours it
function kpiMeaning(count, expected, concernClass, threshold) {
  if (expected == null) return { gap: null, cls: concernClass, tag: "" };
  const diff = count - expected;
  if (diff >= threshold) {
    return { gap: `${diff} more than the ~${expected} expected`, cls: concernClass, tag: "unusual" };
  }
  if (diff <= -threshold) {
    return { gap: `${Math.abs(diff)} fewer than the ~${expected} expected`, cls: "calm", tag: "quieter than normal" };
  }
  return { gap: `about the ~${expected} expected`, cls: "calm", tag: "near normal" };
}

// temperature KPIs need the city's tone: fewer cold nights is a WARM-direction
// signal in a warming city, not a "quiet" one — only both-ends-down (calm) is
// genuinely quiet. Colour reflects climate direction, not just the raw sign.
function tempMeaning(kind, count, expected, tone, threshold) {
  if (expected == null) return { gap: "", cls: "calm", tag: "" };
  const diff = count - expected;
  const more = `${diff} more than the ~${expected} expected`;
  const fewer = `${Math.abs(diff)} fewer than the ~${expected} expected`;
  if (Math.abs(diff) < threshold) return { gap: `about the ~${expected} expected`, cls: "calm", tag: "near normal" };
  if (kind === "warm") {
    if (diff >= threshold) return { gap: more, cls: "heat", tag: "unusually warm" };
    if (tone === "calm") return { gap: fewer, cls: "calm", tag: "quieter than normal" };
    return { gap: fewer, cls: "cold", tag: "fewer warm days" };
  }
  // cold nights
  if (diff >= threshold) return { gap: more, cls: "cold", tag: "more cold nights" };
  if (tone === "calm") return { gap: fewer, cls: "calm", tag: "quieter than normal" };
  if (tone === "warm" || tone === "swing") return { gap: fewer, cls: "heat", tag: "fewer cold nights" };
  return { gap: fewer, cls: "calm", tag: "fewer cold nights" };
}

// per-chart one-liners; nulls where a chart has too little data to speak
function chartTakeaways(city) {
  const s = cityStats(city);
  const k = city.kpis;

  // trend
  let trend;
  if (s.recentWarm == null) {
    trend = "How often each year runs outside the seasonal normal.";
  } else if (s.recentWarm >= 0.13) {
    trend =
      `Warm days now fill about ${Math.round(s.recentWarm * 100)}% of the year — ` +
      `roughly ${(s.recentWarm / 0.1).toFixed(1)}× the 10% the 1991–2020 baseline would give` +
      (s.recentCold != null ? `, while cold nights have thinned to about ${Math.round(s.recentCold * 100)}%.` : ".");
  } else if (s.recentWarm <= 0.07) {
    trend = "Warm days have grown rarer than the 10% the 1991–2020 baseline implies — this place is not running warmer against its own history.";
  } else {
    trend = "Both lines still hover near the 10% the 1991–2020 baseline implies — no clear long-run drift here.";
  }

  // ribbon
  const hot = k.hot_extreme_days || 0;
  const coldFlags = city.last_365.filter((d) => d.f && d.f.includes("cold_extreme")).length;
  let ribbon;
  if (hot === 0 && coldFlags === 0) {
    ribbon = "No day in the past year broke the seasonal envelope — an unremarkable year, which the tool is built to say plainly.";
  } else {
    ribbon =
      `The daily high left the 1991–2020 envelope on ${hot} unusually warm ` +
      `and ${coldFlags} unusually cold ${hot + coldFlags === 1 ? "day" : "days"} in the past year.`;
  }

  // precip
  const pm = kpiMeaning(k.heavy_precip_days || 0, k.heavy_precip_days_expected, "precip", NOTABLE_PRECIP);
  let precip;
  if (pm.tag === "unusual") {
    precip = `${k.heavy_precip_days} heavy-rain days — ${pm.gap}. Rain arrived unusually concentrated.`;
  } else if (pm.tag === "quieter than normal") {
    precip = `${k.heavy_precip_days} heavy-rain days — ${pm.gap}. Fewer downpours than usual.`;
  } else {
    precip = `${k.heavy_precip_days} heavy-rain days, ${pm.gap} — nothing unusual in how the rain fell.`;
  }

  // events
  const vs = state.index.validation_summary;
  const n = city.events.length;
  let events;
  if (!n) {
    events = "No candidate events in the last three years.";
  } else {
    events =
      `${n} candidate ${n === 1 ? "event" : "events"} in three years` +
      (vs && vs.checked
        ? `. Across all cities, ${vs.checked} flags have been checked against independent evidence: ${vs.validated} validated, ${vs.corroborated} corroborated, ${vs.contradicted} contradicted.`
        : ".");
  }

  return { trend, ribbon, precip, events };
}

/* ---------- SVG helpers ---------- */

const SVG_NS = "http://www.w3.org/2000/svg";

function el(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value != null) node.setAttribute(key, value);
  }
  if (parent) parent.appendChild(node);
  return node;
}

function linePath(points) {
  let path = "";
  let pen = false;
  for (const point of points) {
    if (!point) { pen = false; continue; }
    path += `${pen ? "L" : "M"}${point[0].toFixed(1)},${point[1].toFixed(1)}`;
    pen = true;
  }
  return path;
}

function makeTip(wrap) {
  let tip = wrap.querySelector(".chart-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "chart-tip";
    wrap.appendChild(tip);
  }
  return tip;
}

/* Crosshair + tooltip: `locate(fractionX)` returns {html, px, py} or null. */
function attachHover(wrap, svg, width, height, margins, locate) {
  const tip = makeTip(wrap);
  const cross = el("line", {
    y1: margins.top, y2: height - margins.bottom,
    stroke: COLORS.mist, "stroke-width": 1, "stroke-dasharray": "3,3", opacity: 0,
  }, svg);
  const halo = el("circle", { r: 4.5, fill: "none", stroke: COLORS.charcoal, "stroke-width": 1.5, opacity: 0 }, svg);

  function onMove(event) {
    const rect = svg.getBoundingClientRect();
    const fx = ((event.clientX - rect.left) / rect.width) * width;
    const hit = locate(fx);
    if (!hit) { onLeave(); return; }
    cross.setAttribute("x1", hit.px);
    cross.setAttribute("x2", hit.px);
    cross.setAttribute("opacity", 1);
    if (hit.py != null) {
      halo.setAttribute("cx", hit.px);
      halo.setAttribute("cy", hit.py);
      halo.setAttribute("opacity", 1);
    } else {
      halo.setAttribute("opacity", 0);
    }
    tip.innerHTML = hit.html;
    tip.style.opacity = 1;
    const wrapRect = wrap.getBoundingClientRect();
    const tipW = tip.offsetWidth;
    let left = ((hit.px / width) * rect.width) + (rect.left - wrapRect.left) + 14;
    if (left + tipW > wrapRect.width - 8) left = left - tipW - 28;
    tip.style.left = `${Math.max(4, left)}px`;
    tip.style.top = `${event.clientY - wrapRect.top - window.scrollY * 0 + 12}px`;
  }

  function onLeave() {
    tip.style.opacity = 0;
    cross.setAttribute("opacity", 0);
    halo.setAttribute("opacity", 0);
  }

  svg.addEventListener("mousemove", onMove);
  svg.addEventListener("mouseleave", onLeave);
}

/* ---------- boot ---------- */

async function boot() {
  let index;
  try {
    const res = await fetch(INDEX_URL, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    index = await res.json();
  } catch (error) {
    setHtml(
      "detail-status",
      `Dataset could not be loaded (${esc(error.message)}). ` +
      "If running locally: <code>python3 scripts/build_dataset.py</code> then serve the repo root."
    );
    const status = byId("detail-status");
    if (status) status.classList.add("error");
    return;
  }

  state.index = index;
  setText(
    "generated-note",
    `ERA5 via Open-Meteo · updated ${fmtDate(index.generated_at_utc.slice(0, 10))}`
  );

  renderNational();
  renderCityList("");
  initMap();

  const search = byId("city-search");
  if (search) search.addEventListener("input", () => renderCityList(search.value));

  const backdrop = byId("modal-backdrop");
  if (backdrop) {
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) closeModal();
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });

  const fromHash = decodeURIComponent((location.hash || "").replace(/^#/, ""));
  const initial =
    index.cities.find((c) => c.id === fromHash) || pickDefaultCity(index.cities);
  if (initial) selectCity(initial.id);
}

function pickDefaultCity(cities) {
  const flagged = cities
    .filter((c) => c.latest_flag)
    .sort((a, b) => {
      const dateCmp = b.latest_flag.date.localeCompare(a.latest_flag.date);
      if (dateCmp !== 0) return dateCmp;
      return bestRank(a.latest_flag.flags) - bestRank(b.latest_flag.flags);
    });
  return flagged[0] || cities[0];
}

function bestRank(flags) {
  return Math.min(...flags.map((f) => (FLAG_META[f] ? FLAG_META[f].rank : 9)));
}

function bestFlag(flags) {
  return [...flags].sort((a, b) => (FLAG_META[a]?.rank ?? 9) - (FLAG_META[b]?.rank ?? 9))[0];
}

/* ---------- national summary ---------- */

function renderNational() {
  const cities = state.index.cities;
  const recentEnd = state.index.recent_end;
  const weekAgo = addDays(recentEnd, -7);
  const flaggedWeek = cities.filter(
    (c) => c.latest_flag && c.latest_flag.date >= weekAgo
  );
  const notable = [...flaggedWeek]
    .sort(
      (a, b) =>
        b.latest_flag.date.localeCompare(a.latest_flag.date) ||
        bestRank(a.latest_flag.flags) - bestRank(b.latest_flag.flags)
    )
    .slice(0, 4);

  const top = notable[0];
  const topClause = top
    ? ` Most recently flagged: <a href="#${top.id}" data-city="${top.id}">${esc(top.name)}</a> (${(FLAG_META[bestFlag(top.latest_flag.flags)] || {}).label || ""}, ${fmtDate(top.latest_flag.date)}).`
    : "";
  const rows = [
    `<p class="national-verdict">In the last week of data, <b>${flaggedWeek.length} of ${cities.length}</b> cities logged at least one day outside their seasonal normal.${topClause}</p>`,
  ];
  const os = state.index.observed_summary;
  if (os && os.temp_events_checked > 0) {
    const pct = Math.round((os.temp_events_agree / os.temp_events_checked) * 100);
    rows.push(
      `<div class="row"><span>ERA5 vs nearest weather station, recent heat/cold flags</span><b>${pct}% match</b></div>`,
      `<div class="row"><span style="color:var(--slate)">${os.temp_events_agree} of ${os.temp_events_checked} within ${os.tolerance_c}°C, across ${os.cities_with_station} of ${os.cities_total} cities with a station ≤35 km</span></div>`
    );
  }
  const vs = state.index.validation_summary;
  if (vs && vs.checked > 0) {
    rows.push(
      `<div class="row"><span>Flags also hand-checked against news/IMD</span><b>${vs.checked}</b></div>`,
      `<div class="row"><span style="color:var(--slate)">${vs.validated} validated · ${vs.corroborated} corroborated · ${vs.unverified} no public record · ${vs.contradicted} contradicted</span></div>`
    );
  }
  rows.push(renderLeaderboard());
  rows.push(
    `<details class="climate-note"><summary>Could this be a sign of climate change?</summary>` +
    `<p>One unusual day, or one city, is weather — this tool never claims a single event was <em>caused</em> by climate change; that needs attribution modelling it doesn't do. But switch the ranking to <b>Decade</b>: across most cities, warm days now come far more often than the 1991–2020 baseline predicts, and cold nights less often. That direction — more warm extremes, fewer cold ones, almost everywhere at once — is the fingerprint of a warming climate. What you can see here is that drift; proving causation for any one event is a separate, harder question.</p></details>`
  );
  rows.push(
    `<div class="row"><span style="color:var(--mist)">Reanalysis lag: data runs to ${fmtDate(recentEnd)}</span></div>`
  );
  setHtml("national-summary", rows.join(""));
  const box = byId("national-summary");
  if (box) {
    box.querySelectorAll("a[data-city]").forEach((a) =>
      a.addEventListener("click", (event) => {
        event.preventDefault();
        selectCity(a.dataset.city);
      })
    );
    box.querySelectorAll("button[data-tab]").forEach((btn) =>
      btn.addEventListener("click", () => {
        state.leaderTab = btn.dataset.tab;
        renderNational();
      })
    );
  }
}

const LEADER_TABS = {
  week: {
    label: "This week",
    caption: "Most flagged days in the last 7 days of data",
    value: (c) => c.flags_7d || 0,
    fmt: (v) => `${v} ${v === 1 ? "day" : "days"}`,
    ok: (c) => (c.flags_7d || 0) > 0,
  },
  month: {
    label: "This month",
    caption: "Most flagged days in the last 30 days of data",
    value: (c) => c.flags_30d || 0,
    fmt: (v) => `${v} ${v === 1 ? "day" : "days"}`,
    ok: (c) => (c.flags_30d || 0) > 0,
  },
  decade: {
    label: "This decade",
    caption: "Warm days furthest above the 1991–2020 baseline, last 10 years",
    value: (c) => c.recent10_warm ?? 0,
    fmt: (v) => `${(v / 0.1).toFixed(1)}× baseline`,
    ok: (c) => c.recent10_warm != null,
  },
};

function renderLeaderboard() {
  const tab = state.leaderTab || "week";
  const cfg = LEADER_TABS[tab];
  const ranked = state.index.cities
    .filter(cfg.ok)
    .sort((a, b) => cfg.value(b) - cfg.value(a))
    .slice(0, 5);
  const tabs = Object.entries(LEADER_TABS)
    .map(([key, c]) => `<button data-tab="${key}" class="${key === tab ? "active" : ""}">${c.label}</button>`)
    .join("");
  const list = ranked.length
    ? ranked
        .map(
          (c, i) =>
            `<li><span class="rank">${i + 1}</span>` +
            `<a href="#${c.id}" data-city="${c.id}">${esc(c.name)}</a>` +
            `<span class="leader-val">${cfg.fmt(cfg.value(c))}</span></li>`
        )
        .join("")
    : `<li class="leader-empty">No cities flagged in this window.</li>`;
  return (
    `<div class="leader"><div class="leader-tabs">${tabs}</div>` +
    `<p class="leader-caption">Where India is most unusual — ${cfg.caption}.</p>` +
    `<ol class="leader-list">${list}</ol></div>`
  );
}

function addDays(iso, delta) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}

/* ---------- city list ---------- */

function renderCityList(query) {
  const listNode = byId("city-list");
  if (!listNode) return;
  const q = query.trim().toLowerCase();
  const groups = new Map();
  for (const city of state.index.cities) {
    if (q && !`${city.name} ${city.state} ${city.region}`.toLowerCase().includes(q)) continue;
    if (!groups.has(city.region)) groups.set(city.region, []);
    groups.get(city.region).push(city);
  }
  const parts = [];
  for (const [region, cities] of groups) {
    parts.push(`<div class="region-label">${esc(region)}</div>`);
    for (const city of cities) {
      const tone = cityTone(city.kpis);
      const color = TONE_COLOR[tone];
      const active = city.id === state.selectedId ? " active" : "";
      parts.push(
        `<button class="city-row${active}" data-city="${city.id}" title="${TONE_LABEL[tone]}">
          <span class="flag-dot" style="background:${color}"></span>
          <span>${esc(city.name)}</span>
          <span class="sub">${TONE_LABEL[tone]}</span>
        </button>`
      );
    }
  }
  listNode.innerHTML = parts.join("") || `<div class="status-note">No matches.</div>`;
  listNode.querySelectorAll("button[data-city]").forEach((btn) =>
    btn.addEventListener("click", () => selectCity(btn.dataset.city))
  );
}

/* ---------- map ---------- */

function initMap() {
  const mapNode = byId("map");
  if (!mapNode) return;
  if (typeof L === "undefined") {
    mapNode.outerHTML = `<div class="status-note">Map unavailable (Leaflet failed to load). Use the city list below.</div>`;
    return;
  }
  const map = L.map("map", {
    zoomControl: true,
    scrollWheelZoom: false,
    attributionControl: true,
    zoomAnimation: false,
    fadeAnimation: false,
  });
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: "© OpenStreetMap contributors © CARTO",
    subdomains: "abcd",
    maxZoom: 12,
  }).addTo(map);
  const INDIA_BOUNDS = [[7.5, 68.0], [35.5, 93.5]];
  map.fitBounds(INDIA_BOUNDS, { animate: false });
  state.map = map;
  // the container can be measured before fonts/layout settle; re-measure and
  // re-frame once everything has loaded, else the view lands off-centre
  const reframe = () => {
    map.invalidateSize();
    map.fitBounds(INDIA_BOUNDS, { animate: false });
  };
  setTimeout(reframe, 300);
  window.addEventListener("load", () => setTimeout(reframe, 100), { once: true });

  for (const city of state.index.cities) {
    const marker = L.marker([city.latitude, city.longitude], {
      icon: cityIcon(city, false),
      title: city.name,
    }).addTo(map);
    marker.on("click", () => selectCity(city.id));
    marker.bindTooltip(city.name, { direction: "top", offset: [0, -6] });
    state.markers.set(city.id, marker);
  }
}

function cityIcon(city, selected) {
  const color = TONE_COLOR[cityTone(city.kpis)];
  const size = selected ? 18 : 12;
  return L.divIcon({
    className: "",
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    html: `<span class="city-dot" style="width:${size}px;height:${size}px;background:${color};${selected ? "box-shadow:0 0 0 3px rgba(23,50,77,.45);" : ""}"></span>`,
  });
}

function refreshMarkers() {
  if (!state.map) return;
  for (const city of state.index.cities) {
    const marker = state.markers.get(city.id);
    if (marker) marker.setIcon(cityIcon(city, city.id === state.selectedId));
  }
}

/* ---------- city selection ---------- */

async function selectCity(id) {
  if (!state.index) return;
  const entry = state.index.cities.find((c) => c.id === id);
  if (!entry) return;
  state.selectedId = id;
  location.hash = id;
  refreshMarkers();
  renderCityList(byId("city-search") ? byId("city-search").value : "");

  const status = byId("detail-status");
  if (status) {
    status.hidden = false;
    status.classList.remove("error");
    status.textContent = `Loading ${entry.name}…`;
  }

  let city = state.cities.get(id);
  if (!city) {
    try {
      const res = await fetch(CITY_URL(id), { cache: "no-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      city = await res.json();
      state.cities.set(id, city);
    } catch (error) {
      if (status) {
        status.classList.add("error");
        status.textContent = `Could not load ${entry.name}: ${error.message}`;
      }
      return;
    }
  }
  if (state.selectedId !== id) return; // user moved on mid-fetch

  try {
    if (status) status.hidden = true;
    renderHeader(city);
    renderVerdict(city);
    renderKpis(city);
    renderTakeaways(city);
    renderTrendChart(city);
    renderRibbonChart(city);
    renderPrecipChart(city);
    renderTopEvents(city);
    renderEvents(city);
    scheduleLive(city);
  } catch (error) {
    if (status) {
      status.hidden = false;
      status.classList.add("error");
      status.textContent = `Dashboard render error: ${error.message}`;
    }
    console.error(error);
  }
}

/* ---------- header + live ---------- */

function renderHeader(city) {
  const header = byId("city-header");
  if (header) header.hidden = false;
  setText("city-name", city.meta.name);
  setText("city-where", `${city.meta.state} · ${city.meta.region}`);
  // elevation caveat is only load-bearing where the grid cell and the town
  // differ sharply (Himalaya); elsewhere it is noise, so suppress it
  const cellElev = city.meta.grid_cell_elevation_m;
  const townElev = city.meta.town_elevation_m;
  let elevNote = "";
  if (cellElev != null && townElev != null && Math.abs(cellElev - townElev) > 300) {
    elevNote =
      `These values describe the ERA5 grid cell at about <b>${Math.round(cellElev)} m</b>, ` +
      `not the town at ${Math.round(townElev)} m — in steep terrain, read it as the surrounding landscape.`;
  }
  setHtml("city-elev", elevNote);
}

const VERDICT_ICON = { warm: "▲", cool: "▼", calm: "•", swing: "↕", neutral: "•" };

function renderVerdict(city) {
  const node = byId("city-verdict");
  if (!node) return;
  const v = cityVerdict(city);
  node.className = `verdict verdict-${v.tone}`;
  node.innerHTML =
    `<span class="verdict-mark">${VERDICT_ICON[v.tone] || "•"}</span>` +
    `<p class="verdict-text">${esc(v.text)}</p>`;
}

function renderTakeaways(city) {
  const t = chartTakeaways(city);
  setText("trend-title", t.trend);
  setText("ribbon-title", t.ribbon);
  setText("precip-title", t.precip);
  setText("events-title", t.events);
}

function scheduleLive(city) {
  if (state.liveTimer) clearInterval(state.liveTimer);
  fetchLive(city);
  state.liveTimer = setInterval(() => fetchLive(city), LIVE_REFRESH_MS);
}

async function fetchLive(city) {
  const strip = byId("live-strip");
  if (!strip) return;
  const url =
    "https://api.open-meteo.com/v1/forecast" +
    `?latitude=${city.meta.latitude}&longitude=${city.meta.longitude}` +
    "&current=temperature_2m,precipitation,snowfall,weather_code,wind_speed_10m&timezone=Asia%2FKolkata";
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    if (state.selectedId !== city.meta.id) return;
    const current = payload.current || {};
    const chips = [
      `<span class="live-chip"><b>${current.temperature_2m != null ? current.temperature_2m.toFixed(1) : "–"}°C</b></span>`,
      `<span class="live-chip">${esc(WEATHER_CODES[current.weather_code] || "—")}</span>`,
      `<span class="live-chip">precip ${current.precipitation != null ? current.precipitation : "–"} mm</span>`,
      `<span class="live-chip">wind ${current.wind_speed_10m != null ? Math.round(current.wind_speed_10m) : "–"} km/h</span>`,
    ];
    strip.innerHTML =
      `<span class="live-tag">Right now</span>${chips.join("")}` +
      `<span class="live-caveat">live forecast estimate · does not affect the anomaly flags above</span>`;
  } catch {
    strip.innerHTML = `<span class="live-tag">Now</span><span style="color:var(--mist)">live conditions unavailable</span>`;
  }
}

/* ---------- KPI cards ---------- */

function renderKpis(city) {
  const k = city.kpis;
  const tone = cityTone(k);
  // each card leads with meaning (the signed gap), coloured by climate
  // DIRECTION of departure, not hazard type. Temperature cards are tone-aware
  // so "fewer cold nights" reads as warming (not "quiet") in a warming city.
  const defs = [
    { label: "Warm days", count: k.warm_days, m: tempMeaning("warm", k.warm_days ?? 0, k.warm_days_expected, tone, NOTABLE_TEMP) },
    { label: "Unusually warm days", count: k.hot_extreme_days, m: kpiMeaning(k.hot_extreme_days ?? 0, k.hot_extreme_days_expected, "heat", 6) },
    { label: "Cold nights", count: k.cold_nights, m: tempMeaning("cold", k.cold_nights ?? 0, k.cold_nights_expected, tone, NOTABLE_TEMP) },
    { label: "Heavy-rain days", count: k.heavy_precip_days, m: kpiMeaning(k.heavy_precip_days ?? 0, k.heavy_precip_days_expected, "precip", NOTABLE_PRECIP) },
  ];
  const cards = defs.map(({ label, count, m }) => {
    const tag = m.tag ? `<span class="kpi-tag ${m.cls}">${m.tag}</span>` : "";
    return `<div class="kpi ${m.cls}">
      <div class="label">${esc(label)} · last year</div>
      <p class="value">${count != null ? count : "–"}</p>
      <div class="expected">${esc(m.gap || "")} ${tag}</div>
    </div>`;
  });
  if (city.meta.snow_capable) {
    const snow = (k.rare_snow_days || 0) + (k.exceptional_snow_days || 0);
    cards.push(
      `<div class="kpi ${snow > 0 ? "snow" : "calm"}">
        <div class="label">Unusual snow · last year</div>
        <p class="value">${snow}</p>
        <div class="expected">${snow > 0 ? "rare-season or exceptional amount" : "none outside the seasonal norm"}</div>
      </div>`
    );
  }
  setHtml("kpi-cards", cards.join(""));
}

/* ---------- most unusual days (best material, out of the modal) ---------- */

// how rare an event was, 0 = rarest (nothing in the baseline matched it)
function eventRarity(event) {
  const d = event.detail || {};
  if (d.tmax) return d.tmax.n_at_or_above / d.tmax.n_baseline;
  if (d.tmin) return d.tmin.n_at_or_below / d.tmin.n_baseline;
  if (d.precip) return d.precip.n_at_or_above / d.precip.n_baseline;
  if (d.snow_occurrence) return d.snow_occurrence.baseline_snow_days / d.snow_occurrence.baseline_days;
  if (d.snow_amount) return d.snow_amount.n_at_or_above / d.snow_amount.n_baseline;
  return 1;
}

function renderTopEvents(city) {
  const node = byId("top-events");
  if (!node) return;
  // one card per phenomenon (the rarest of each category), so a snow city
  // surfaces its snow, not three near-identical heat days; then the rarest
  // few overall — with a tie broken toward recency
  const byCategory = new Map();
  for (const event of city.events) {
    const prev = byCategory.get(event.category);
    if (!prev || eventRarity(event) < eventRarity(prev)) byCategory.set(event.category, event);
  }
  const top = [...byCategory.values()]
    .sort((a, b) => eventRarity(a) - eventRarity(b) || b.date.localeCompare(a.date))
    .slice(0, 4);
  if (!top.length) {
    node.innerHTML = `<div class="status-note">No unusual days flagged in the last three years.</div>`;
    return;
  }
  node.innerHTML = top
    .map((event, idx) => {
      const meta = FLAG_META[event.category] || { label: event.category, cls: "" };
      const validation = VALIDATION_META[event.validation?.status] || VALIDATION_META.not_checked;
      return `<button class="event-card ${meta.cls}" data-top="${idx}">
        <div class="event-card-top">
          <span class="badge ${meta.cls}">${meta.label}</span>
          <span class="badge ${validation.cls}">${validation.label}</span>
        </div>
        <p class="event-headline">${esc(evidenceStatement(event))}.</p>
        <div class="event-meta">${fmtDate(event.date)} · ${event.value != null ? event.value : "–"} ${esc(event.unit)}</div>
        ${observedLine(event)}
      </button>`;
    })
    .join("");
  node.querySelectorAll("button[data-top]").forEach((btn) =>
    btn.addEventListener("click", () => openModal(top[Number(btn.dataset.top)], city))
  );
}

// observed cross-check line for an event card / modal (the "actuals" layer)
function observedLine(event) {
  const o = event.observed;
  if (!o) return ""; // snow, or category with no station variable
  if (o.status === "no_obs") {
    return `<div class="obs obs-none">Nearest station ${o.station_km} km · no reading that day</div>`;
  }
  if (o.var === "prcp") {
    const word = o.status === "agree" ? "also recorded rain" : "recorded little/no rain";
    return `<div class="obs obs-${o.status}">Station ${o.station_km} km (${esc(o.station)}) ${word} (${o.obs} mm)</div>`;
  }
  const word = o.status === "agree" ? "agrees" : "differs";
  return `<div class="obs obs-${o.status}">Station ${o.station_km} km: observed ${o.obs}°C vs ERA5 ${o.era5}°C — ${word}</div>`;
}

function observedSentence(event) {
  const o = event.observed;
  if (!o || !o.var) return "";
  if (o.var === "prcp") {
    return `The station ${o.station_km} km away (${esc(o.station)}) recorded ${o.obs} mm that day` +
      (o.status === "agree" ? " — it did rain." : " — little or no rain, so treat this ERA5 flag with caution.");
  }
  return `The station ${o.station_km} km away (${esc(o.station)}) observed ${o.obs}°C; ERA5 estimated ${o.era5}°C` +
    (o.status === "agree" ? ` — within ${Math.abs(o.delta)}°C, so the estimate holds.` : ` — a ${Math.abs(o.delta)}°C gap, so treat this flag with caution.`);
}

/* ---------- trend chart ---------- */

function renderTrendChart(city) {
  const wrap = byId("trend-chart");
  if (!wrap) return;
  wrap.innerHTML = "";

  const years = city.annual.filter(
    (a) => a.coverage_days >= 350 && a.warm_day_fraction != null
  );
  if (years.length < 5) {
    wrap.innerHTML = `<div class="status-note">Not enough annual data.</div>`;
    return;
  }

  const W = 880, H = 270;
  const M = { top: 16, right: 118, bottom: 30, left: 46 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  svg.setAttribute("aria-label", "Annual share of days outside the seasonal normal");
  wrap.appendChild(svg);

  const x0 = years[0].year, x1 = years[years.length - 1].year;
  const maxY = Math.max(
    0.25,
    ...years.map((a) => Math.max(a.warm_day_fraction, a.cold_night_fraction || 0))
  ) * 1.08;
  const X = (year) => M.left + ((year - x0) / (x1 - x0)) * (W - M.left - M.right);
  const Y = (frac) => H - M.bottom - (frac / maxY) * (H - M.top - M.bottom);

  // baseline-era shading
  el("rect", {
    x: X(1991), y: M.top, width: X(2020) - X(1991), height: H - M.top - M.bottom,
    fill: "#eef3f2", opacity: 0.75,
  }, svg);
  el("text", {
    x: X(2005), y: M.top + 12, "text-anchor": "middle", class: "axis-label",
  }, svg).textContent = "baseline 1991–2020";

  // y grid + labels
  for (let f = 0; f <= maxY; f += 0.1) {
    const y = Y(f);
    el("line", { x1: M.left, x2: W - M.right, y1: y, y2: y, class: "grid-line" }, svg);
    el("text", { x: M.left - 8, y: y + 4, "text-anchor": "end", class: "axis-label" }, svg)
      .textContent = `${Math.round(f * 100)}%`;
  }
  // x labels each decade
  for (let year = Math.ceil(x0 / 10) * 10; year <= x1; year += 10) {
    el("text", { x: X(year), y: H - 8, "text-anchor": "middle", class: "axis-label" }, svg)
      .textContent = year;
  }

  // null-expectation reference
  el("line", {
    x1: M.left, x2: W - M.right, y1: Y(0.1), y2: Y(0.1),
    stroke: COLORS.ochre, "stroke-width": 1.6, "stroke-dasharray": "6,4",
  }, svg);
  el("text", {
    x: W - M.right + 6, y: Y(0.1) + 4, class: "axis-label", fill: COLORS.ochre,
  }, svg).textContent = "expected ≈10%";

  const smooth = (key) =>
    years.map((row, i) => {
      const near = years.filter(
        (b) => Math.abs(b.year - row.year) <= 4 && b[key] != null
      );
      return near.length >= 5 ? near.reduce((s, b) => s + b[key], 0) / near.length : null;
    });

  const series = [
    { key: "warm_day_fraction", color: COLORS.heat, label: "Warm days" },
    { key: "cold_night_fraction", color: COLORS.cold, label: "Cold nights" },
  ];
  for (const s of series) {
    // annual values recede; the 9-year mean carries the story
    el("path", {
      d: linePath(years.map((a) => (a[s.key] == null ? null : [X(a.year), Y(a[s.key])]))),
      fill: "none", stroke: s.color, "stroke-width": 1, opacity: 0.28,
      "stroke-linejoin": "round",
    }, svg);
    const smoothed = smooth(s.key);
    const pts = years.map((a, i) => (smoothed[i] == null ? null : [X(a.year), Y(smoothed[i])]));
    el("path", {
      d: linePath(pts), fill: "none", stroke: s.color, "stroke-width": 2.6,
      "stroke-linejoin": "round", "stroke-linecap": "round",
    }, svg);
    const last = pts.filter(Boolean).at(-1);
    if (last) {
      el("circle", { cx: last[0], cy: last[1], r: 3, fill: s.color }, svg);
      const label = el("text", {
        x: W - M.right + 6, y: last[1] + 4, "font-size": 12, "font-weight": 700, fill: s.color,
      }, svg);
      label.textContent = s.label;
    }
  }
  // avoid label overlap: nudge if close
  const labels = [...svg.querySelectorAll("text")].filter((t) =>
    ["Warm days", "Cold nights"].includes(t.textContent)
  );
  if (labels.length === 2) {
    const y0v = parseFloat(labels[0].getAttribute("y"));
    const y1v = parseFloat(labels[1].getAttribute("y"));
    if (Math.abs(y0v - y1v) < 14) labels[1].setAttribute("y", y0v + (y1v >= y0v ? 14 : -14));
  }

  attachHover(wrap, svg, W, H, M, (fx) => {
    const year = Math.round(x0 + ((fx - M.left) / (W - M.left - M.right)) * (x1 - x0));
    const row = years.find((a) => a.year === year);
    if (!row) return null;
    const px = X(row.year);
    return {
      px,
      py: Y(row.warm_day_fraction),
      html:
        `<div class="tip-date">${row.year}${row.year >= 1991 && row.year <= 2020 ? " · in baseline (judged vs other 29 years)" : ""}</div>` +
        `<div><span style="color:#e8a08d">●</span> warm days ${(row.warm_day_fraction * 100).toFixed(1)}%</div>` +
        `<div><span style="color:#9db9dd">●</span> cold nights ${((row.cold_night_fraction || 0) * 100).toFixed(1)}%</div>` +
        `<div class="tip-muted">mean tmax anomaly ${row.mean_tmax_anom > 0 ? "+" : ""}${row.mean_tmax_anom}°C</div>`,
    };
  });

  setHtml(
    "trend-legend",
    `<span class="key"><span class="swatch" style="background:${COLORS.heat}"></span>Warm days, 9-yr mean (annual faint)</span>
     <span class="key"><span class="swatch" style="background:${COLORS.cold}"></span>Cold nights, 9-yr mean (annual faint)</span>
     <span class="key"><span class="swatch" style="background:${COLORS.ochre}"></span>Expected under baseline climate</span>`
  );
}

/* ---------- ribbon chart ---------- */

function doyOf(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  const start = Date.UTC(d.getUTCFullYear(), 0, 0);
  return Math.round((d.getTime() - start) / 86400000);
}

function renderRibbonChart(city) {
  const wrap = byId("ribbon-chart");
  if (!wrap) return;
  wrap.innerHTML = "";
  const days = city.last_365.filter((d) => d.tx != null);
  if (days.length < 30) {
    wrap.innerHTML = `<div class="status-note">Not enough recent data.</div>`;
    return;
  }
  const ribbon = city.ribbon;

  const W = 880, H = 280;
  const M = { top: 14, right: 16, bottom: 30, left: 46 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  svg.setAttribute("aria-label", "Last 365 days of maximum temperature against the seasonal envelope");
  wrap.appendChild(svg);

  const enriched = days.map((d, i) => {
    const doy = Math.min(366, doyOf(d.d));
    return {
      ...d, i,
      p05: ribbon.tmax_p05[doy - 1],
      p50: ribbon.tmax_p50[doy - 1],
      p95: ribbon.tmax_p95[doy - 1],
    };
  });

  const values = enriched.flatMap((d) => [d.tx, d.p05, d.p95]).filter((v) => v != null);
  const yMin = Math.floor(Math.min(...values)) - 2;
  const yMax = Math.ceil(Math.max(...values)) + 2;
  const X = (i) => M.left + (i / (enriched.length - 1)) * (W - M.left - M.right);
  const Y = (v) => H - M.bottom - ((v - yMin) / (yMax - yMin)) * (H - M.top - M.bottom);

  // y grid
  const step = yMax - yMin > 30 ? 10 : 5;
  for (let v = Math.ceil(yMin / step) * step; v <= yMax; v += step) {
    el("line", { x1: M.left, x2: W - M.right, y1: Y(v), y2: Y(v), class: "grid-line" }, svg);
    el("text", { x: M.left - 8, y: Y(v) + 4, "text-anchor": "end", class: "axis-label" }, svg)
      .textContent = `${v}°`;
  }
  // month ticks
  let lastMonth = null;
  enriched.forEach((d, i) => {
    const month = d.d.slice(0, 7);
    if (month !== lastMonth) {
      lastMonth = month;
      if (i > 3 && i < enriched.length - 6) {
        el("text", { x: X(i), y: H - 8, class: "axis-label" }, svg).textContent = new Date(
          `${d.d}T00:00:00`
        ).toLocaleDateString("en-IN", { month: "short" });
      }
    }
  });

  // envelope band p05–p95
  const upper = enriched.map((d) => (d.p95 != null ? [X(d.i), Y(d.p95)] : null));
  const lower = enriched
    .map((d) => (d.p05 != null ? [X(d.i), Y(d.p05)] : null))
    .reverse();
  const bandPath = `${linePath(upper)}L${lower
    .filter(Boolean)
    .map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`)
    .join("L")}Z`;
  el("path", { d: bandPath, fill: COLORS.band, opacity: 0.85 }, svg);

  // median
  el("path", {
    d: linePath(enriched.map((d) => (d.p50 != null ? [X(d.i), Y(d.p50)] : null))),
    fill: "none", stroke: COLORS.mist, "stroke-width": 1.4, "stroke-dasharray": "1,3",
  }, svg);

  // observed trace
  el("path", {
    d: linePath(enriched.map((d) => [X(d.i), Y(d.tx)])),
    fill: "none", stroke: COLORS.charcoal, "stroke-width": 1.8,
    "stroke-linejoin": "round",
  }, svg);

  // flagged dots (temperature + snow flags carry to this chart)
  for (const d of enriched) {
    if (!d.f) continue;
    const flag = bestFlag(d.f);
    const meta = FLAG_META[flag];
    if (!meta) continue;
    const major = ["hot_extreme", "cold_extreme", "rare_snow", "exceptional_snow"].includes(flag);
    el("circle", {
      cx: X(d.i), cy: Y(d.tx), r: major ? 4 : 2.6,
      fill: meta.color, stroke: "#fff", "stroke-width": 1.2,
      opacity: major ? 1 : 0.75,
    }, svg);
  }

  attachHover(wrap, svg, W, H, M, (fx) => {
    const i = Math.round(((fx - M.left) / (W - M.left - M.right)) * (enriched.length - 1));
    const d = enriched[Math.max(0, Math.min(enriched.length - 1, i))];
    if (!d) return null;
    const flagsHtml = d.f
      ? `<div>${d.f.map((f) => FLAG_META[f]?.label || f).join(" · ")}</div>`
      : "";
    return {
      px: X(d.i),
      py: Y(d.tx),
      html:
        `<div class="tip-date">${fmtDate(d.d)}</div>` +
        `<div>max ${d.tx}°C <span class="tip-muted">(normal ${d.p05}–${d.p95}°C)</span></div>` +
        `<div class="tip-muted">min ${d.tn}°C · ${d.pr ?? 0} mm${d.sn ? ` · snow ${d.sn} cm` : ""}</div>` +
        flagsHtml,
    };
  });

  setHtml(
    "ribbon-legend",
    `<span class="key"><span class="swatch" style="background:${COLORS.band};height:10px"></span>Seasonal p05–p95 (1991–2020)</span>
     <span class="key"><span class="swatch" style="background:${COLORS.charcoal}"></span>Daily max temperature</span>
     <span class="key"><span class="swatch dot" style="background:${COLORS.heat}"></span>Heat flag</span>
     <span class="key"><span class="swatch dot" style="background:${COLORS.cold}"></span>Cold flag</span>
     <span class="key"><span class="swatch dot" style="background:${COLORS.snow}"></span>Snow flag</span>`
  );
}

/* ---------- precipitation chart ---------- */

function renderPrecipChart(city) {
  const wrap = byId("precip-chart");
  if (!wrap) return;
  wrap.innerHTML = "";
  const days = city.last_365;
  const ribbon = city.ribbon;
  if (!days.length) {
    wrap.innerHTML = `<div class="status-note">No recent data.</div>`;
    return;
  }

  const W = 880, H = 200;
  const M = { top: 12, right: 16, bottom: 28, left: 46 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  svg.setAttribute("aria-label", "Daily precipitation over the last 365 days");
  wrap.appendChild(svg);

  const enriched = days.map((d, i) => {
    const doy = Math.min(366, doyOf(d.d));
    return { ...d, i, wet95: ribbon.wet_p95[doy - 1] };
  });
  const maxV = Math.max(10, ...enriched.map((d) => Math.max(d.pr || 0, d.wet95 || 0))) * 1.06;
  const X = (i) => M.left + (i / (enriched.length - 1)) * (W - M.left - M.right);
  const Y = (v) => H - M.bottom - (v / maxV) * (H - M.top - M.bottom);
  const barW = Math.max(1, (W - M.left - M.right) / enriched.length - 0.6);

  for (let v = 0; v <= maxV; v += maxV > 120 ? 50 : 25) {
    el("line", { x1: M.left, x2: W - M.right, y1: Y(v), y2: Y(v), class: "grid-line" }, svg);
    el("text", { x: M.left - 8, y: Y(v) + 4, "text-anchor": "end", class: "axis-label" }, svg)
      .textContent = `${Math.round(v)}`;
  }
  el("text", { x: M.left - 8, y: M.top + 2, "text-anchor": "end", class: "axis-label" }, svg)
    .textContent = "mm";

  let lastMonth = null;
  enriched.forEach((d, i) => {
    const month = d.d.slice(0, 7);
    if (month !== lastMonth) {
      lastMonth = month;
      if (i > 3 && i < enriched.length - 6) {
        el("text", { x: X(i), y: H - 8, class: "axis-label" }, svg).textContent = new Date(
          `${d.d}T00:00:00`
        ).toLocaleDateString("en-IN", { month: "short" });
      }
    }
  });

  // wet-day p95 reference (broken where undefined)
  el("path", {
    d: linePath(enriched.map((d) => (d.wet95 != null ? [X(d.i), Y(d.wet95)] : null))),
    fill: "none", stroke: COLORS.ochre, "stroke-width": 1.4, "stroke-dasharray": "5,4",
    opacity: 0.9,
  }, svg);

  for (const d of enriched) {
    const v = d.pr || 0;
    if (v <= 0) continue;
    const flagged = d.f && d.f.includes("heavy_precip");
    el("rect", {
      x: X(d.i) - barW / 2, y: Y(v), width: barW, height: H - M.bottom - Y(v),
      rx: barW > 2 ? 1 : 0,
      fill: COLORS.precip, opacity: flagged ? 1 : 0.45,
    }, svg);
    if (d.sn && d.sn >= 1 && d.f && (d.f.includes("rare_snow") || d.f.includes("exceptional_snow"))) {
      el("circle", { cx: X(d.i), cy: Y(v) - 6, r: 3.4, fill: COLORS.snow, stroke: "#fff", "stroke-width": 1 }, svg);
    }
  }

  attachHover(wrap, svg, W, H, M, (fx) => {
    const i = Math.round(((fx - M.left) / (W - M.left - M.right)) * (enriched.length - 1));
    const d = enriched[Math.max(0, Math.min(enriched.length - 1, i))];
    if (!d) return null;
    return {
      px: X(d.i),
      py: d.pr ? Y(d.pr) : null,
      html:
        `<div class="tip-date">${fmtDate(d.d)}</div>` +
        `<div>${d.pr ?? 0} mm${d.sn ? ` · snow ${d.sn} cm` : ""}</div>` +
        `<div class="tip-muted">seasonal wet-day p95: ${d.wet95 != null ? `${d.wet95} mm` : "undefined (too few baseline wet days)"}</div>` +
        (d.f && d.f.includes("heavy_precip") ? `<div>Heavy precipitation flag</div>` : ""),
    };
  });

  setHtml(
    "precip-legend",
    `<span class="key"><span class="swatch" style="background:${COLORS.precip}"></span>Daily precipitation</span>
     <span class="key"><span class="swatch" style="background:${COLORS.ochre}"></span>Seasonal wet-day 95th percentile</span>
     <span class="key"><span class="swatch dot" style="background:${COLORS.snow}"></span>Flagged snow day</span>`
  );
}

/* ---------- events table + drilldown ---------- */

function evidenceStatement(event) {
  // a day can carry several flags (e.g. cold + heavy rain), so the statement
  // must follow the event's own category, not whichever detail exists first
  const d = event.detail || {};
  const only = (k, n, what) =>
    k === 0 ? `No ${what} (of ${n}) reached this value` : `Only ${k} of ${n} ${what} reached this value`;
  const statements = {
    heat: () => d.tmax && only(d.tmax.n_at_or_above, d.tmax.n_baseline, "comparable baseline days"),
    cold: () =>
      d.tmin &&
      (d.tmin.n_at_or_below === 0
        ? `No comparable baseline day (of ${d.tmin.n_baseline}) was this cold`
        : `Only ${d.tmin.n_at_or_below} of ${d.tmin.n_baseline} comparable baseline days were this cold`),
    precip: () => d.precip && only(d.precip.n_at_or_above, d.precip.n_baseline, "comparable baseline wet days"),
    snowOcc: () =>
      d.snow_occurrence &&
      `Only ${d.snow_occurrence.baseline_snow_days} of ${d.snow_occurrence.baseline_days} comparable baseline days had snow at all`,
    snowAmt: () => d.snow_amount && only(d.snow_amount.n_at_or_above, d.snow_amount.n_baseline, "baseline snow days"),
  };
  const order = {
    hot_extreme: ["heat"], warm_day: ["heat"],
    cold_extreme: ["cold"], cold_night: ["cold"],
    heavy_precip: ["precip"],
    rare_snow: ["snowOcc", "snowAmt"],
    exceptional_snow: ["snowAmt", "snowOcc"],
  }[event.category] || ["heat", "cold", "precip", "snowOcc", "snowAmt"];
  for (const key of order) {
    const s = statements[key]();
    if (s) return s;
  }
  return "";
}

function renderEvents(city) {
  const body = byId("events-body");
  if (!body) return;
  const events = [...city.events].sort((a, b) => b.date.localeCompare(a.date));
  if (!events.length) {
    body.innerHTML = `<tr><td colspan="5"><div class="status-note">No flagged events in the last three years.</div></td></tr>`;
    return;
  }
  body.innerHTML = events
    .map((event, idx) => {
      const meta = FLAG_META[event.category] || { label: event.category, cls: "" };
      const validation = VALIDATION_META[event.validation?.status] || VALIDATION_META.not_checked;
      return `<tr data-idx="${idx}">
        <td>${fmtDate(event.date)}</td>
        <td><span class="badge ${meta.cls}">${meta.label}</span></td>
        <td><b>${event.value != null ? event.value : "–"}</b> ${esc(event.unit)}</td>
        <td class="hide-sm evidence">${esc(evidenceStatement(event))}</td>
        <td><span class="badge ${validation.cls}">${validation.label}</span></td>
      </tr>`;
    })
    .join("");
  body.querySelectorAll("tr[data-idx]").forEach((row) =>
    row.addEventListener("click", () => openModal(events[Number(row.dataset.idx)], city))
  );
}

function openModal(event, city) {
  const backdrop = byId("modal-backdrop");
  const modal = byId("modal");
  if (!backdrop || !modal) return;
  const meta = FLAG_META[event.category] || { label: event.category, cls: "" };
  const validation = VALIDATION_META[event.validation?.status] || VALIDATION_META.not_checked;
  const cityName = city.meta.name;

  const facts = [];
  if (event.tmax_pct != null && event.category === "hot_extreme")
    facts.push(["Seasonal percentile (tmax)", pctLabel(event.tmax_pct)]);
  if (event.tmin_pct != null && event.category === "cold_extreme")
    facts.push(["Seasonal percentile (tmin)", pctLabel(event.tmin_pct)]);
  if (event.precip_wet_pct != null)
    facts.push(["Wet-day percentile", pctLabel(event.precip_wet_pct)]);
  if (event.snow_occ_prob != null)
    facts.push(["Baseline snow probability", `${(event.snow_occ_prob * 100).toFixed(1)}%`]);
  if (event.snow_amount_pct != null)
    facts.push(["Snow-amount percentile", pctLabel(event.snow_amount_pct)]);
  facts.push(["Comparison window", `±2 days of season · 1991–2020`]);
  facts.push(["Source tier", "Reanalysis estimate (ERA5)"]);

  const newsQuery = encodeURIComponent(`"${cityName}" weather ${event.date}`);
  const worldview = `https://worldview.earthdata.nasa.gov/?v=${city.meta.longitude - 1.6},${city.meta.latitude - 1.2},${city.meta.longitude + 1.6},${city.meta.latitude + 1.2}&t=${event.date}&l=MODIS_Terra_CorrectedReflectance_TrueColor,MODIS_Terra_NDSI_Snow_Cover`;

  const suggested = `ERA5 estimates ${meta.label.toLowerCase()} near ${cityName} on ${fmtDate(event.date)} (${event.value} ${event.unit}); ${evidenceStatement(event).toLowerCase()} (same-season 1991–2020 baseline). Reanalysis estimate — verify against station or satellite evidence.`;

  modal.innerHTML = `
    <button class="close-btn" id="modal-close">Close ✕</button>
    <span class="badge ${meta.cls}">${meta.label}</span>
    <h3>${esc(cityName)}, ${fmtDate(event.date)}</h3>
    <div class="when">${event.value != null ? `${event.value} ${esc(event.unit)}` : ""} · validation: <span class="badge ${validation.cls}">${validation.label}</span></div>
    <div class="statement"><b>${esc(evidenceStatement(event))}</b> — same 5-day seasonal window across 1991–2020${event.date >= "1991" && event.date <= "2021" ? ", excluding the event's own year" : ""}.</div>
    ${event.observed && event.observed.var ? `<div class="statement obs-statement obs-${event.observed.status}"><b>Nearest weather station${event.observed.status === "agree" ? " agrees" : event.observed.status === "disagree" ? " differs" : ""}.</b> ${observedSentence(event)}</div>` : ""}
    ${event.validation?.note ? `<div class="statement">${esc(event.validation.note)}${event.validation.evidence_url ? ` · <a href="${esc(event.validation.evidence_url)}" target="_blank" rel="noopener">evidence</a>` : ""}</div>` : ""}
    <div class="grid2">
      ${facts.map(([label, value]) => `<div class="fact"><div class="label">${esc(label)}</div><div class="val">${esc(value)}</div></div>`).join("")}
    </div>
    <div class="caveat">This is a screening flag from reanalysis, not an observation. It says nothing about cause, and it describes one ~9–31 km grid cell, not a district. Before citing publicly, check at least one independent evidence tier below.</div>
    <p><b>Verify against:</b></p>
    <ul class="links">
      <li><a href="https://news.google.com/search?q=${newsQuery}" target="_blank" rel="noopener">News coverage around this date</a></li>
      <li><a href="https://mausam.imd.gov.in/" target="_blank" rel="noopener">IMD observations and bulletins</a></li>
      ${event.category.includes("snow") ? `<li><a href="${worldview}" target="_blank" rel="noopener">MODIS snow cover on NASA Worldview (this date, this area)</a></li>` : ""}
    </ul>
    <p><b>Suggested careful phrasing:</b></p>
    <div class="statement" style="font-size:13px">${esc(suggested)}</div>
  `;
  backdrop.classList.add("open");
  const closeBtn = byId("modal-close");
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
}

function closeModal() {
  const backdrop = byId("modal-backdrop");
  if (backdrop) backdrop.classList.remove("open");
}

/* ---------- go ---------- */

document.addEventListener("DOMContentLoaded", boot);
