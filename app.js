const DATA_URL = "./public/data/anomaly_summary.json";

const SIGNALS = {
  extreme_heat: { label: "Extreme heat", color: "#b94a34" },
  extreme_cold: { label: "Extreme cold", color: "#356b9a" },
  heavy_precipitation: { label: "Heavy precipitation", color: "#6c5d9e" },
  unseasonable_snow: { label: "Unseasonable snow", color: "#2f7d5b" },
  within_seasonal_range: { label: "Seasonal range", color: "#687067" },
};

const MAP_BOUNDS = {
  minLat: 30.1,
  maxLat: 34.8,
  minLon: 73.6,
  maxLon: 80.1,
};

const LABEL_OFFSETS = {
  gulmarg: { x: 14, y: -18 },
  sonamarg: { x: 18, y: 0 },
  srinagar: { x: 14, y: 18 },
  leh: { x: 16, y: 0 },
  manali: { x: 14, y: 0 },
  shimla: { x: 14, y: 0 },
  joshimath: { x: -108, y: -18 },
};

let dashboardData = null;
let selectedId = null;

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function pct(value) {
  return `${Math.round(value * 100)}%`;
}

function fmt(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(digits);
}

function prettyDate(dateText) {
  return new Intl.DateTimeFormat("en", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(`${dateText}T00:00:00+05:30`));
}

function signalMeta(signal) {
  return SIGNALS[signal] || SIGNALS.within_seasonal_range;
}

function scoreColor(score) {
  if (score >= 0.85) return "#b94a34";
  if (score >= 0.65) return "#c47a22";
  if (score >= 0.45) return "#6c5d9e";
  return "#2f7d5b";
}

function locationIntensity(location) {
  return clamp((location.kpis.recent_anomaly_days_365 || 0) / 120, 0, 1);
}

async function loadData() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    dashboardData = await response.json();
    selectedId = dashboardData.locations[0]?.id;
    render();
  } catch (error) {
    document.querySelector(".workspace").innerHTML = `
      <section class="tool-panel empty-state">
        <div>
          <strong>Dataset not found</strong>
          <p>Run <code>python3 scripts/build_dataset.py</code> and serve the repository root.</p>
          <p>${error.message}</p>
        </div>
      </section>
    `;
  }
}

function render() {
  const generated = new Date(dashboardData.generated_at_utc);
  document.getElementById("generatedAt").textContent =
    `Generated ${generated.toLocaleDateString("en", { month: "short", day: "numeric", year: "numeric" })}`;
  document.getElementById("locationCount").textContent = dashboardData.locations.length;

  renderLocationList();
  renderMap();
  renderSelected();
}

function selectedLocation() {
  return dashboardData.locations.find((location) => location.id === selectedId) || dashboardData.locations[0];
}

function renderLocationList() {
  const container = document.getElementById("locationList");
  container.innerHTML = dashboardData.locations
    .map((location) => {
      const score = locationIntensity(location);
      return `
        <button class="location-button ${location.id === selectedId ? "active" : ""}" data-location="${location.id}">
          <i class="severity-dot" style="background:${scoreColor(score)}; box-shadow:0 0 0 4px ${scoreColor(score)}22"></i>
          <span>
            <strong>${location.name}</strong>
            <span>${location.region}</span>
          </span>
          <span class="location-score">${location.kpis.recent_anomaly_days_365}</span>
        </button>
      `;
    })
    .join("");

  container.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedId = button.dataset.location;
      render();
    });
  });
}

function renderMap() {
  const container = document.getElementById("regionMap");
  container.innerHTML = dashboardData.locations
    .map((location) => {
      const left = clamp(
        ((location.longitude - MAP_BOUNDS.minLon) / (MAP_BOUNDS.maxLon - MAP_BOUNDS.minLon)) * 100,
        5,
        95,
      );
      const top = clamp(
        ((MAP_BOUNDS.maxLat - location.latitude) / (MAP_BOUNDS.maxLat - MAP_BOUNDS.minLat)) * 100,
        8,
        92,
      );
      const score = locationIntensity(location);
      const color = scoreColor(score);
      const offset = LABEL_OFFSETS[location.id] || { x: 14, y: 0 };
      return `
        <button
          class="map-point ${location.id === selectedId ? "active" : ""}"
          data-location="${location.id}"
          aria-label="${location.name}"
          style="left:${left}%; top:${top}%; background:${color}"
        ></button>
        <span class="map-label" style="left:${left}%; top:${top}%; transform:translate(${offset.x}px, ${offset.y}px)">${location.name}</span>
      `;
    })
    .join("");

  container.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      selectedId = button.dataset.location;
      render();
    });
  });
}

