"use strict";
// Metal Focus dashboard. All text goes in via textContent (window titles are untrusted strings).

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const CATS = ["Coding", "Docs", "Chat", "Video", "Social", "Other", "Excluded", "Away"];
const STATES = ["Working", "Short break", "Drifting", "Rabbit hole", "Away", "Unlabeled"];
const SVG = "http://www.w3.org/2000/svg";
const $ = (s) => document.querySelector(s);

let days = [], current = null, dayData = null;

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") el.className = v; else if (k === "style") el.style.cssText = v; else el.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
function s(tag, attrs = {}) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  return el;
}
const catVar = (c) => `var(--cat-${c}, var(--cat-Other))`;
const stVar = (st) => `var(--st-${(st || "Unlabeled").replace(/ /g, "-")})`;
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const clockS = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);
function dur(min) {
  if (min < 1) return `${Math.round(min * 60)}s`;
  const hh = Math.floor(min / 60), mm = Math.round(min % 60);
  return hh ? `${hh}h ${String(mm).padStart(2, "0")}m` : `${mm}m`;
}

async function api(path, params = {}) {
  const u = new URL(path, location.origin);
  u.searchParams.set("t", TOKEN);
  for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
  const r = await fetch(u, { cache: "no-store" });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}
function showError(msg) { const e = $("#error"); e.hidden = !msg; e.textContent = msg || ""; }

// ---------- tiles ----------
function renderTiles(d) {
  const t = d.totals, ms = t.minutes_by_state;
  const distracted = (ms["Short break"] || 0) + (ms["Drifting"] || 0) + (ms["Rabbit hole"] || 0);
  const tile = (k, v, sub) => h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), h("div", { class: "s" }, sub || ""));
  $("#tiles").replaceChildren(
    tile("Logged", dur(t.logged_min), `${t.samples.toLocaleString()} samples`),
    tile("Working", dur(ms["Working"] || 0), "labeled windows"),
    tile("Distracted", dur(distracted), `drifting + rabbit hole ${dur((ms["Drifting"] || 0) + (ms["Rabbit hole"] || 0))}`),
    tile("Rabbit holes", String(t.rabbit_holes), "sessions"),
    tile("Check-ins", `${t.checkins_answered}/${t.checkins_asked}`, "answered / asked"),
    tile("Nudges", String(t.nudges), "shown"),
  );
}

// ---------- timeline ----------
function runs(ws, key) {
  const out = [];
  for (const w of ws) {
    const v = key(w), last = out[out.length - 1];
    if (last && last.v === v && Math.abs(last.end - w.start) < 1) last.end = w.start + 120, last.n++;
    else out.push({ v, start: w.start, end: w.start + 120, n: 1 });
  }
  return out;
}

