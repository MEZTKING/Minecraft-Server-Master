"""Self-contained HTML statistics report.

The report is a single ``.html`` file: all data is embedded as JSON and
rendered by a small vanilla-JavaScript app (no internet connection or
libraries needed). Open it in any browser.

Tabs:
    Overview     server/world facts, totals, warnings
    Activity     online hours per day, weekday x hour heatmap, per-player time
    Players      leaderboard table + a detailed page per player
    Events       every event, filterable by type / player / text / date
    Replays      every .mcpr with time range and how many events it covers
    Files        which log files were read and how they were dated

Times are shown in the *viewer's* local time zone (the browser converts
from the UTC values in the data).
"""

from __future__ import annotations

import json

from .. import models as m
from ..players import play_time_ticks
from ..timeutil import iso


def _payload(data: m.ServerData) -> dict:
    players = []
    for p in sorted(data.players.values(), key=lambda p: -play_time_ticks(p.stats)):
        players.append({
            "name": p.name, "uuid": p.uuid, "first_seen": iso(p.first_seen), "last_seen": iso(p.last_seen),
            "sessions": p.session_count, "session_seconds": round(p.session_seconds),
            "play_ticks": play_time_ticks(p.stats), "chat": p.chat_messages, "deaths_logged": p.deaths_logged,
            "is_op": p.is_op, "is_whitelisted": p.is_whitelisted, "is_banned": p.is_banned,
            "stats": p.stats, "playerdata": p.playerdata,
            "advancements": [{"id": k, "done": v["done"], "completed": iso(v.get("completed"))}
                             for k, v in sorted(p.advancements.items(),
                                                key=lambda kv: iso(kv[1].get("completed")) or "~")],
        })
    return {
        "generated_at": iso(data.generated_at),
        "timezone_info": data.timezone_info,
        "warnings": data.warnings,
        "server_properties": data.server_properties,
        "world": m.to_jsonable(data.world),
        "mods": data.mods,
        "players": players,
        # Compact event rows: [epoch_ms, type, player, text, source, line, data]
        "events": [[int(e.time.timestamp() * 1000), e.type, e.player, e.text, e.source, e.line,
                    m.to_jsonable(e.data)] for e in data.events],
        "sessions": [[s.player, int(s.start.timestamp() * 1000),
                      int(s.end.timestamp() * 1000) if s.end else None, s.end_reason] for s in data.sessions],
        "replays": [{"path": r.path, "start": iso(r.start), "end": iso(r.end), "duration_ms": r.duration_ms,
                     "server_name": r.server_name, "mc_version": r.mc_version, "generator": r.generator,
                     "recorded_player": r.recorded_player, "players": r.player_names,
                     "format": r.format, "start_source": r.start_source, "unfinished": r.unfinished,
                     "markers": r.markers, "size": r.size_bytes} for r in data.replays],
        "log_files": [m.to_jsonable(f) for f in data.log_files],
    }