function renderSelected() {
  const location = selectedLocation();
  document.getElementById("selectedRegion").textContent = `${location.region} / ${location.state}`;
  document.getElementById("selectedName").textContent = location.name;
  document.getElementById("terrainNote").textContent = location.terrain_note;

  renderKpis(location);
  renderDailyChart(location);
  renderMonthlyChart(location);
  renderEvents(location);
  renderAnnualChart(location);
}

function renderKpis(location) {
  const kpis = [
    ["Anomaly days", location.kpis.recent_anomaly_days_365, "last 365 available days"],
    ["Heavy rain days", location.kpis.recent_heavy_precip_days_365, "95th percentile threshold"],
    ["Heat days", location.kpis.recent_heat_days_365, "daily max above seasonal p95"],
    ["Cold days", location.kpis.recent_cold_days_365, "daily min below seasonal p05"],
    ["Snow flags", location.kpis.recent_unseasonable_snow_days_365, "rare snowfall for date window"],
    ["Max score", Math.round(location.kpis.max_recent_score_365 * 100), "screening rank, not attribution"],
  ];
  document.getElementById("kpiGrid").innerHTML = kpis
    .map(
      ([label, value, note]) => `
        <div class="kpi">
          <span>${label}</span>
          <strong>${value}</strong>
          <em>${note}</em>
        </div>
      `,
    )
    .join("");
}

function svgFrame(width = 720, height = 310) {
  return { width, height, pad: { top: 18, right: 18, bottom: 36, left: 44 } };
}

