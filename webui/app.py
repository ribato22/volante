from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from webui.runner import stream_events

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Baton — live orchestration</title>
<style>
  :root { --bg:#0f1117; --panel:#171a23; --line:#262b38; --fg:#e6e8ee; --muted:#8b93a7;
          --accent:#8b5cf6; --ok:#34d399; --err:#f87171; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { padding:20px 24px; border-bottom:1px solid var(--line); }
  h1 { margin:0; font-size:20px; letter-spacing:.02em; }
  h1 span { color:var(--accent); }
  .sub { color:var(--muted); font-size:12px; }
  main { max-width:1000px; margin:0 auto; padding:20px 24px; }
  form { display:flex; gap:10px; margin-bottom:18px; }
  input { flex:1; background:var(--panel); border:1px solid var(--line); color:var(--fg);
          border-radius:8px; padding:10px 12px; font:inherit; }
  button { background:var(--accent); color:#fff; border:0; border-radius:8px;
           padding:10px 18px; font:inherit; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px;
           padding:12px 14px; margin-bottom:14px; }
  .panel h2 { margin:0 0 8px; font-size:12px; text-transform:uppercase; letter-spacing:.08em;
              color:var(--muted); }
  pre { margin:0; white-space:pre-wrap; word-break:break-word; }
  #workers { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .task h2 { color:var(--accent); }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; }
  .badge.ok { background:rgba(52,211,153,.15); color:var(--ok); }
  .badge.err { background:rgba(248,113,113,.15); color:var(--err); }
</style>
</head>
<body>
<header>
  <h1><span>&#9776;</span> Baton</h1>
  <div class="sub" style="margin-top:4px">Live multi-model orchestration &mdash;
    plan &rarr; parallel workers &rarr; synthesis, streamed via SSE.</div>
</header>
<main>
  <form id="f">
    <input id="goal" value="Write a short brief about concurrency in Python" autocomplete="off" />
    <button id="run" type="submit">Run</button>
  </form>

  <div class="panel"><h2>Plan</h2><pre id="plan"></pre></div>
  <div class="panel"><h2>Workers</h2><div id="workers"></div></div>
  <div class="panel"><h2>Synthesis</h2><pre id="synth"></pre></div>
  <div class="panel"><h2>Result</h2><div id="result"><span class="sub">idle</span></div></div>
</main>

<script>
// All dynamic values are inserted via textContent / DOM nodes only (never raw HTML
// strings) so model output and error text cannot inject markup.
const $ = (id) => document.getElementById(id);
let started = false;

function textNode(text, cls) {
  const s = document.createElement("span");
  s.textContent = text;
  if (cls) s.className = cls;
  return s;
}

function setResult(nodes) {
  const r = $("result");
  r.textContent = "";
  nodes.forEach((n) => r.appendChild(n));
}

function reset() {
  $("plan").textContent = "";
  $("synth").textContent = "";
  $("workers").textContent = "";
  setResult([textNode("running\\u2026", "sub")]);
  started = false;
}

function workerPane(task) {
  let pre = $("p-" + task);
  if (!pre) {
    const el = document.createElement("div");
    el.className = "task";
    el.id = "t-" + task;
    const h = document.createElement("h2");
    h.textContent = "[" + task + "]";
    pre = document.createElement("pre");
    pre.id = "p-" + task;
    el.appendChild(h);
    el.appendChild(pre);
    $("workers").appendChild(el);
  }
  return pre;
}

function onEvent(ev) {
  if (ev.type === "worker") { started = true; workerPane(ev.task).textContent += ev.text; }
  else if (ev.type === "phase") { (started ? $("synth") : $("plan")).textContent += ev.text; }
  else if (ev.type === "result") {
    const cls = ev.status === "success" ? "badge ok" : "badge err";
    const nodes = [textNode(ev.status, cls)];
    if (ev.failed_task) nodes.push(textNode(" failed_task: " + ev.failed_task, "sub"));
    nodes.push(textNode(" \\u00b7 cost $" + (ev.cost_usd ?? 0).toFixed(6) +
                        " \\u00b7 " + (ev.duration_ms ?? 0) + " ms", "sub"));
    setResult(nodes);
    if (ev.final) { const pre = document.createElement("pre"); pre.textContent = ev.final;
                    $("result").appendChild(pre); }
  }
  else if (ev.type === "error") {
    setResult([textNode("error", "badge err"), textNode(" " + ev.message, "sub")]);
  }
}

$("f").addEventListener("submit", (e) => {
  e.preventDefault();
  const goal = $("goal").value.trim();
  if (!goal) return;
  reset();
  $("run").disabled = true;
  const es = new EventSource("/stream?goal=" + encodeURIComponent(goal));
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    onEvent(ev);
    if (ev.type === "result" || ev.type === "error") { es.close(); $("run").disabled = false; }
  };
  es.onerror = () => { es.close(); $("run").disabled = false; };
});
</script>
</body>
</html>
"""


def create_app(runtime_factory: Callable[[], Any]) -> Any:
    """Build the FastAPI app. `runtime_factory` returns a fresh Runtime per run
    (the Supervisor is non-re-entrant). fastapi is imported lazily so `import
    webui.app` works without the `ui` extra installed."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, StreamingResponse

    app = FastAPI(title="Baton")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _INDEX_HTML

    @app.get("/stream")
    async def stream(goal: str) -> StreamingResponse:
        async def gen():
            async for event in stream_events(runtime_factory(), goal):
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app