function renderTimeline(d) {
  const svg = $("#timeline"), tip = $("#tip");
  svg.replaceChildren();
  const ws = d.windows;
  if (!ws.length) {
    svg.setAttribute("height", 40);
    const t = s("text", { x: 0, y: 24 }); t.textContent = "No completed windows for this day yet."; svg.append(t);
    return;
  }
  const W = svg.clientWidth || 900, L = 84, R = 8;
  const t0 = Math.floor(ws[0].start / 3600) * 3600, t1 = Math.ceil((ws[ws.length - 1].start + 120) / 3600) * 3600;
  const x = (ts) => L + ((ts - t0) / (t1 - t0)) * (W - L - R);
  const rows = [{ key: "cat", label: "Category", h: 22 }, { key: "state", label: "Label", h: 22 }];
  if (d.has_readouts) rows.push({ key: "risk", label: "Risk", h: 64 });
  rows.push({ key: "marks", label: "Check-ins", h: 16 });
  let y = 4;
  for (const r of rows) { r.y = y; y += r.h + 10; }
  const axisY = y + 4, H = axisY + 14;
  svg.setAttribute("height", H);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);

  const step = (t1 - t0) / 3600 > 12 ? 2 : 1;
  const ax = s("g", { class: "axis" });
  for (let t = t0; t <= t1; t += 3600 * step) {
    ax.append(s("line", { class: "gridline", x1: x(t), x2: x(t), y1: 0, y2: axisY - 6 }));
    const tx = s("text", { x: x(t), y: axisY + 6, "text-anchor": "middle" }); tx.textContent = clock(t); ax.append(tx);
  }
  svg.append(ax);

  for (const r of rows) {
    const lab = s("text", { x: 0, y: r.y + r.h / 2 + 4 }); lab.textContent = r.label; svg.append(lab);
    const g = s("g");
    if (r.key === "cat" || r.key === "state") {
      const key = r.key === "cat" ? (w) => w.category : (w) => w.state || "Unlabeled";
      const color = r.key === "cat" ? catVar : stVar;
      for (const run of runs(ws, key)) {
        const x0 = x(run.start), wpx = Math.max(1, x(run.end) - x0 - 2);
        g.append(s("rect", { x: x0, y: r.y, width: wpx, height: r.h, rx: Math.min(4, wpx / 2), fill: color(run.v) }));
      }
    } else if (r.key === "risk") {
      const yv = (v) => r.y + r.h - v * r.h;
      const thr = 0.6;
      g.append(s("line", { class: "thr", x1: L, x2: W - R, y1: yv(thr), y2: yv(thr) }));
      const tl = s("text", { x: W - R, y: yv(thr) - 4, "text-anchor": "end" }); tl.textContent = "nudge 0.6"; g.append(tl);
      let dpath = "", pen = false;
      for (const w of ws) {
        if (w.risk == null) { pen = false; continue; }
        dpath += `${pen ? "L" : "M"}${x(w.start + 60).toFixed(1)},${yv(w.risk).toFixed(1)}`; pen = true;
      }
      g.append(s("line", { class: "gridline", x1: L, x2: W - R, y1: r.y + r.h, y2: r.y + r.h }));
      if (dpath) g.append(s("path", { class: "risk", d: dpath }));
    } else {
      for (const c of d.checkins) {
        const answered = c.response && !["skip", "dismissed"].includes(c.response);
        g.append(s("circle", { class: "ck", cx: x(c.asked_at), cy: r.y + r.h / 2, r: 5, fill: answered ? stVar(c.response) : "var(--text-muted)" }));
      }
      for (const n of d.nudges) {
        const cx = x(n.window_start + 120), cy = r.y + r.h / 2;
        g.append(s("path", { class: "ck", d: `M${cx},${cy - 6}L${cx + 6},${cy + 5}L${cx - 6},${cy + 5}Z`, fill: n.holdout ? "var(--text-muted)" : "var(--accent)" }));
      }
    }
    svg.append(g);
  }

  const cross = s("line", { class: "cross", y1: 0, y2: axisY - 6, visibility: "hidden" });
  const hit = s("rect", { x: L, y: 0, width: W - L - R, height: axisY, fill: "transparent" });
  svg.append(cross, hit);
  const scale = () => svg.getBoundingClientRect().width / W;
  hit.addEventListener("mousemove", (ev) => {
    const box = svg.getBoundingClientRect();
    const px = (ev.clientX - box.left) / scale();
    const ts = t0 + ((px - L) / (W - L - R)) * (t1 - t0);
    let i = ws.findIndex((w) => ts >= w.start && ts < w.start + 120);
    if (i < 0) { cross.setAttribute("visibility", "hidden"); tip.hidden = true; return; }
    const w = ws[i];
    cross.setAttribute("x1", x(w.start + 60)); cross.setAttribute("x2", x(w.start + 60)); cross.setAttribute("visibility", "visible");
    const row = (k, v) => h("div", { class: "row" }, h("span", {}, k), h("span", {}, v));
    const ck = d.checkins.find((c) => Math.abs(c.asked_at - (w.start + 60)) <= 60);
    tip.replaceChildren(
      h("b", {}, `${clock(w.start)}–${clock(w.start + 120)}`),
      row("Category", w.category + (w.low_confidence ? " (unsure)" : "")),
      row("App / site", w.dominant || "—"),
      row("Label", w.state ? `${w.state} · ${w.source}` : "Unlabeled"),
      row("Switches", w.switches), row("Idle", `${Math.round(w.idle_s)} s`), row("Typing", pct(w.typing)),
      w.media ? row("Video playing", pct(w.media)) : null,
      w.risk != null ? row("Risk", pct(w.risk)) : null,
      ck ? row("Check-in", `${ck.trigger}: ${ck.response || "no answer"}`) : null,
    );
    tip.hidden = false;
    const wrap = svg.parentElement.getBoundingClientRect();
    let left = ev.clientX - wrap.left + 14;
    if (left + tip.offsetWidth > wrap.width) left = ev.clientX - wrap.left - tip.offsetWidth - 14;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${ev.clientY - wrap.top + 12}px`;
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

function renderLegends(d) {
  const present = new Set(d.windows.map((w) => w.category));
  const presentSt = new Set(d.windows.map((w) => w.state || "Unlabeled"));
  const item = (name, color) => h("span", {}, h("span", { class: "sw", style: `background:${color}` }), name);
  $("#legend-cat").replaceChildren(h("span", { class: "lt" }, "Category"), ...CATS.filter((c) => present.has(c)).map((c) => item(c, catVar(c))));
  $("#legend-state").replaceChildren(h("span", { class: "lt" }, "Label"), ...STATES.filter((c) => presentSt.has(c)).map((c) => item(c, stVar(c))));
}

// ---------- bars & tables ----------
function bars(el, items, color) {
  if (!items.length) return el.replaceChildren(h("div", { class: "empty" }, "Nothing yet."));
  const max = Math.max(...items.map((i) => i.min));
  el.replaceChildren(...items.map((i) => h("div", { class: "brow", title: i.name },
    h("span", { class: "name" }, i.name),
    h("div", { class: "track" }, h("div", { class: "fill", style: `width:${(100 * i.min) / max}%;${color ? `background:${color(i.name)}` : ""}` })),
    h("span", { class: "val" }, dur(i.min)))));
}
const fromSec = (rows) => rows.map((r) => ({ name: r.name, min: r.seconds / 60, app: r.app }));
const byKey = (obj, order) => order.filter((k) => obj[k]).map((k) => ({ name: k, min: obj[k] }));

function renderPrompts(d) {
  const rows = [
    ...d.checkins.map((c) => ({ ts: c.asked_at, kind: `Check-in${c.split === "eval" ? " (eval)" : ""}`,
      detail: `${c.trigger}${c.model_top_state ? ` · model: ${c.model_top_state} ${pct(c.model_top_prob)}` : ""}`, resp: c.response || "—" })),
    ...d.nudges.map((n) => ({ ts: n.window_start + 120, kind: n.holdout ? "Nudge (held out)" : "Nudge",
      detail: `risk ${pct(n.risk)}${n.body ? ` · ${n.body}` : ""}`, resp: n.response || "—" })),
  ].sort((a, b) => a.ts - b.ts);
  const tb = $("#prompts tbody");
  if (!rows.length) return tb.replaceChildren(h("tr", {}, h("td", { colspan: 4, class: "muted" }, "No check-ins or nudges this day.")));
  tb.replaceChildren(...rows.map((r) => h("tr", {}, h("td", { class: "t" }, clock(r.ts)), h("td", {}, r.kind), h("td", { class: "title" }, r.detail), h("td", {}, r.resp))));
}

function renderTitles(d) {
  const tb = $("#titles tbody");
  if (!d.top_titles.length) return tb.replaceChildren(h("tr", {}, h("td", { colspan: 3, class: "muted" }, "No window titles (Accessibility off or nothing logged).")));
  tb.replaceChildren(...d.top_titles.map((r) => h("tr", {}, h("td", {}, r.app || "—"), h("td", { class: "title" }, r.name), h("td", { class: "num" }, dur(r.seconds / 60)))));
}

function renderWindows(d) {
  $("#windows tbody").replaceChildren(...d.windows.map((w) => h("tr", {},
    h("td", { class: "t" }, clock(w.start)), h("td", {}, w.category), h("td", {}, w.dominant || "—"),
    h("td", {}, w.state || "—"), h("td", { class: "muted" }, w.source || ""), h("td", { class: "num" }, w.switches),
    h("td", { class: "num" }, Math.round(w.idle_s)), h("td", { class: "num" }, pct(w.typing)), h("td", { class: "num" }, pct(w.risk)))));
}

function renderDay(d) {
  renderTiles(d);
  renderTimeline(d);
  renderLegends(d);
  bars($("#by-cat"), byKey(d.totals.minutes_by_category, CATS), catVar);
  bars($("#by-state"), byKey(d.totals.minutes_by_state, STATES), stVar);
  bars($("#apps"), fromSec(d.top_apps));
  bars($("#domains"), fromSec(d.top_domains));
  renderTitles(d);
  renderPrompts(d);
  renderWindows(d);
}

// ---------- live ----------
async function refreshLive() {
  try {
    const lv = await api("/api/live");
    const st = $("#status");
    const age = lv.last_sample ? Math.round(lv.now - lv.last_sample) : null;
    st.className = `pill ${lv.running ? "on" : "off"}`;
    st.textContent = lv.running ? "Sampling" : age == null ? "No samples yet" : `Stopped · last sample ${dur(age / 60)} ago`;
    if (lv.permission_missing_at_start) st.textContent += ` · ${lv.permission_missing_at_start} was off at start`;
    const flag = (on, name) => (on ? h("span", { class: "tag" }, name) : null);
    $("#live tbody").replaceChildren(...lv.samples.map((r) => h("tr", {},
      h("td", { class: "t" }, clockS(r.ts)),
      h("td", {}, r.excluded ? "Excluded" : r.app_name || r.bundle_id || "—"),
      h("td", { class: "title" }, r.window_title || h("span", { class: "muted" }, r.excluded ? "hidden (excluded)" : r.private ? "hidden (private)" : "—")),
      h("td", {}, r.domain || "—"),
      h("td", {}, flag(r.private, "private"), flag(r.excluded, "excluded"), flag(r.fullscreen, "fullscreen"), flag(r.locked, "locked"), flag(r.media_playing, "video")),
      h("td", { class: "num" }, r.idle_s == null ? "—" : `${r.idle_s.toFixed(0)} s`),
      h("td", {}, flag(r.key_active, "type"), flag(r.click_active, "click"), flag(r.scroll_active, "scroll")))));
    showError("");
  } catch (e) { showError(`Can't read the database: ${e.message}`); }
}

// ---------- day navigation ----------
async function loadDay(day) {
  current = day;
  $("#day").value = day;
  const i = days.indexOf(day);
  $("#prev").disabled = i < 0 || i >= days.length - 1;
  $("#next").disabled = i <= 0;
  try { dayData = await api("/api/day", { d: day }); renderDay(dayData); showError(""); }
  catch (e) { showError(`Can't load ${day}: ${e.message}`); }
}

async function init() {
  if (!TOKEN) return showError("Open the link printed by `uv run python -m dashboard` (it carries an access token).");
  try { days = (await api("/api/days")).days; }
  catch (e) { return showError(`Can't read the database: ${e.message}`); }
  $("#day").replaceChildren(...days.map((d) => h("option", { value: d }, d)));
  $("#day").addEventListener("change", (e) => loadDay(e.target.value));
  $("#prev").addEventListener("click", () => loadDay(days[days.indexOf(current) + 1]));
  $("#next").addEventListener("click", () => loadDay(days[days.indexOf(current) - 1]));
  let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => dayData && renderTimeline(dayData), 120); });
  if (days.length) await loadDay(days[0]);
  refreshLive();
  setInterval(refreshLive, 5000);
  // keep today's timeline current as windows complete
  setInterval(() => { if (current && current === days[0]) loadDay(current); }, 60000);
}
init();
