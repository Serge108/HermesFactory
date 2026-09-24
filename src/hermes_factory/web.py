from __future__ import annotations

import json
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from hermes_factory.health import HealthFinding, audit_tasks
from hermes_factory.hermes_cli import HermesClient
from hermes_factory.metrics import BoardMetrics, compute_metrics

DashboardClient = HermesClient


def _open_work_by_seat(tasks: list[dict[str, Any]]) -> dict[str, int]:
    seats: dict[str, int] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("status") in {"ready", "running", "blocked"}:
            seat = task.get("assignee") or "unassigned"
            seats[seat] = seats.get(seat, 0) + 1
    return seats


def build_snapshot(
    rig: Any,
    client: DashboardClient,
    *,
    board_slug: str | None = None,
) -> dict[str, Any]:
    slug = board_slug or rig.board.slug
    board = ["kanban", "--board", slug]
    stats = client.run_json(board + ["stats", "--json"])
    tasks = client.run_json(board + ["list", "--json"])
    diagnostics = client.run_json(board + ["diagnostics", "--json"])
    if not isinstance(tasks, list):
        tasks = []
    if not isinstance(diagnostics, list):
        diagnostics = []

    runs_by_task: dict[str, list] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("status") == "done":
            runs_by_task[task.get("id")] = client.run_json(
                board + ["runs", task.get("id"), "--json"]
            )

    findings = audit_tasks(tasks, runs_by_task, diagnostics)
    metrics = compute_metrics(tasks, runs_by_task, stats)

    return {
        "board": slug,
        "board_name": rig.board.name,
        "project": rig.project.slug,
        "generated_at": int(time.time()),
        "stats": stats if isinstance(stats, dict) else {},
        "tasks": tasks,
        "runs_by_task": runs_by_task,
        "findings": [asdict(finding) for finding in findings],
        "metrics": asdict(metrics),
        "seats": _open_work_by_seat(tasks),
    }


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Factory</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; background: #0e1116; color: #d7dce4;
  font: 14px/1.5 "Segoe UI", system-ui, sans-serif;
}
header {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 20px; background: #161b23;
  border-bottom: 1px solid #262d3a; position: sticky; top: 0; z-index: 5;
}
header h1 { font-size: 16px; margin: 0; font-weight: 600; }
header .board { color: #8b93a5; font-size: 13px; }
.dot {
  width: 9px; height: 9px; border-radius: 50%;
  background: #37d67a; display: inline-block;
  animation: pulse 2s infinite;
}
@keyframes pulse { 50% { opacity: 0.35; } }
#last-updated { margin-left: auto; color: #8b93a5; font-size: 12px; }
main { padding: 18px 20px 40px; max-width: 1500px; margin: 0 auto; }
h2 {
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
  color: #8b93a5; margin: 22px 0 10px; font-weight: 600;
}
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.kpi {
  background: #161b23; border: 1px solid #262d3a; border-radius: 8px;
  padding: 12px 14px;
}
.kpi .label { color: #8b93a5; font-size: 12px; }
.kpi .value { font-size: 22px; font-weight: 600; margin-top: 2px; }
.kpi .value.bad { color: #ff7b72; }
.kpi .value.warn { color: #d29922; }
.columns {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 12px; align-items: start;
}
.column { background: #12161d; border: 1px solid #262d3a; border-radius: 8px; min-height: 90px; }
.column h3 {
  margin: 0; padding: 10px 12px; font-size: 12px; color: #8b93a5;
  text-transform: uppercase; letter-spacing: 0.05em;
  border-bottom: 1px solid #262d3a;
  display: flex; justify-content: space-between;
}
.column h3 .count { color: #d7dce4; font-weight: 600; }
.column .cards { padding: 8px; display: flex; flex-direction: column; gap: 8px; }
.card {
  background: #161b23; border: 1px solid #262d3a; border-radius: 6px;
  padding: 10px 12px;
}
.card .title { font-weight: 600; font-size: 13px; margin-bottom: 6px; }
.card .meta { display: flex; flex-wrap: wrap; gap: 6px; font-size: 11px; color: #8b93a5; }
.badge {
  border-radius: 4px; padding: 1px 7px; font-size: 11px; font-weight: 600;
  background: #262d3a; color: #d7dce4;
}
.badge.proof { background: #1a3a2a; color: #37d67a; }
.badge.noproof { background: #3a1a1a; color: #ff7b72; }
.badge.attempts { background: #3a2f1a; color: #d29922; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
table { width: 100%; border-collapse: collapse; background: #161b23;
  border: 1px solid #262d3a; border-radius: 8px; overflow: hidden; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #262d3a; font-size: 13px; }
th { color: #8b93a5; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
tr:last-child td { border-bottom: none; }
.sev { font-weight: 700; font-size: 11px; text-transform: uppercase; }
.sev.critical { color: #ff7b72; } .sev.error { color: #d29922; }
.sev.warning { color: #d29922; }
.empty { color: #5c6470; font-size: 13px; padding: 10px 2px; }
.seat-row { display: flex; justify-content: space-between; padding: 6px 0;
  border-bottom: 1px solid #262d3a; }
.seat-row:last-child { border-bottom: none; }
.seat-row .n { font-weight: 600; }
#load-error { display: none; background: #3a1a1a; color: #ff7b72;
  padding: 10px 20px; font-size: 13px; }
</style>
</head>
<body>
<header>
  <span class="dot" id="live-dot"></span>
  <h1>Hermes Factory</h1>
  <span class="board" id="board-label">loading&hellip;</span>
  <span id="last-updated"></span>
</header>
<div id="load-error"></div>
<main id="main">
  <h2>Throughput &amp; quality</h2>
  <div class="kpis" id="kpis"></div>

  <h2>Pipeline</h2>
  <div class="columns" id="columns"></div>

  <div class="grid2">
    <section>
      <h2>Seats &mdash; open work</h2>
      <div id="seats" class="kpi"></div>
    </section>
    <section>
      <h2>Quality signals</h2>
      <div id="signals" class="kpi"></div>
    </section>
  </div>

  <h2>Health findings</h2>
  <div id="findings"></div>
</main>
<script>
const REFRESH_SECONDS = __REFRESH_SECONDS__;
const STATUS_ORDER = ["todo", "triage", "ready", "running", "review", "blocked", "done", "cancelled"];
const STATUS_COLORS = {};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}[c]));
}

function fmtAge(seconds) {
  if (seconds == null) return "-";
  const d = Math.floor(seconds / 86400), h = Math.floor(seconds % 86400 / 3600),
        m = Math.floor(seconds % 3600 / 60);
  if (d) return d + "d " + h + "h";
  if (h) return h + "h " + m + "m";
  return m + "m";
}

function fmtLead(minutes) {
  if (minutes == null) return "-";
  if (minutes < 60) return minutes + " min";
  return (minutes / 60).toFixed(1) + " h";
}

function render(payload) {
  const m = payload.metrics || {};
  const now = (payload.stats || {}).now || payload.generated_at;

  document.getElementById("board-label").textContent =
    payload.project + " / " + payload.board_name + " (" + payload.board + ")";

  const kpis = [
    ["done tasks", m.done ?? 0, null],
    ["throughput / day", m.throughput_per_day != null ? m.throughput_per_day : "-", null],
    ["proof rate", m.proof_rate != null ? (m.proof_rate * 100).toFixed(0) + "%" :
      "-", m.proof_rate != null && m.proof_rate < 1 ? "warn" : null],
    ["rework rate", m.rework_rate != null ? (m.rework_rate * 100).toFixed(0) + "%" :
      "-", m.rework_rate != null && m.rework_rate > 0 ? "bad" : null],
    ["median lead time", m.median_lead_time_minutes != null ?
      fmtLead(m.median_lead_time_minutes) : "-", null],
  ];
  document.getElementById("kpis").innerHTML = kpis.map(([label, value, cls]) =>
    '<div class="kpi"><div class="label">' + label +
    '</div><div class="value ' + (cls || "") + '">' + esc(value) + "</div></div>").join("");

  const runs = payload.runs_by_task || {};
  const byStatus = {};
  for (const task of (payload.tasks || [])) {
    (byStatus[task.status] = byStatus[task.status] || []).push(task);
  }
  const order = [...STATUS_ORDER.filter(s => byStatus[s]),
                 ...Object.keys(byStatus).filter(s => !STATUS_ORDER.includes(s))];
  document.getElementById("columns").innerHTML = order.map(status => {
    const tasks = byStatus[status];
    const cards = tasks.map(t => {
      const tRuns = runs[t.id] || [];
      const hasProof = t.result || tRuns.some(r =>
        r.outcome === "success" || r.status === "succeeded");
      const age = (t.status === "ready" || t.status === "blocked" ||
                   t.status === "running") && t.created_at != null
        ? fmtAge(now - t.created_at) : null;
      return '<div class="card"><div class="title">' + esc(t.title || t.id) +
        '</div><div class="meta">' +
        (t.assignee ? '<span class="badge">' + esc(t.assignee) + "</span>" : "") +
        (tRuns.length > 1 ? '<span class="badge attempts">' + tRuns.length +
          " runs</span>" : "") +
        (t.status === "done"
          ? (hasProof ? '<span class="badge proof">proof</span>'
                     : '<span class="badge noproof">no proof</span>')
          : "") +
        (age ? '<span class="badge">age ' + age + "</span>" : "") +
        "</div></div>";
    }).join("");
    return '<div class="column"><h3>' + esc(status) +
      '<span class="count">' + tasks.length + "</span></h3>" +
      '<div class="cards">' + (cards || '<div class="empty">empty</div>') +
      "</div></div>";
  }).join("");

  const seats = payload.seats || {};
  const seatRows = Object.keys(seats).sort((a, b) => seats[b] - seats[a]);
  document.getElementById("seats").innerHTML = seatRows.length
    ? seatRows.map(s => '<div class="seat-row"><span>' + esc(s) +
        '</span><span class="n">' + seats[s] + "</span></div>").join("")
    : '<div class="empty">no open work</div>';

  document.getElementById("signals").innerHTML =
    '<div class="seat-row"><span>total runs</span><span class="n">' +
      (m.total_runs ?? 0) + "</span></div>" +
    '<div class="seat-row"><span>changes requested</span><span class="n">' +
      (m.changes_requested ?? 0) + "</span></div>" +
    '<div class="seat-row"><span>failed runs</span><span class="n">' +
      (m.failed_runs ?? 0) + "</span></div>" +
    '<div class="seat-row"><span>done without proof</span><span class="n">' +
      (m.proof_rate != null ? (m.done ?? 0) - Math.round((m.proof_rate || 0) * (m.done || 0)) : 0) +
      "</span></div>";

  const findings = payload.findings || [];
  document.getElementById("findings").innerHTML = findings.length
    ? '<table><thead><tr><th>severity</th><th>code</th><th>message</th>' +
      "<th>task</th></tr></thead><tbody>" +
      findings.map(f =>
        "<tr><td><span class='sev " + esc(f.severity) + "'>" + esc(f.severity) +
        "</span></td><td>" + esc(f.code) + "</td><td>" + esc(f.message) +
        "</td><td>" + esc(f.task_id || "-") + "</td></tr>").join("") +
      "</tbody></table>"
    : '<div class="empty">no findings &mdash; board is clean</div>';

  document.getElementById("last-updated").textContent =
    "updated " + new Date(payload.generated_at * 1000).toLocaleTimeString();
  document.getElementById("live-dot").style.background = "#37d67a";
}

async function tick() {
  const err = document.getElementById("load-error");
  try {
    const res = await fetch("/data", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    render(await res.json());
    err.style.display = "none";
  } catch (e) {
    document.getElementById("live-dot").style.background = "#ff7b72";
    err.textContent = "live data unavailable: " + e.message +
      " (retrying in " + REFRESH_SECONDS + "s)";
    err.style.display = "block";
  }
}
tick();
setInterval(tick, REFRESH_SECONDS * 1000);
</script>
</body>
</html>
"""


def create_dashboard_handler(
    snapshot_builder: Callable[[str], dict[str, Any]],
    board_slug: str,
    refresh_seconds: int = 10,
):
    html = _DASHBOARD_HTML.replace("__REFRESH_SECONDS__", str(refresh_seconds))

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            elif self.path == "/data":
                snapshot = snapshot_builder(board_slug)
                body = json.dumps(snapshot, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path == "/health":
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    return _Handler


def create_server(
    snapshot_builder: Callable[[str], dict[str, Any]],
    *,
    host: str = "127.0.0.1",
    port: int = 8400,
    board_slug: str = "",
    refresh_seconds: int = 10,
) -> ThreadingHTTPServer:
    handler = create_dashboard_handler(
        snapshot_builder, board_slug, refresh_seconds
    )
    return ThreadingHTTPServer((host, port), handler)