def render(data: m.ServerData, title: str | None = None) -> str:
    payload = json.dumps(_payload(data), ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # never let data close the <script> tag
    name = title or data.server_properties.get("motd") or data.world.get("level_name") or "Minecraft server"
    import html
    return (_TEMPLATE.replace("__TITLE__", html.escape(str(name)))
                     .replace("__DATA__", payload))


def write_report(data: m.ServerData, path: str, title: str | None = None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(data, title))


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ – Server Report</title>
<style>
:root {
  color-scheme: light;
  --bg: #f4f4f2; --surface: #fcfcfb; --surface-2: #f0efec; --border: #e2e1dc;
  --text: #0b0b0b; --text-2: #52514e; --muted: #7a7975;
  --accent: #2a78d6; --series-1: #2a78d6;
  --seq-0: #f0efec; --seq-1: #cde2fb; --seq-2: #9ec5f4; --seq-3: #6da7ec; --seq-4: #3987e5; --seq-5: #256abf; --seq-6: #184f95; --seq-7: #0d366b;
  --good: #1a7f37; --warn: #9a6700; --bad: #c62828;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #121211; --surface: #1a1a19; --surface-2: #242422; --border: #33332f;
    --text: #f0efec; --text-2: #c3c2b7; --muted: #8f8e88;
    --accent: #3987e5; --series-1: #3987e5;
    --seq-0: #242422; --seq-1: #0d366b; --seq-2: #104281; --seq-3: #184f95; --seq-4: #1c5cab; --seq-5: #2a78d6; --seq-6: #5598e7; --seq-7: #86b6ef;
    --good: #4ac26b; --warn: #d4a72c; --bad: #ff7b72;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #121211; --surface: #1a1a19; --surface-2: #242422; --border: #33332f;
  --text: #f0efec; --text-2: #c3c2b7; --muted: #8f8e88;
  --accent: #3987e5; --series-1: #3987e5;
  --seq-0: #242422; --seq-1: #0d366b; --seq-2: #104281; --seq-3: #184f95; --seq-4: #1c5cab; --seq-5: #2a78d6; --seq-6: #5598e7; --seq-7: #86b6ef;
  --good: #4ac26b; --warn: #d4a72c; --bad: #ff7b72;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
header { padding: 20px 16px 0; max-width: 1200px; margin: 0 auto; }
header h1 { margin: 0 0 4px; font-size: 22px; }
header .sub { color: var(--text-2); font-size: 13px; }
nav { max-width: 1200px; margin: 12px auto 0; padding: 0 16px; display: flex; gap: 4px; flex-wrap: wrap; border-bottom: 1px solid var(--border); }
nav button { background: none; border: 0; border-bottom: 2px solid transparent; color: var(--text-2); padding: 8px 12px; font: inherit; cursor: pointer; }
nav button.active { color: var(--text); border-bottom-color: var(--accent); font-weight: 600; }
main { max-width: 1200px; margin: 0 auto; padding: 16px; }
section { display: none; } section.active { display: block; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 16px; overflow-x: auto; }
.card h2 { margin: 0 0 12px; font-size: 16px; }
.card h3 { margin: 16px 0 8px; font-size: 14px; color: var(--text-2); }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.tile .v { font-size: 22px; font-weight: 650; font-variant-numeric: tabular-nums; }
.tile .k { color: var(--text-2); font-size: 12px; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--text-2); font-weight: 600; font-size: 12px; position: sticky; top: 0; background: var(--surface); cursor: pointer; white-space: nowrap; }
td.n, th.n { text-align: right; }
tr:hover td { background: var(--surface-2); }
.muted { color: var(--muted); }
.pill { display: inline-block; padding: 0 8px; border-radius: 999px; font-size: 12px; background: var(--surface-2); color: var(--text-2); border: 1px solid var(--border); white-space: nowrap; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
input, select { font: inherit; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--border); background: var(--surface); color: var(--text); }
.warn { border-left: 4px solid var(--warn); padding: 8px 12px; background: var(--surface-2); margin: 6px 0; border-radius: 4px; }
.warn::before { content: "⚠ "; color: var(--warn); }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
a.plink { color: var(--accent); cursor: pointer; text-decoration: none; }
a.plink:hover { text-decoration: underline; }
svg text { fill: var(--text-2); font-size: 11px; }
svg .grid { stroke: var(--border); stroke-width: 1; }
svg .bar { fill: var(--series-1); }
svg .bar:hover, svg .hit:hover + .bar { opacity: .8; }
#tooltip { position: fixed; pointer-events: none; background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.15); display: none; z-index: 10; max-width: 320px; }
.legend { display: flex; gap: 4px; align-items: center; font-size: 12px; color: var(--text-2); margin-top: 8px; }
.legend span.sw { width: 18px; height: 10px; border-radius: 2px; display: inline-block; }
.theme-toggle { float: right; background: var(--surface); border: 1px solid var(--border); color: var(--text-2); border-radius: 6px; padding: 4px 10px; cursor: pointer; font: inherit; }
details summary { cursor: pointer; color: var(--text-2); }
code { background: var(--surface-2); padding: 1px 5px; border-radius: 4px; font-size: 12px; }
@media (max-width: 600px) { .tile .v { font-size: 18px; } th, td { padding: 4px 6px; } }
</style>
</head>
<body>
<header>
  <button class="theme-toggle" id="themeBtn" type="button">Theme</button>
  <h1>__TITLE__</h1>
  <div class="sub" id="subtitle"></div>
</header>
<nav id="nav"></nav>
<main>
  <section id="tab-overview"></section>
  <section id="tab-activity"></section>
  <section id="tab-players"></section>
  <section id="tab-player"></section>
  <section id="tab-events"></section>
  <section id="tab-replays"></section>
  <section id="tab-files"></section>
</main>
<div id="tooltip"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtN = n => (n ?? 0).toLocaleString();
const dt = ms => ms == null ? "—" : new Date(ms).toLocaleString();
const dIso = s => s ? new Date(s).toLocaleString() : "—";
const dur = sec => { sec = Math.round(sec || 0); const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60);
  return h ? `${fmtN(h)}h ${m}m` : (m ? `${m}m` : `${sec}s`); };
const pretty = id => String(id).replace(/^minecraft:/, "").replace(/^[a-z_]+\//, s => s.replace("/", " › ")).replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
const TYPE_LABEL = { join: "Join", leave: "Leave", chat: "Chat", server_chat: "Server msg", emote: "/me", death: "Death",
  advancement: "Advancement", command: "Command", server_start: "Server start", server_ready: "Server ready",
  server_stop: "Server stop", crash: "Crash", replay_marker: "Replay marker" };
const tip = $("#tooltip");
function showTip(ev, html) { tip.innerHTML = html; tip.style.display = "block";
  const x = Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8), y = Math.min(ev.clientY + 14, innerHeight - tip.offsetHeight - 8);
  tip.style.left = x + "px"; tip.style.top = y + "px"; }