function renderDailyChart(location) {
  const records = location.last_45_days || [];
  if (!records.length) {
    document.getElementById("dailyChart").innerHTML = `<div class="empty-state">No daily records</div>`;
    return;
  }
  const { width, height, pad } = svgFrame();
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const anomalies = records.map((d) => Number(d.tmax_anomaly_c || 0));
  const precip = records.map((d) => Number(d.precip_mm || 0));
  const maxAbs = Math.max(4, ...anomalies.map(Math.abs));
  const maxPrecip = Math.max(5, ...precip);
  const zeroY = pad.top + innerH / 2;
  const step = innerW / records.length;

  const bars = records
    .map((record, index) => {
      const anomaly = Number(record.tmax_anomaly_c || 0);
      const x = pad.left + index * step + step * 0.15;
      const barW = Math.max(3, step * 0.7);
      const y = anomaly >= 0 ? zeroY - (Math.abs(anomaly) / maxAbs) * (innerH / 2) : zeroY;
      const h = Math.max(1, (Math.abs(anomaly) / maxAbs) * (innerH / 2));
      const color = anomaly >= 0 ? "#b94a34" : "#356b9a";
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="2" fill="${color}" opacity="0.86" />`;
    })
    .join("");

  const rainLine = records
    .map((record, index) => {
      const x = pad.left + index * step + step / 2;
      const y = pad.top + innerH - (Number(record.precip_mm || 0) / maxPrecip) * innerH;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  const labels = [
    `<text x="${pad.left}" y="${height - 12}" fill="#687067" font-size="11">${prettyDate(records[0].date)}</text>`,
    `<text x="${width - pad.right}" y="${height - 12}" text-anchor="end" fill="#687067" font-size="11">${prettyDate(records[records.length - 1].date)}</text>`,
    `<text x="${pad.left}" y="${pad.top + 10}" fill="#687067" font-size="11">+${maxAbs.toFixed(0)}C</text>`,
    `<text x="${pad.left}" y="${height - pad.bottom}" fill="#687067" font-size="11">-${maxAbs.toFixed(0)}C</text>`,
  ].join("");

  document.getElementById("dailyChart").innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Daily temperature anomaly chart">
      <rect x="0" y="0" width="${width}" height="${height}" rx="8" fill="#fbfcfa" />
      <line x1="${pad.left}" x2="${width - pad.right}" y1="${zeroY}" y2="${zeroY}" stroke="#222521" stroke-opacity="0.22" />
      ${bars}
      <path d="${rainLine}" fill="none" stroke="#2f7d5b" stroke-width="3" stroke-linecap="round" opacity="0.86" />
      <circle cx="${width - 118}" cy="24" r="5" fill="#b94a34" />
      <text x="${width - 106}" y="28" fill="#687067" font-size="12">warm anomaly</text>
      <circle cx="${width - 118}" cy="45" r="5" fill="#356b9a" />
      <text x="${width - 106}" y="49" fill="#687067" font-size="12">cool anomaly</text>
      <path d="M${width - 122},66 L${width - 112},66" stroke="#2f7d5b" stroke-width="3" />
      <text x="${width - 106}" y="70" fill="#687067" font-size="12">precipitation</text>
      ${labels}
    </svg>
  `;
}

function renderMonthlyChart(location) {
  const records = location.monthly || [];
  if (!records.length) {
    document.getElementById("monthlyChart").innerHTML = `<div class="empty-state">No monthly records</div>`;
    return;
  }
  const { width, height, pad } = svgFrame();
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const maxPrecip = Math.max(10, ...records.map((d) => d.total_precip_mm || 0));
  const maxScore = Math.max(1, ...records.map((d) => d.max_anomaly_score || 0));
  const step = innerW / records.length;

  const bars = records
    .map((record, index) => {
      const x = pad.left + index * step + step * 0.18;
      const barW = Math.max(4, step * 0.64);
      const h = ((record.total_precip_mm || 0) / maxPrecip) * innerH;
      const y = pad.top + innerH - h;
      const score = record.max_anomaly_score || 0;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" rx="3" fill="${scoreColor(score)}" opacity="${0.38 + (score / maxScore) * 0.48}" />`;
    })
    .join("");

  const line = records
    .map((record, index) => {
      const x = pad.left + index * step + step / 2;
      const normalized = clamp(((record.mean_tmax_anomaly_c || 0) + 6) / 12, 0, 1);
      const y = pad.top + innerH - normalized * innerH;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  document.getElementById("monthlyChart").innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Monthly precipitation and temperature anomaly chart">
      <rect x="0" y="0" width="${width}" height="${height}" rx="8" fill="#fbfcfa" />
      <line x1="${pad.left}" x2="${width - pad.right}" y1="${pad.top + innerH / 2}" y2="${pad.top + innerH / 2}" stroke="#222521" stroke-opacity="0.12" />
      ${bars}
      <path d="${line}" fill="none" stroke="#222521" stroke-width="3" stroke-linecap="round" opacity="0.72" />
      <text x="${pad.left}" y="${height - 12}" fill="#687067" font-size="11">${records[0].month}</text>
      <text x="${width - pad.right}" y="${height - 12}" text-anchor="end" fill="#687067" font-size="11">${records[records.length - 1].month}</text>
      <text x="${pad.left}" y="${pad.top + 10}" fill="#687067" font-size="11">${maxPrecip.toFixed(0)} mm</text>
      <text x="${width - 170}" y="28" fill="#687067" font-size="12">bars: precipitation</text>
      <text x="${width - 170}" y="48" fill="#687067" font-size="12">line: temp anomaly</text>
    </svg>
  `;
}

function renderEvents(location) {
  const rows = (location.top_events || [])
    .map((event) => {
      const meta = signalMeta(event.signal);
      return `
        <tr>
          <td>${prettyDate(event.date)}</td>
          <td><span class="signal-badge" style="background:${meta.color}">${meta.label}</span></td>
          <td><strong>${Math.round(event.anomaly_score * 100)}</strong></td>
          <td>${fmt(event.tmin_c)}-${fmt(event.tmax_c)}C</td>
          <td>${fmt(event.precip_mm)} mm</td>
          <td>${fmt(event.snowfall_cm)} cm</td>
        </tr>
      `;
    })
    .join("");
  document.getElementById("eventRows").innerHTML = rows;
}

function renderAnnualChart(location) {
  const records = location.annual || [];
  if (!records.length) {
    document.getElementById("annualChart").innerHTML = `<div class="empty-state">No annual records</div>`;
    return;
  }
  const { width, height, pad } = svgFrame();
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const maxDays = Math.max(5, ...records.map((d) => d.anomaly_days || 0));
  const step = innerW / records.length;

  const bars = records
    .map((record, index) => {
      const x = pad.left + index * step + step * 0.18;
      const barW = Math.max(10, step * 0.64);
      const h = ((record.anomaly_days || 0) / maxDays) * innerH;
      const y = pad.top + innerH - h;
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" rx="4" fill="#2f7d5b" opacity="0.78" />`;
    })
    .join("");

  const snowMarks = records
    .map((record, index) => {
      if (!record.flagged_unseasonable_snow_days) return "";
      const x = pad.left + index * step + step / 2;
      const y = pad.top + innerH - ((record.anomaly_days || 0) / maxDays) * innerH - 10;
      return `<circle cx="${x.toFixed(1)}" cy="${Math.max(pad.top + 8, y).toFixed(1)}" r="5" fill="#356b9a" />`;
    })
    .join("");

  document.getElementById("annualChart").innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Annual anomaly count chart">
      <rect x="0" y="0" width="${width}" height="${height}" rx="8" fill="#fbfcfa" />
      ${bars}
      ${snowMarks}
      <text x="${pad.left}" y="${height - 12}" fill="#687067" font-size="11">${records[0].year}</text>
      <text x="${width - pad.right}" y="${height - 12}" text-anchor="end" fill="#687067" font-size="11">${records[records.length - 1].year}</text>
      <text x="${pad.left}" y="${pad.top + 10}" fill="#687067" font-size="11">${maxDays} days</text>
      <circle cx="${width - 145}" cy="25" r="5" fill="#356b9a" />
      <text x="${width - 132}" y="29" fill="#687067" font-size="12">rare snow flag</text>
    </svg>
  `;
}

loadData();
