"use strict";
const BREAKS = [0.05, 1, 3, 6, 12]; // inches -> s0..s5
// Everything inserted with innerHTML goes through esc(); links must be https.
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = u => /^https:\/\/[^\s"'<>]+$/i.test(u || "") ? u : null;
const extLink = (u, text) => { const h = safeUrl(u); return h ? `<a href="${esc(h)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : ""; };
// Per-viewer UI preferences only; the page works the same if storage is unavailable.
const store = {
  get(k, d) { try { const v = localStorage.getItem("nes:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("nes:" + k, JSON.stringify(v)); } catch {} },
};
const fmtDate = iso => new Date(iso + "T12:00:00").toLocaleDateString([], { month: "short", day: "numeric" });
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const bin = s => { let i = 0; while (i < BREAKS.length && s >= BREAKS[i]) i++; return i; };
const snowColor = s => css(`--s${bin(s)}`);
const dirName = d => d == null ? "–" : ["N","NE","E","SE","S","SW","W","NW"][Math.round(d / 45) % 8];
const fmtIn = v => v == null ? "–" : (v < 0.05 ? "0" : v.toFixed(1)) + "″";
const dayLabel = iso => new Date(iso).toLocaleString([], { weekday: "short", hour: "numeric" });
// Headline amount: WPC 50th percentile when we have it, else the NWS deterministic total.
const likely = m => m.range?.p50 ?? m.snow_72h_in;
let barMax = 1, ppp = null;
const fmtWin = w => w ? `${new Date(w.start).toLocaleString([], { weekday: "short", hour: "numeric" })} → ${new Date(w.end).toLocaleString([], { weekday: "short", hour: "numeric" })}` : "";
function rangeBar(r) {
  if (!r || r.p10 == null || !(r.p90 >= 0.05)) return "";
  const x = v => `${Math.min(100, (v / barMax) * 100).toFixed(1)}%`;
  const w = (a, b) => `left:${x(a)};width:calc(${x(b)} - ${x(a)})`;
  return `<div class="bar" aria-hidden="true"><span class="track"></span>
    <span class="w" style="${w(r.p10, r.p90)}"></span><span class="b" style="${w(r.p25, r.p75)};min-width:2px"></span>
    <span class="t" style="left:calc(${x(r.p50)} - 1px)"></span></div>`;
}
const rangeText = r => r && r.p10 != null ? (r.p90 >= 0.05 ? `${fmtIn(r.p10)}–${fmtIn(r.p90)}` : "none expected") : "no range";
// Mostly-rain flag: meaningful liquid, little snow, and above-freezing highs.
const mostlyRain = m => m.qpf_72h_in >= 0.15 && m.snow_72h_in < m.qpf_72h_in * 3 && m.temp_max_f > 34;

document.getElementById("scale").innerHTML = [0,1,2,3,4,5].map(i => `<span style="background:var(--s${i})"></span>`).join("");

const map = L.map("map", { zoomControl: true, zoomSnap: 0.5 }).setView([43.7, -72.4], 7);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 14, className: "basemap",
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);

let rows = [], markers = {}, filter = "all";
let showBC = store.get("showBC", true), maxDrive = +store.get("maxDrive", 0) || 0;
const bcOff = new Set(store.get("bcOff", []));
let infoById = {}, seasonAsOf = null, fRank = {};

function seasonChip(info) {
  const s = info?.season;
  if (!s || s.status === "untracked") return "";
  const text = s.status === "opened" ? `Opened ${fmtDate(s.date)}` : s.status === "scheduled" ? `Opens ${fmtDate(s.date)}` : "No opening date yet";
  return `<span class="season ${s.status}">${text}</span>`;
}
function seasonBlock(info) {
  if (!info) return "";
  const chip = seasonChip(info), link = extLink(info.report || info.site, "Snow report ↗");
  return `<div class="sub" style="margin-top:6px">${chip}${chip && link ? " · " : ""}${link}</div>
    <div class="sub">${seasonAsOf ? `Season status as of ${fmtDate(seasonAsOf)}; ` : ""}not live lift status. Base depth and open trails aren't checked.</div>`;
}

function popupHtml(m) {
  const days = (m.by_day || []).map(d => `<tr><td>${dayLabel(d.start)} +24h</td><td>${fmtIn(d.snow_in)} <span class="sub">(${d.qpf_in.toFixed(2)}″ liquid)</span></td></tr>`).join("");
  let bcWarn = "";
  if (m.pass === "Backcountry") {
    const a = bcZoneByKey()[m.bc_zone]?.avalanche;
    bcWarn = `<div class="warn">Not a ski recommendation. ${a?.link
      ? `Check the official avalanche forecast before going: ${extLink(a.link, a.center || "official avalanche forecast")}.`
      : `No avalanche center forecasts this area${a?.reason ? `: ${esc(a.reason)}` : "."}`}</div>`;
  }
  return `<div class="pp">
    <h3>${esc(m.name)}</h3>
    <div class="sub"><span class="pass ${esc(m.pass)}">${esc(m.pass.toUpperCase())}</span> · ${esc(m.state)} · summit ${m.summit_ft.toLocaleString()} ft · grid cell ${m.grid_elevation_ft?.toLocaleString() ?? "–"} ft</div>
    ${m.range ? `<table>
      <tr><td colspan="2"><b>WPC 72h snowfall range</b><br><span class="sub">${fmtWin(ppp.window)} · cycle ${ppp.cycle.slice(8)}Z</span></td></tr>
      <tr><td>Low (10th pct)</td><td>${fmtIn(m.range.p10)}</td></tr>
      <tr><td><b>Likely (50th)</b></td><td><b>${fmtIn(m.range.p50)}</b></td></tr>
      <tr><td>High (90th pct)</td><td>${fmtIn(m.range.p90)}</td></tr>
      <tr><td>Middle half (25th–75th)</td><td>${fmtIn(m.range.p25)}–${fmtIn(m.range.p75)}</td></tr>
    </table>${rangeBar(m.range)}` : `<div class="sub" style="margin-top:6px">No WPC range data for this run.</div>`}
    <table>
      <tr><td colspan="2"><b>NWS point forecast</b><br><span class="sub">next 72h from ${new Date(m.window_start).toLocaleString([], { weekday: "short", hour: "numeric" })}</span></td></tr>
      <tr><td>72h snow</td><td><b>${fmtIn(m.snow_72h_in)}</b></td></tr>
      ${days}
      <tr><td>Liquid (QPF)</td><td>${m.qpf_72h_in.toFixed(2)}″${m.slr ? ` · SLR ${m.slr}:1` : ""}</td></tr>
      <tr><td>Temp range</td><td>${m.temp_min_f}–${m.temp_max_f}°F</td></tr>
      <tr><td>Max wind / gust</td><td>${m.wind_max_mph} / ${m.gust_max_mph ?? "–"} mph ${dirName(m.wind_dir_mode_deg)}</td></tr>
    </table>
    ${mostlyRain(m) ? `<div class="rain">Mostly rain or mixed precipitation</div>` : ""}
    ${bcWarn}${m.pass === "Backcountry" ? "" : seasonBlock(infoById[m.id])}
    <div class="sub" style="margin-top:6px">NWS grid updated ${m.grid_update_time ? new Date(m.grid_update_time).toLocaleString() : "–"}</div>
  </div>`;
}

const GRADES = [[70, "Great", "--g3"], [50, "Good", "--g2"], [30, "Fair", "--g1"], [0, "Poor", "--g0"]];
const grade = sc => GRADES.find(([min]) => sc >= min);
const fmtH = h => h == null ? "–" : `${Math.floor(h)}h${String(Math.round((h % 1) * 60)).padStart(2, "0")}`;

let view = "storm", wk = null, wkById = {}, metaText = { storm: "", weekend: "", backcountry: "" };

function weekendPopup(m, r) {
  const day = d => `<div class="day ${d.label === r.best_day ? "best" : ""}">
      <h4>${d.label} · ${d.score}</h4>
      <div>${d.closed ? `<span class="season scheduled">Not open yet</span>` : esc(d.surface)}</div>
      <div>Fresh ${fmtIn(d.fresh_in)} <span class="sub">(${fmtIn(d.fresh_forecast_in)} fcst + ${fmtIn(d.fresh_observed_in)} obs)</span></div>
      <div>${d.temp_lift_min_f ?? "–"}–${d.temp_lift_max_f ?? "–"}°F · rain ${d.rain_in.toFixed(2)}″</div>
      <div>Gusts ${d.gust_mph ?? "–"} mph · ${d.wind_risk} hold risk</div>
      <div class="sub">conditions ${d.parts.conditions.toFixed(0)} × wind ${d.parts.wind_factor} × drive ${d.parts.drive_factor}</div>
      ${d.complete ? "" : `<div class="sub">forecast doesn't cover all lift hours yet</div>`}
    </div>`;
  const [, g] = grade(r.score);
  return `<div class="pp">
    <h3>${fRank[r.id] ?? r.rank}. ${esc(r.name)} · ${r.score} <span class="sub">${g}${(fRank[r.id] ?? r.rank) !== r.rank ? ` · #${r.rank} overall` : ""}</span></h3>
    <div class="sub"><span class="pass ${esc(r.pass)}">${esc(r.pass.toUpperCase())}</span> · ${esc(r.state)} · ${fmtH(r.drive_h)} drive (${r.drive_mi ?? "–"} mi, free-flow)</div>
    <div class="days">${r.days.map(day).join("")}</div>
    <div class="sub" style="margin-top:8px">Observed last 7 days: ${fmtIn(r.recent_7d_in)}${r.storm_range ? ` · WPC 72h range ${rangeText(r.storm_range)}` : ""}</div>
    ${seasonBlock(r)}
  </div>`;
}

const passColor = p => css({ Ikon: "--ikon", Epic: "--epic", Indy: "--indy" }[p] || "--bc");
// Fit the map to whatever mountains the current view + filter shows.
function homeView() {
  const pts = Object.values(markers).map(mk => mk.getLatLng());
  if (pts.length) map.fitBounds(L.latLngBounds(pts), { padding: [24, 24], maxZoom: 9 });
}

// Markers shrink when zoomed out so dense areas (central VT/NH) stay readable.
const zoomScale = () => { const z = map.getZoom(); return z >= 8 ? 1 : z <= 5.5 ? 0.4 : 0.4 + 0.6 * (z - 5.5) / 2.5; };
const triIcon = (fill, s) => L.divIcon({ className: "", iconSize: [s, s],
  html: `<svg width="${s}" height="${s}" viewBox="0 0 20 20"><polygon points="10,1 19,18 1,18" fill="${fill}" stroke="${css("--bc")}" stroke-width="2"/></svg>` });
function sizeMarker(mk) {
  const k = zoomScale();
  if (mk._tri) { const s = Math.round(Math.max(12, mk._base * k)); mk.setIcon(triIcon(mk._fill, s)); mk._lbl.r = s / 2; }
  else { const r = Math.max(4, mk._base * k); mk.setRadius(r); mk._lbl.r = r; }
}

function marker(m) {
  let mk;
  if (view === "weekend") {
    const r = wkById[m.id], closed = r.days.every(d => d.closed);
    mk = L.circleMarker([m.lat, m.lon], { radius: 1, color: passColor(m.pass), weight: 2.5, fillColor: css(grade(r.score)[2]),
      fillOpacity: closed ? 0.35 : 0.95, dashArray: closed ? "3 3" : null });
    mk._base = 8 + r.score / 8;
    mk._lbl = { text: `${fRank[m.id] ?? r.rank}. ${m.name} ${closed ? "(not open)" : r.score}`, r: 1 };
    mk.bindPopup(weekendPopup(m, r), { maxWidth: 360 });
  } else {
    const c = snowColor(likely(m)), big = 8 + Math.min(likely(m), 18) * 0.8;
    if (m.pass === "Backcountry") {
      mk = L.marker([m.lat, m.lon], { icon: triIcon(c, 12) });
      Object.assign(mk, { _tri: true, _fill: c, _base: Math.min(big * 1.6 + 6, 30) });
    } else {
      mk = L.circleMarker([m.lat, m.lon], { radius: 1, color: passColor(m.pass), weight: 2.5, fillColor: c, fillOpacity: 0.95 });
      mk._base = big;
    }
    mk._lbl = { text: `${m.name} ${fmtIn(likely(m))}`, r: 1 };
    mk.bindPopup(popupHtml(m), { maxWidth: 320 });
  }
  sizeMarker(mk);
  return mk;
}

// Place each permanent label where it doesn't collide: try right, left, above, below, in list (priority) order.
// If no spot is clear at this zoom, the label becomes hover-only.
const measureCtx = document.createElement("canvas").getContext("2d");
function placeLabels() {
  measureCtx.font = `700 11px ${getComputedStyle(document.body).fontFamily}`;
  const items = Object.values(markers).filter(mk => mk._lbl)
    .map(mk => ({ mk, p: map.latLngToContainerPoint(mk.getLatLng()), ...mk._lbl }));
  const size = map.getSize();
  const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  const taken = items.map(it => ({ x: it.p.x - it.r, y: it.p.y - it.r, w: it.r * 2, h: it.r * 2, own: it }));
  for (const it of items) {
    const w = measureCtx.measureText(it.text).width + 8, h = 16, g = it.r + 6, { x, y } = it.p;
    const spots = [
      { dir: "right",  off: [it.r, 0],  box: { x: x + g, y: y - h / 2, w, h } },
      { dir: "left",   off: [-it.r, 0], box: { x: x - g - w, y: y - h / 2, w, h } },
      { dir: "top",    off: [0, -it.r], box: { x: x - w / 2, y: y - g - h, w, h } },
      { dir: "bottom", off: [0, it.r],  box: { x: x - w / 2, y: y + g, w, h } },
    ];
    const inView = b => b.x >= 0 && b.y >= 0 && b.x + b.w <= size.x && b.y + b.h <= size.y;
    const spot = spots.find(s => inView(s.box) && !taken.some(t => t.own !== it && hit(s.box, t)));
    it.mk.unbindTooltip();
    if (spot) {
      taken.push(spot.box);
      it.mk.bindTooltip(it.text, { permanent: true, direction: spot.dir, offset: spot.off, className: "label" });
    } else {
      it.mk.bindTooltip(it.text, { direction: "right", offset: [it.r, 0], className: "label" });
    }
  }
}
map.on("zoomend", () => { Object.values(markers).forEach(sizeMarker); placeLabels(); showZoneMarkers(); });
map.on("moveend", placeLabels);

let bc = null, bcDay = 0, bcLayer = null, bcZoneLayer = null, bcLines = {};
const ZONE_ORDER = ["presidentials", "mansfield", "adirondacks", "katahdin", "gba-maine"];
const zoneRank = k => { const i = ZONE_ORDER.indexOf(k); return i < 0 ? 99 : i; };
const bcZoneByKey = () => Object.fromEntries((bc?.zones || []).map(z => [z.key, z]));
const ZONE_MARKER_MAX_ZOOM = 11; // below this, lines are too small to see: show one triangle per zone instead
const AVY_COLORS = { 1: "#50b848", 2: "#fff200", 3: "#f7941e", 4: "#ed1c24", 5: "#231f20" };
// Lines need to read on the muted basemap, so "not in condition" is a solid warm grey, not the pale --g0 tile color.
const bcColor = d => d.status === "gated" ? css("--muted") : css(d.score >= 65 ? "--g3" : d.score >= 40 ? "--g2" : "--bc-poor");

function avyBanner(z) {
  const a = z.avalanche;
  const color = a.available ? AVY_COLORS[a.level] : css("--muted");
  if (a.no_center) {
    return `<div class="avy" style="--avy:${color}"><div class="sub">${esc(z.name)}${z.state ? ` · ${esc(z.state)}` : ""} · ${z.lines} lines</div>
      <b>No avalanche forecast for this area</b><div class="sub">${esc(a.reason)}</div></div>`;
  }
  return `<div class="avy" style="--avy:${color}"><div class="sub">${esc(z.name)} · ${z.lines} lines · official avalanche forecast (${esc(a.center || "avalanche center")})</div>
    <b>${a.available ? `${esc(a.danger)} (${a.level}/5)` : "No rating issued"}</b>${a.available ? "" : ` <span class="sub">· ${esc(a.reason)}</span>`}
    <div style="margin-top:4px">${extLink(a.link, "Read the full forecast →")}</div></div>`;
}

function bcPopup(p, d) {
  const c = d.conditions, a = bcZoneByKey()[p.zone].avalanche;
  return `<div class="pp">
    <h3>${esc(p.name)}</h3>
    <div class="sub">${esc(p.area)} · faces ${p.aspect} (${p.aspect_deg}°) · ${p.top_ft.toLocaleString()}→${p.bottom_ft.toLocaleString()} ft · ${p.vert_ft} ft vert</div>
    <div class="sub">Mean ${p.slope_mean_deg}° · steepest 30 m ${p.slope_max_deg}°</div>
    <div class="warn" style="border-color:${a.available ? AVY_COLORS[a.level] : css("--muted")}">
      <b>Avalanche danger: ${a.available ? `${esc(a.danger)} (${a.level}/5)` : "no forecast"}</b>${a.link ? ` · ${extLink(a.link, `${a.center || "Official"} forecast`)}` : " · no avalanche center covers this area"}<br>${esc(d.verdict)}</div>
    <table>
      <tr><td colspan="2"><b>${d.label} · conditions ${d.score}/100</b> <span class="sub">${d.conditions_label}</span></td></tr>
      <tr><td>Fresh snow</td><td>${fmtIn(c.fresh_in)} <span class="sub">(${fmtIn(c.fresh_forecast_in)} fcst + ${fmtIn(c.fresh_observed_in)} obs) · ${d.parts.fresh}/35</span></td></tr>
      <tr><td>Season to date</td><td>${c.season_in == null ? "–" : fmtIn(c.season_in)} <span class="sub">· ${d.parts.coverage}/20</span></td></tr>
      <tr><td>Wind</td><td>${d.wind_effect}${c.wind.hours ? ` <span class="sub">(${c.wind.mean_mph} mph from ${dirName(c.wind.from_deg)}, ${c.wind.hours}h)</span>` : ""} <span class="sub">· ${d.parts.wind}/20</span></td></tr>
      <tr><td>Surface</td><td>${d.surface} <span class="sub">· ${d.parts.surface}/25</span></td></tr>
      <tr><td>Temps</td><td>${c.temp_overnight_min_f ?? "–"}°F overnight · ${c.temp_day_max_f ?? "–"}°F high</td></tr>
    </table>
    ${d.flags.length ? `<b style="font-size:12px">Hazard flags</b><ul class="flags">${d.flags.map(f => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
    <div class="sub">${esc(p.hazards)}</div>
    <div class="sub" style="margin-top:6px">Geometry: OSM ${(p.osm_ways || [p.osm_way]).map(w => extLink(`https://www.openstreetmap.org/way/${+w}`, `way ${+w}`)).join(", ")}. Not for navigation.</div>
  </div>`;
}

function showZoneMarkers() {
  if (!bcZoneLayer) return;
  if (view === "backcountry" && map.getZoom() < ZONE_MARKER_MAX_ZOOM) bcZoneLayer.addTo(map); else bcZoneLayer.remove();
}

function renderBackcountry(list) {
  if (!bc?.zones) { list.innerHTML = `<div class="err">No data/backcountry.json yet. Run <code>./update.sh</code>.</div>`; return; }
  const zones = [...bc.zones].sort((a, b) => zoneRank(a.key) - zoneRank(b.key));
  document.getElementById("bc-zones").innerHTML = zones.map(z =>
    `<button data-z="${esc(z.key)}" aria-pressed="${!bcOff.has(z.key)}">${esc(z.name)}</button>`).join("");
  document.getElementById("bc-days").innerHTML = bc.days.map((d, i) =>
    `<button data-d="${i}" aria-pressed="${i === bcDay}">${esc(d.label)}</button>`).join("");
  bcLayer?.remove(); bcZoneLayer?.remove();
  bcLines = {};
  const on = zones.filter(z => !bcOff.has(z.key));
  const feats = bc.features.filter(f => !bcOff.has(f.properties.zone));
  bcLayer = L.geoJSON({ type: "FeatureCollection", features: feats }, {
    style: f => { const d = f.properties.days[bcDay]; return { color: bcColor(d), weight: 5, opacity: 0.95,
      dashArray: d.status === "no_forecast" ? "6 6" : d.status === "gated" ? "2 6" : null }; },
    onEachFeature: (f, layer) => {
      const p = f.properties, d = p.days[bcDay];
      bcLines[p.key] = layer;
      layer.bindPopup(bcPopup(p, d), { maxWidth: 340 })
           .bindTooltip(`${esc(p.name)} ${d.status === "gated" ? "—" : d.score}`, { sticky: true, className: "label" });
    },
  }).addTo(map);
  // Zoomed out, lines are too small to see: one triangle per cluster of areas (areas within ~15 km merge,
  // so the Presidentials get one marker but the two far-apart GBA glade areas get one each). Click to zoom in.
  const clusters = [];
  for (const z of on) {
    const byArea = {};
    bcLayer.getLayers().filter(l => l.feature.properties.zone === z.key)
      .forEach(l => (byArea[l.feature.properties.area] ??= []).push(...l.getLatLngs()));
    const groups = [];
    for (const [area, pts] of Object.entries(byArea)) {
      const b = L.latLngBounds(pts);
      const g = groups.find(g => g.b.getCenter().distanceTo(b.getCenter()) < 15000);
      if (g) { g.b.extend(b); g.areas.push(area); } else groups.push({ b, areas: [area] });
    }
    groups.forEach(g => clusters.push({ z, b: g.b, name: groups.length > 1 ? `${z.name.replace(/ \(.*\)$/, "")}: ${g.areas.join(", ")}` : z.name }));
  }
  bcZoneLayer = L.layerGroup(clusters.map(({ z, b, name }) =>
    L.marker(b.getCenter(), { icon: triIcon(z.avalanche.available ? AVY_COLORS[z.avalanche.level] : css("--panel"), 18) })
      .bindTooltip(esc(name), { permanent: true, direction: "right", offset: [9, 0], className: "label" })
      .on("click", () => map.fitBounds(b, { padding: [40, 40], maxZoom: 14 }))));
  showZoneMarkers();
  if (!on.length) { list.innerHTML = `<div class="err">All backcountry zones are turned off. Turn one on above.</div>`; return; }
  // Gated lines always sort last, whatever their conditions score: they are never presented as in condition.
  const rank = d => (d.status === "gated" ? -1000 : 0) + d.score;
  list.innerHTML = on.map(z => {
    const ps = feats.map(f => f.properties).filter(p => p.zone === z.key)
      .sort((a, b) => rank(b.days[bcDay]) - rank(a.days[bcDay]) || a.name.localeCompare(b.name));
    return `<li class="zone-head" tabindex="0" data-zone="${esc(z.key)}">${avyBanner(z)}</li>` + ps.map(p => { const d = p.days[bcDay]; return `
    <li tabindex="0" data-line="${esc(p.key)}">
      <span class="sw" style="background:${bcColor(d)}"></span>
      <span><span class="nm">${esc(p.name)}</span> <span class="sub">· ${esc(p.area)} · ${esc(p.aspect)} · ${p.slope_mean_deg}°/${p.slope_max_deg}°</span>
        <div class="verdict ${esc(d.status)}">${esc(d.verdict)}</div>
        <div class="why">${esc(d.conditions_label)} · ${esc(d.surface)} · ${esc(d.wind_effect)}</div>
        ${d.flags.length ? `<div class="flag">⚠ ${esc(d.flags[0])}${d.flags.length > 1 ? ` +${d.flags.length - 1}` : ""}</div>` : ""}</span>
      <span class="amt">${d.status === "gated" ? "—" : d.score}<small>${d.status === "gated" ? "gated" : "/100"}</small></span>
    </li>`; }).join("");
  }).join("");
}

function visibleRows() {
  return rows.filter(m => {
    if (view === "backcountry") return false;
    if (m.pass === "Backcountry") return view === "storm" && showBC;   // weekend view ranks resorts only
    if (filter !== "all" && m.pass !== filter) return false;
    if (view === "weekend") {
      const r = wkById[m.id];
      if (!r) return false;
      if (maxDrive && !(r.drive_h <= maxDrive)) return false;
    }
    return true;
  });
}

function weekendSummary(ranked) {
  const el = document.getElementById("summary");
  const open = ranked.filter(r => !r.days.every(d => d.closed));
  const closed = ranked.length - open.length;
  if (!open.length) {
    el.innerHTML = ranked.length ? "<b>None of these resorts are open this weekend.</b>" : "<b>No resorts match these filters.</b>";
    return;
  }
  const best = open[0], [, g] = grade(best.score);
  const others = open.slice(1, 3).map(r => `${esc(r.name)} (${r.score})`).join(", ");
  const bestDays = open.map(r => r.days.find(x => x.label === r.best_day));
  const rainy = bestDays.filter(d => d.surface === "Rain-affected").length;
  let why = "";
  if (best.score < 30) why = `Nothing scores better than Poor this weekend${rainy > open.length / 2 ? ": most resorts are rain-affected" : ""}.`;
  el.innerHTML = `<b>Best bet: ${esc(best.name)}</b> · ${esc(best.best_day)} · ${best.score} ${g} · ${fmtH(best.drive_h)} drive
    ${others ? `<span class="sub">Next: ${others}</span>` : ""}
    ${why ? `<span class="sub">${why}</span>` : ""}
    ${closed ? `<span class="sub">${closed} resort${closed > 1 ? "s" : ""} in this list ${closed > 1 ? "aren't" : "isn't"} open yet (announced opening dates).</span>` : ""}`;
}

function render() {
  Object.values(markers).forEach(mk => mk.remove());
  markers = {};
  const vis = visibleRows();
  const ranked = view === "weekend" ? vis.map(m => wkById[m.id]).sort((a, b) => a.rank - b.rank) : [];
  fRank = Object.fromEntries(ranked.map((r, i) => [r.id, i + 1]));  // rank within the current filters
  // Add in priority order (best first) so the top mountains claim label space first.
  const prio = view === "weekend" ? [...vis].sort((a, b) => wkById[a.id].rank - wkById[b.id].rank)
    : [...vis].sort((a, b) => likely(b) - likely(a));
  prio.forEach(m => markers[m.id] = marker(m).addTo(map));
  placeLabels();
  document.getElementById("meta").textContent = metaText[view];
  document.getElementById("title").textContent = view === "storm" ? "Northeast Storm Totals · next 72h"
    : view === "backcountry" ? `Backcountry Lines${bc?.zones ? ` · ${bc.zones.length} zones` : ""}`
    : `Where to Ski This Weekend${wk?.weekend?.length ? " · " + wk.weekend.join(" / ") : ""}`;
  document.getElementById("legend-storm").hidden = view !== "storm";
  document.getElementById("legend-weekend").hidden = view !== "weekend";
  document.getElementById("legend-backcountry").hidden = view !== "backcountry";
  document.getElementById("bc-head").hidden = view !== "backcountry";
  document.getElementById("pass-filters").hidden = view === "backcountry";
  document.querySelector("[data-bc]").hidden = document.getElementById("bc-sep").hidden = view !== "storm";
  document.getElementById("wk-tools").hidden = document.getElementById("summary").hidden = view !== "weekend";
  const list = document.getElementById("list");
  if (view === "backcountry") { renderBackcountry(list); return; }
  bcLayer?.remove(); bcLayer = null; bcZoneLayer?.remove(); bcZoneLayer = null;

  if (view === "weekend") {
    if (!wk) { list.innerHTML = `<div class="err">No data/weekend_rank.json yet. Run <code>./update.sh</code>.</div>`; return; }
    weekendSummary(ranked);
    list.innerHTML = ranked.map(r => {
      const d = r.days.find(x => x.label === r.best_day);
      const [, g, gv] = grade(r.score);
      return `<li class="wk" tabindex="0" data-id="${esc(r.id)}">
        <span class="rank">${fRank[r.id]}</span>
        <span><span class="nm">${esc(r.name)}</span>
          <span class="sub"> · <span class="pass ${esc(r.pass)}">${esc(r.pass.toUpperCase())}</span> · ${esc(r.state)}</span> ${seasonChip(r)}
          <div class="why">${esc(r.best_day)}: ${d.closed ? "not open yet" : esc(d.surface)}${d.notes.length ? " · " + d.notes.slice(d.closed ? 1 : 0, 2).map(esc).join(" · ") : ""}</div>
          <div class="factors"><span>Fresh <b>${fmtIn(d.fresh_in)}</b></span><span><b>${d.temp_lift_min_f ?? "–"}–${d.temp_lift_max_f ?? "–"}°F</b></span><span>Gusts <b>${d.gust_mph ?? "–"}</b></span><span>Drive <b>${fmtH(r.drive_h)}</b></span></div>
        </span>
        <span class="amt">${r.score}<span class="grade" style="color:var(${gv === "--g0" ? "--muted" : gv === "--g1" ? "--g2" : gv})">${g}</span></span>
      </li>`;
    }).join("");
    return;
  }

  list.innerHTML = vis.slice().sort((a, b) => likely(b) - likely(a) || (b.range?.p90 ?? 0) - (a.range?.p90 ?? 0) || a.name.localeCompare(b.name)).map(m => `
    <li tabindex="0" data-id="${esc(m.id)}">
      <span class="sw ${m.pass === "Backcountry" ? "tri" : ""}" style="background:${snowColor(likely(m))}"></span>
      <span><span class="nm">${esc(m.name)}</span><br>
        <span class="sub"><span class="pass ${esc(m.pass)}">${esc(m.pass.toUpperCase())}</span> · ${esc(m.state)} · ${m.temp_min_f}–${m.temp_max_f}°F</span>
        ${mostlyRain(m) ? `<br><span class="rain">Mostly rain/mix · ${m.qpf_72h_in.toFixed(2)}″ liquid</span>` : ""}
        ${rangeBar(m.range)}</span>
      <span class="amt">${fmtIn(likely(m))}<span class="rng">${m.range ? rangeText(m.range) : "NWS only"}</span></span>
    </li>`).join("");
}

document.getElementById("pass-filters").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.hasAttribute("data-bc")) {
    showBC = !showBC; store.set("showBC", showBC);
    b.setAttribute("aria-pressed", showBC);
  } else {
    filter = b.dataset.f;
    document.querySelectorAll("#pass-filters button[data-f]").forEach(x => x.setAttribute("aria-pressed", x === b));
  }
  render();
  homeView();
});
const maxSel = document.getElementById("maxdrive");
maxSel.value = String(maxDrive);
maxSel.addEventListener("change", () => { maxDrive = +maxSel.value; store.set("maxDrive", maxDrive); render(); homeView(); });
document.querySelector("[data-bc]").setAttribute("aria-pressed", showBC);
document.getElementById("bc-zones").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  const k = b.dataset.z;
  bcOff.has(k) ? bcOff.delete(k) : bcOff.add(k);
  store.set("bcOff", [...bcOff]);
  map.closePopup(); render();
  if (bcLayer?.getLayers().length) map.fitBounds(bcLayer.getBounds(), { padding: [30, 30], maxZoom: 14 });
});
function setView(v) {
  view = v;
  document.querySelectorAll(".views button").forEach(x => x.setAttribute("aria-selected", x.dataset.v === v));
  try { history.replaceState(null, "", v === "storm" ? "#" : "#" + v); } catch {}
  map.closePopup();
  render();
  if (v === "backcountry" && bcLayer) map.fitBounds(bcLayer.getBounds(), { padding: [30, 30], maxZoom: 14 });
  else if (v !== "backcountry") homeView();
}
window.addEventListener("hashchange", () => {
  const h = location.hash.slice(1);
  if (rows.length) setView(h === "weekend" || h === "backcountry" ? h : "storm");
});
document.getElementById("bc-days").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  bcDay = +b.dataset.d; map.closePopup(); render();
});
document.querySelector(".views").addEventListener("click", e => {
  const b = e.target.closest("button"); if (b) setView(b.dataset.v);
});
document.getElementById("list").addEventListener("click", e => {
  const li = e.target.closest("li"); if (!li) return;
  if (li.dataset.zone) {
    const ls = bcLayer.getLayers().filter(l => l.feature.properties.zone === li.dataset.zone);
    if (ls.length) map.fitBounds(L.featureGroup(ls).getBounds(), { padding: [40, 40], maxZoom: 14 });
    return;
  }
  if (li.dataset.line) {
    const layer = bcLines[li.dataset.line];
    map.fitBounds(layer.getBounds(), { padding: [60, 60], maxZoom: 15 });
    layer.openPopup(layer.getCenter ? layer.getCenter() : undefined);
    return;
  }
  const m = rows.find(r => r.id === li.dataset.id);
  if (!m) return;
  map.flyTo([m.lat, m.lon], 9, { duration: 0.6 });
  markers[m.id].openPopup();
});
document.getElementById("list").addEventListener("keydown", e => { if (e.key === "Enter") e.target.click(); });

const getJson = url => fetch(url, { cache: "no-store" }).then(r => { if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json(); });
Promise.all([
  getJson("data/storm_totals.json"),
  getJson("data/storm_ranges.json").catch(() => null),
  getJson("data/weekend_rank.json").catch(() => null),
  getJson("data/backcountry.json").catch(() => null),
])
  .then(([d, ranges, weekend, backcountry]) => {
    bc = backcountry;
    ppp = ranges;
    wk = weekend;
    wkById = Object.fromEntries((weekend?.resorts || []).map(r => [r.id, r]));
    infoById = wkById;  // season status + snow-report links ride along with the weekend ranking
    seasonAsOf = weekend?.season_info?.as_of ?? null;
    const byId = Object.fromEntries((ranges?.mountains || []).map(r => [r.id, r]));
    rows = d.mountains.filter(m => !m.error).map(m => ({ ...m, window_start: d.window_start, range: byId[m.id] || null }));
    // Shared scale for every range bar so mountains compare visually; at least 2" so a dusting doesn't fill the bar.
    barMax = Math.max(2, ...rows.map(m => m.range?.p90 ?? 0));
    const failed = d.mountains.length - rows.length;
    metaText.storm =
      (ranges ? `WPC ranges ${fmtWin(ranges.window)} (cycle ${ranges.cycle.slice(8)}Z) · ` : "No WPC ranges · ") +
      `NWS from ${new Date(d.window_start).toLocaleString([], { weekday: "short", hour: "numeric" })} · fetched ${new Date(d.generated_at).toLocaleString()}` +
      (failed ? ` · ${failed} mountain(s) failed` : "");
    metaText.weekend = weekend
      ? `Lift hours 9–4 ET · from ${weekend.home?.name ?? "home"} · scored ${new Date(weekend.generated_at).toLocaleString()}`
      : "No weekend ranking yet";
    document.getElementById("caveats").innerHTML = (weekend?.caveats || []).map(c => `• ${c}`).join("<br>");
    metaText.backcountry = bc ? `Lines scored ${new Date(bc.generated_at).toLocaleString()} · avalanche rating from avalanche.org` : "No backcountry data yet";
    const h = location.hash.slice(1);
    setView(h === "weekend" || h === "backcountry" ? h : "storm");
  })
  .catch(err => {
    document.getElementById("list").outerHTML = `<div class="err">Couldn't load data/storm_totals.json (${err.message}). Run <code>./update.sh</code>, then serve this folder with <code>python3 -m http.server</code>.</div>`;
  });