function hideTip() { tip.style.display = "none"; }

// ---------- theme toggle ----------
$("#themeBtn").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = cur === "dark" ? "light" : "dark";
  try { localStorage.setItem("mcsm-theme", document.documentElement.dataset.theme); } catch (e) {}
};
try { const t = localStorage.getItem("mcsm-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}

// ---------- unit-aware stat formatting ----------
function fmtStat(key, v) {
  if (/_one_cm$/.test(key)) return v >= 100000 ? (v / 100000).toFixed(2) + " km" : (v / 100).toFixed(0) + " m";
  if (/(^|_)time($|_)|play_one_minute|time_since/.test(key)) return dur(v / 20);
  if (/^damage_/.test(key)) return fmtN(Math.round(v / 10)) + " ♥/2";
  return fmtN(v);
}

// ---------- derived data ----------
const EV = D.events.map(r => ({ t: r[0], type: r[1], player: r[2], text: r[3], source: r[4], line: r[5], data: r[6] || {} }));
const SESS = D.sessions.map(r => ({ player: r[0], start: r[1], end: r[2], reason: r[3] }));
const tStart = EV.length ? EV[0].t : null, tEnd = EV.length ? EV[EV.length - 1].t : null;
const playerByName = Object.fromEntries(D.players.map(p => [p.name.toLowerCase(), p]));
function plink(name) { return name ? `<a class="plink" data-player="${esc(name)}">${esc(name)}</a>` : ""; }
document.addEventListener("click", e => { const a = e.target.closest("a.plink"); if (a) { showPlayer(a.dataset.player); } });

// ---------- tabs ----------
const TABS = [["overview", "Overview"], ["activity", "Activity"], ["players", "Players"], ["events", "Events"], ["replays", "Replays"], ["files", "Files"]];
const rendered = {};
function openTab(id) {
  document.querySelectorAll("main > section").forEach(s => s.classList.toggle("active", s.id === "tab-" + id));
  document.querySelectorAll("nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === id || (id === "player" && b.dataset.tab === "players")));
  if (!rendered[id] && RENDER[id]) { RENDER[id](); rendered[id] = true; }
  hideTip();
}
$("#nav").innerHTML = TABS.map(([id, l]) => `<button type="button" data-tab="${id}">${l}</button>`).join("");
$("#nav").onclick = e => { const b = e.target.closest("button"); if (b) { openTab(b.dataset.tab); history.replaceState(null, "", "#" + b.dataset.tab); } };

$("#subtitle").textContent = (tStart ? `Logs cover ${new Date(tStart).toLocaleDateString()} – ${new Date(tEnd).toLocaleDateString()} · ` : "") +
  `${D.players.length} players · ${fmtN(EV.length)} events · report generated ${dIso(D.generated_at)}`;

// ---------- generic sortable table ----------
function table(cols, rows, opts = {}) {
  // cols: [{key, label, num, fmt}] ; rows: objects
  const id = "t" + Math.random().toString(36).slice(2);
  let sortKey = opts.sort || null, sortDir = opts.dir || -1;
  const wrap = document.createElement("div");
  function draw() {
    let rs = rows.slice();
    if (sortKey) rs.sort((a, b) => { const x = a[sortKey], y = b[sortKey];
      return (x == null) - (y == null) || (x < y ? -1 : x > y ? 1 : 0) * sortDir; });
    wrap.innerHTML = `<table id="${id}"><thead><tr>${cols.map(c => `<th class="${c.num ? "n" : ""}" data-k="${c.key}">${esc(c.label)}${sortKey === c.key ? (sortDir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead><tbody>` +
      rs.map(r => `<tr>${cols.map(c => `<td class="${c.num ? "n" : ""}">${c.fmt ? c.fmt(r[c.key], r) : esc(r[c.key])}</td>`).join("")}</tr>`).join("") + `</tbody></table>`;
    wrap.querySelectorAll("th").forEach(th => th.onclick = () => { const k = th.dataset.k; sortDir = sortKey === k ? -sortDir : -1; sortKey = k; draw(); });
  }
  draw();
  return wrap;
}

// ---------- charts (hand-rolled SVG) ----------
function barChart(el, items, { valueFmt = fmtN, height = 220, labelEvery = 1, tipFmt } = {}) {
  // items: [{label, value, tip}]
  const W = Math.max(320, el.clientWidth || 800), H = height, L = 48, R = 8, T = 10, B = 28;
  const max = Math.max(1, ...items.map(d => d.value));
  const nice = niceMax(max), ticks = 4;
  const bw = (W - L - R) / Math.max(1, items.length), gap = Math.min(2, bw * 0.2);
  let s = `<svg width="100%" viewBox="0 0 ${W} ${H}" role="img">`;
  for (let i = 0; i <= ticks; i++) { const v = nice * i / ticks, y = T + (H - T - B) * (1 - i / ticks);
    s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}"/><text x="${L - 6}" y="${y + 4}" text-anchor="end">${esc(valueFmt(v))}</text>`; }
  items.forEach((d, i) => {
    const h = (H - T - B) * d.value / nice, x = L + i * bw + gap / 2, w = Math.max(1, bw - gap), y = H - B - h;
    const r = Math.min(4, w / 2, h);
    if (h > 0) s += `<path class="bar" d="M${x},${H - B} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${H - B} Z"/>`;
    s += `<rect class="hit" data-i="${i}" x="${L + i * bw}" y="${T}" width="${bw}" height="${H - T - B}" fill="transparent"/>`;
    if (i % labelEvery === 0) s += `<text x="${x + w / 2}" y="${H - B + 16}" text-anchor="middle">${esc(d.label)}</text>`;
  });
  s += `</svg>`;
  el.innerHTML = s;
  el.querySelectorAll("rect.hit").forEach(r => {
    r.onmousemove = e => { const d = items[+r.dataset.i]; showTip(e, tipFmt ? tipFmt(d) : `<b>${esc(d.label)}</b><br>${esc(valueFmt(d.value))}`); };
    r.onmouseleave = hideTip;
  });
}
function niceMax(v) { const p = Math.pow(10, Math.floor(Math.log10(v))); for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p; return 10 * p; }

function hbarChart(el, items, valueFmt) {
  const max = Math.max(1, ...items.map(d => d.value));
  el.innerHTML = `<table>${items.map((d, i) => `<tr data-i="${i}"><td style="width:28%">${d.html || esc(d.label)}</td>` +
    `<td><svg width="100%" height="14" viewBox="0 0 100 14" preserveAspectRatio="none"><rect class="bar" x="0" y="2" rx="2" height="10" width="${Math.max(0.5, 100 * d.value / max)}"/></svg></td>` +
    `<td class="n" style="width:22%">${esc(valueFmt(d.value))}</td></tr>`).join("")}</table>`;
}

// ---------- OVERVIEW ----------
const RENDER = {};
RENDER.overview = () => {
  const el = $("#tab-overview"), w = D.world || {}, sp = D.server_properties || {};
  const deaths = EV.filter(e => e.type === "death").length, chats = EV.filter(e => e.type === "chat").length;
  const advs = EV.filter(e => e.type === "advancement").length;
  const totalPlay = D.players.reduce((a, p) => a + p.play_ticks / 20, 0);
  const starts = EV.filter(e => e.type === "server_start").length;
  const tiles = [
    ["Players", D.players.length], ["Total play time (stats)", dur(totalPlay)], ["Sessions logged", fmtN(SESS.length)],
    ["Chat messages", fmtN(chats)], ["Deaths (logged)", fmtN(deaths)], ["Advancements (logged)", fmtN(advs)],
    ["Server starts", fmtN(starts)], ["Replays", D.replays.length],
    ["World age (game time)", w.game_time_ticks != null ? dur(w.game_time_ticks / 20) : "—"],
    ["In-game days", w.in_game_days != null ? fmtN(w.in_game_days) : "—"],
    ["World size", w.size_bytes ? (w.size_bytes / 1048576).toFixed(0) + " MB" : "—"],
  ];
  const dims = Object.entries(w.dimensions || {});
  el.innerHTML = (D.warnings.length ? `<div class="card"><h2>Things to check</h2>${D.warnings.map(x => `<div class="warn">${esc(x)}</div>`).join("")}</div>` : "") +
    `<div class="tiles">${tiles.map(([k, v]) => `<div class="tile"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join("")}</div>` +
    `<div class="grid2"><div class="card"><h2>World</h2><table>` +
    [["Name", w.level_name], ["Folder", w.folder], ["Version", w.version_name], ["Seed", w.seed], ["Difficulty", w.difficulty],
     ["Default game mode", w.game_type], ["Hardcore", w.hardcore ? "yes" : "no"], ["Ender dragon killed", w.dragon_killed == null ? "—" : (w.dragon_killed ? "yes" : "no")],
     ["Spawn", (w.spawn || []).join(", ")], ["Server brand", (w.brand || []).join(", ")], ["Time zone", D.timezone_info]]
      .map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td>${esc(v ?? "—")}</td></tr>`).join("") + `</table>` +
    (dims.length ? `<h3>Explored area by dimension</h3><table><tr><th>Dimension</th><th class="n">Region files</th><th class="n">≈ km²</th><th class="n">Size</th></tr>` +
      dims.map(([d, v]) => `<tr><td>${esc(pretty(d))}</td><td class="n">${fmtN(v.region_files)}</td><td class="n">${v.approx_area_km2}</td><td class="n">${(v.bytes / 1048576).toFixed(1)} MB</td></tr>`).join("") + `</table>` : "") +
    `</div><div class="card"><h2>server.properties</h2><table>${Object.entries(sp).sort().map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table></div></div>` +
    (D.mods.length ? `<div class="card"><h2>Mods & plugins (${D.mods.length})</h2><div id="modsTable"></div></div>` : "") +
    (w.game_rules && Object.keys(w.game_rules).length ? `<div class="card"><details><summary>Game rules (${Object.keys(w.game_rules).length})</summary><table>${Object.entries(w.game_rules).sort().map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table></details></div>` : "");
  if (D.mods.length) $("#modsTable").appendChild(table([{ key: "name", label: "Name" }, { key: "version", label: "Version" }, { key: "kind", label: "Kind" }, { key: "loader", label: "Loader" }, { key: "file", label: "File" }], D.mods, { sort: "name", dir: 1 }));
};

// ---------- ACTIVITY ----------
function dayKey(ms) { const d = new Date(ms); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function splitByHour(s, e, cb) { // calls cb(hourStartDate, seconds) for each local hour the interval touches
  let t = s; while (t < e) { const d = new Date(t); d.setMinutes(0, 0, 0); const next = d.getTime() + 3600000; const end = Math.min(e, next); cb(new Date(t), (end - t) / 1000); t = end; } }
RENDER.activity = () => {
  const el = $("#tab-activity");
  el.innerHTML = `<div class="card"><h2>Player-hours online per day</h2><div class="filters"><label>Player <select id="actPlayer"><option value="">Everyone</option>${D.players.map(p => `<option>${esc(p.name)}</option>`).join("")}</select></label><span class="muted">Summed over all online players, from join/leave lines in the logs.</span></div><div id="dailyChart"></div></div>` +
    `<div class="card"><h2>When do people play?</h2><p class="muted" style="margin-top:-8px">Player-hours by weekday and hour of day (your local time).</p><div id="heat"></div></div>` +
    `<div class="grid2"><div class="card"><h2>Play time per player <span class="muted" style="font-weight:400">(from stats files)</span></h2><div id="ptChart"></div></div>` +
    `<div class="card"><h2>Deaths per player <span class="muted" style="font-weight:400">(from stats files)</span></h2><div id="deathChart"></div></div></div>`;
  const draw = () => {
    const who = $("#actPlayer").value.toLowerCase();
    const per = {}, heat = Array.from({ length: 7 }, () => new Array(24).fill(0));
    for (const s of SESS) { if (!s.end || (who && s.player.toLowerCase() !== who)) continue;
      splitByHour(s.start, s.end, (d, sec) => { const k = dayKey(d.getTime()); per[k] = (per[k] || 0) + sec; heat[(d.getDay() + 6) % 7][d.getHours()] += sec; }); }
    const keys = Object.keys(per).sort();
    const items = [];
    if (keys.length) { for (let d = new Date(keys[0] + "T00:00"); dayKey(d.getTime()) <= keys[keys.length - 1]; d.setDate(d.getDate() + 1)) {
      const k = dayKey(d.getTime()); items.push({ label: k.slice(5), full: k, value: (per[k] || 0) / 3600 }); } }
    if (!items.length) { $("#dailyChart").innerHTML = `<p class="muted">No sessions found in the logs.</p>`; }
    else barChart($("#dailyChart"), items, { valueFmt: v => v.toFixed(v < 10 && v % 1 ? 1 : 0) + "h", labelEvery: Math.ceil(items.length / 12),
      tipFmt: d => `<b>${esc(new Date(d.full + "T00:00").toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" }))}</b><br>${dur(d.value * 3600)} online` });
    const max = Math.max(1, ...heat.flat()), days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    const cell = v => v <= 0 ? "var(--seq-0)" : `var(--seq-${Math.min(7, 1 + Math.floor(6.999 * v / max))})`;
    $("#heat").innerHTML = `<table style="table-layout:fixed;min-width:560px"><tr><th style="width:44px"></th>${Array.from({ length: 24 }, (_, h) => `<th style="text-align:center;padding:2px;cursor:default">${h % 3 === 0 ? h : ""}</th>`).join("")}</tr>` +
      heat.map((row, d) => `<tr><td class="muted" style="padding:2px 4px">${days[d]}</td>${row.map((v, h) => `<td class="hc" data-d="${d}" data-h="${h}" style="padding:0;border:2px solid var(--surface)"><div style="height:22px;border-radius:3px;background:${cell(v)}"></div></td>`).join("")}</tr>`).join("") + `</table>` +
      `<div class="legend">Less ${[0, 1, 2, 3, 4, 5, 6, 7].map(i => `<span class="sw" style="background:var(--seq-${i})"></span>`).join("")} More</div>`;
    $("#heat").querySelectorAll("td.hc").forEach(td => { td.onmousemove = e => showTip(e, `<b>${days[td.dataset.d]} ${td.dataset.h}:00–${+td.dataset.h + 1}:00</b><br>${dur(heat[td.dataset.d][td.dataset.h])} player-time`); td.onmouseleave = hideTip; });
  };
  $("#actPlayer").onchange = draw; draw();
  const pt = D.players.filter(p => p.play_ticks > 0).map(p => ({ label: p.name, html: plink(p.name), value: p.play_ticks / 20 }));
  pt.length ? hbarChart($("#ptChart"), pt, dur) : $("#ptChart").innerHTML = `<p class="muted">No stats files found.</p>`;
  const dd = D.players.map(p => ({ label: p.name, html: plink(p.name), value: (p.stats.custom || {}).deaths || 0 })).filter(d => d.value).sort((a, b) => b.value - a.value);
  dd.length ? hbarChart($("#deathChart"), dd, fmtN) : $("#deathChart").innerHTML = `<p class="muted">Nobody has died. Impressive.</p>`;
};

// ---------- PLAYERS ----------
RENDER.players = () => {
  const el = $("#tab-players");
  const rows = D.players.map(p => { const c = p.stats.custom || {}; const sum = cat => Object.values(p.stats[cat] || {}).reduce((a, b) => a + b, 0);
    return { name: p.name, play: p.play_ticks / 20, logged: p.session_seconds, sessions: p.sessions, deaths: c.deaths || 0,
      mob_kills: c.mob_kills || 0, player_kills: c.player_kills || 0, mined: sum("mined"), crafted: sum("crafted"),
      walked: (c.walk_one_cm || 0) + (c.sprint_one_cm || 0), chat: p.chat, advs: p.advancements.filter(a => a.done && !a.id.startsWith("recipes")).length,
      last: p.last_seen ? Date.parse(p.last_seen) : null, flags: [p.is_op && "op", p.is_banned && "banned"].filter(Boolean).join(" ") }; });
  el.innerHTML = `<div class="card"><h2>Leaderboard</h2><p class="muted" style="margin-top:-8px">Click a column to sort, a name for details. "Play time" comes from the game's own stats; "Logged" is the sum of sessions found in the logs (only as complete as your logs).</p><div id="lb"></div></div>`;
  $("#lb").appendChild(table([
    { key: "name", label: "Player", fmt: (v, r) => plink(v) + (r.flags ? ` <span class="pill">${esc(r.flags)}</span>` : "") },
    { key: "play", label: "Play time", num: 1, fmt: dur }, { key: "logged", label: "Logged", num: 1, fmt: dur },
    { key: "sessions", label: "Sessions", num: 1, fmt: fmtN }, { key: "deaths", label: "Deaths", num: 1, fmt: fmtN },
    { key: "mob_kills", label: "Mob kills", num: 1, fmt: fmtN }, { key: "player_kills", label: "PvP kills", num: 1, fmt: fmtN },
    { key: "mined", label: "Blocks mined", num: 1, fmt: fmtN }, { key: "crafted", label: "Crafted", num: 1, fmt: fmtN },
    { key: "walked", label: "Walk+sprint", num: 1, fmt: v => fmtStat("walk_one_cm", v) }, { key: "advs", label: "Advancements", num: 1, fmt: fmtN },
    { key: "chat", label: "Chat msgs", num: 1, fmt: fmtN }, { key: "last", label: "Last seen", fmt: dt },
  ], rows, { sort: "play" }));
};

function showPlayer(name) {
  const p = playerByName[String(name).toLowerCase()];
  if (!p) return;
  rendered.player = true; openTab("player");
  const el = $("#tab-player"), c = p.stats.custom || {}, pd = p.playerdata || {};
  const tiles = [["Play time", dur(p.play_ticks / 20)], ["Sessions (logs)", fmtN(p.sessions)], ["Deaths", fmtN(c.deaths || 0)],
    ["Mob kills", fmtN(c.mob_kills || 0)], ["Jumps", fmtN(c.jump || 0)], ["Walked", fmtStat("walk_one_cm", c.walk_one_cm || 0)],
    ["Chat messages", fmtN(p.chat)], ["First seen", p.first_seen ? new Date(p.first_seen).toLocaleDateString() : "—"], ["Last seen", p.last_seen ? new Date(p.last_seen).toLocaleDateString() : "—"]];
  const cats = Object.keys(p.stats).sort((a, b) => (a === "custom" ? -1 : b === "custom" ? 1 : a.localeCompare(b)));
  const evs = EV.filter(e => e.player && e.player.toLowerCase() === p.name.toLowerCase() && ["death", "advancement", "join"].includes(e.type));
  el.innerHTML = `<div class="card"><a class="plink" onclick="openTab('players')">← All players</a><h2 style="margin-top:8px">${esc(p.name)} ${p.is_op ? '<span class="pill">op</span>' : ""} ${p.is_banned ? '<span class="pill">banned</span>' : ""}</h2><div class="muted">${esc(p.uuid || "")}</div></div>` +
    `<div class="tiles">${tiles.map(([k, v]) => `<div class="tile"><div class="v" style="font-size:18px">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join("")}</div>` +
    `<div class="grid2">` +
    (Object.keys(pd).length ? `<div class="card"><h2>Last saved state</h2><table>` + [["Position", (pd.position || []).join(", ")], ["Dimension", pretty(pd.dimension || "")], ["Health", pd.health], ["Food", pd.food], ["XP level", pd.xp_level], ["Game mode", pd.game_mode], ["Bed/respawn", pd.spawn ? pd.spawn.join(", ") : "—"], ["Last death", pd.last_death ? `${pretty(pd.last_death.dimension)} ${(pd.last_death.pos || []).join(", ")}` : "—"]].map(([k, v]) => `<tr><td class="muted">${k}</td><td>${esc(v ?? "—")}</td></tr>`).join("") + `</table>` +
      `<h3>Inventory</h3>${Object.entries(pd.inventory || {}).map(([k, v]) => `<span class="pill">${esc(pretty(k))} ×${v}</span>`).join(" ") || '<span class="muted">empty</span>'}` +
      `<h3>Ender chest</h3>${Object.entries(pd.ender_chest || {}).map(([k, v]) => `<span class="pill">${esc(pretty(k))} ×${v}</span>`).join(" ") || '<span class="muted">empty</span>'}</div>` : "") +
    `<div class="card"><h2>Advancements (${p.advancements.filter(a => a.done).length})</h2><div id="pAdv" style="max-height:420px;overflow:auto"></div></div></div>` +
    `<div class="card"><h2>Statistics</h2><div class="filters"><select id="pCat">${cats.map(k => `<option value="${esc(k)}">${esc(pretty(k))} (${Object.keys(p.stats[k]).length})</option>`).join("")}</select><input id="pStatQ" placeholder="Search…"></div><div id="pStats" style="max-height:520px;overflow:auto"></div></div>` +
    `<div class="card"><h2>Key moments</h2><div id="pEv" style="max-height:420px;overflow:auto"></div></div>`;
  $("#pAdv").appendChild(table([{ key: "id", label: "Advancement", fmt: v => esc(pretty(v)) }, { key: "completed", label: "Completed", fmt: dIso }],
    p.advancements.filter(a => a.done), { sort: "completed", dir: 1 }));
  const drawStats = () => { const cat = $("#pCat").value, q = $("#pStatQ").value.toLowerCase();
    const rows = Object.entries(p.stats[cat] || {}).filter(([k]) => !q || k.includes(q.replace(/ /g, "_"))).map(([k, v]) => ({ key: k, value: v }));
    $("#pStats").innerHTML = ""; $("#pStats").appendChild(table([{ key: "key", label: "Stat", fmt: v => esc(pretty(v)) }, { key: "value", label: "Value", num: 1, fmt: (v, r) => fmtStat(r.key, v) }], rows, { sort: "value" })); };
  $("#pCat").onchange = drawStats; $("#pStatQ").oninput = drawStats; if (cats.length) drawStats();
  $("#pEv").appendChild(table([{ key: "t", label: "When", fmt: dt }, { key: "type", label: "Type", fmt: v => `<span class="pill">${esc(TYPE_LABEL[v] || v)}</span>` }, { key: "text", label: "What" }], evs, { sort: "t", dir: 1 }));
  scrollTo(0, 0);
}
window.openTab = openTab;

// ---------- EVENTS ----------
RENDER.events = () => {
  const el = $("#tab-events");
  const types = [...new Set(EV.map(e => e.type))];
  el.innerHTML = `<div class="card"><h2>All events</h2><div class="filters">` +
    `<select id="evType"><option value="">All types</option>${types.map(t => `<option value="${t}">${esc(TYPE_LABEL[t] || t)} (${fmtN(EV.filter(e => e.type === t).length)})</option>`).join("")}</select>` +
    `<select id="evPlayer"><option value="">All players</option>${D.players.map(p => `<option>${esc(p.name)}</option>`).join("")}</select>` +
    `<input id="evQ" placeholder="Search text…"> <label>From <input type="date" id="evFrom"></label> <label>To <input type="date" id="evTo"></label> <span id="evCount" class="muted"></span></div>` +
    `<div id="evTable"></div><div style="text-align:center;margin-top:8px"><button id="evMore" type="button" class="theme-toggle" style="float:none">Show more</button></div></div>`;
  let limit = 300;
  const draw = () => {
    const t = $("#evType").value, pl = $("#evPlayer").value.toLowerCase(), q = $("#evQ").value.toLowerCase();
    const from = $("#evFrom").value ? new Date($("#evFrom").value + "T00:00").getTime() : -Infinity;
    const to = $("#evTo").value ? new Date($("#evTo").value + "T00:00").getTime() + 86400000 : Infinity;
    const rows = EV.filter(e => (!t || e.type === t) && (!pl || (e.player || "").toLowerCase() === pl) && e.t >= from && e.t < to &&
      (!q || (e.text || "").toLowerCase().includes(q) || (e.player || "").toLowerCase().includes(q)));
    $("#evCount").textContent = `${fmtN(rows.length)} matching`;
    const shown = rows.slice(-limit).reverse();
    $("#evTable").innerHTML = `<table><thead><tr><th>When</th><th>Type</th><th>Player</th><th>What</th><th>Source</th></tr></thead><tbody>` +
      shown.map(e => `<tr><td style="white-space:nowrap">${dt(e.t)}</td><td><span class="pill">${esc(TYPE_LABEL[e.type] || e.type)}</span></td><td>${plink(e.player)}</td><td>${esc(e.text)}</td><td class="muted" style="white-space:nowrap">${esc(e.source)}${e.line ? ":" + e.line : ""}</td></tr>`).join("") + `</tbody></table>`;
    $("#evMore").style.display = rows.length > limit ? "" : "none";
  };
  ["evType", "evPlayer", "evFrom", "evTo"].forEach(id => $("#" + id).onchange = () => { limit = 300; draw(); });
  $("#evQ").oninput = () => { limit = 300; draw(); };
  $("#evMore").onclick = () => { limit += 500; draw(); };
  draw();
};

// ---------- REPLAYS ----------
RENDER.replays = () => {
  const el = $("#tab-replays");
  if (!D.replays.length) { el.innerHTML = `<div class="card"><h2>Replays</h2><p class="muted">No replays were found (ReplayMod <code>.mcpr</code> or Flashback <code>.zip</code>). ServerReplay saves them under <code>recordings/</code>; include that folder in your download, or pass <code>--replay FILE</code>.</p></div>`; return; }
  const rows = D.replays.map(r => { const s = Date.parse(r.start), e = Date.parse(r.end);
    const inside = EV.filter(x => x.t >= s && x.t <= e);
    return { ...r, s, dur: r.duration_ms / 1000, events: inside.length, deaths: inside.filter(x => x.type === "death").length,
      chat: inside.filter(x => x.type === "chat").length, advs: inside.filter(x => x.type === "advancement").length }; });
  el.innerHTML = `<div class="card"><h2>Replays (${rows.length})</h2><p class="muted" style="margin-top:-8px">Events counted are those from the logs that happened while the recording ran. To get markers/subtitles for a video, run <code>mcsm sync SERVER --replay PATH</code> (see docs/VIDEO_SYNC.md).</p><div id="rpTable"></div></div>`;
  $("#rpTable").appendChild(table([
    { key: "path", label: "File", fmt: v => `<code>${esc(v)}</code>` },
    { key: "format", label: "Format", fmt: (v, r) => `<span class="pill">${v === "flashback" ? "Flashback" : "ReplayMod"}</span>` + (r.unfinished ? ' <span class="pill">unfinished</span>' : "") + (["metadata", "real-time-clock"].includes(r.start_source) ? "" : ` <span class="pill" title="Start time is estimated">start: ${esc(r.start_source)}</span>`) },
    { key: "recorded_player", label: "Recorded player", fmt: v => v ? plink(v) : '<span class="muted">—</span>' },
    { key: "s", label: "Start", fmt: dt }, { key: "dur", label: "Length", num: 1, fmt: dur },
    { key: "events", label: "Events", num: 1, fmt: fmtN }, { key: "chat", label: "Chat", num: 1, fmt: fmtN }, { key: "deaths", label: "Deaths", num: 1, fmt: fmtN },
    { key: "advs", label: "Advs", num: 1, fmt: fmtN }, { key: "players", label: "Players in recording", fmt: v => (v || []).map(plink).join(", ") },
    { key: "size", label: "Size", num: 1, fmt: v => (v / 1048576).toFixed(1) + " MB" },
  ], rows, { sort: "s", dir: 1 }));
};

// ---------- FILES ----------
RENDER.files = () => {
  const el = $("#tab-files");
  el.innerHTML = `<div class="card"><h2>Log files read (${D.log_files.length})</h2><p class="muted" style="margin-top:-8px">Log lines only contain a time of day; the date comes from the file name (or, for latest.log, its modification time). Time zone: ${esc(D.timezone_info)}.</p><div id="lfTable"></div></div>`;
  $("#lfTable").appendChild(table([{ key: "path", label: "File" }, { key: "date", label: "Date of last line" }, { key: "date_source", label: "Dated by" },
    { key: "lines", label: "Lines", num: 1, fmt: fmtN }, { key: "first_time", label: "First line", fmt: dIso }, { key: "last_time", label: "Last line", fmt: dIso }], D.log_files, { sort: "path", dir: 1 }));
};

const start = (location.hash || "#overview").slice(1);
openTab(RENDER[start] ? start : "overview");
</script>
</body>
</html>
"""
