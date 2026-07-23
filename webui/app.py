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
<meta name="color-scheme" content="dark" />
<title>Baton — live orchestration</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238b5cf6' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='5' cy='6' r='2.2'/%3E%3Ccircle cx='19' cy='6' r='2.2'/%3E%3Ccircle cx='12' cy='18' r='2.2'/%3E%3Cpath d='M6.6 8 10.7 15.6M17.4 8 13.3 15.6'/%3E%3C/svg%3E" />

<style>
  :root {
    --bg:#0a0c12; --bg-grid:rgba(139,92,246,.05);
    --panel:#12151e; --panel-2:#161a25; --elev:#1a1f2c;
    --line:#232a3a; --line-soft:#1a2030;
    --fg:#eef1f7; --fg-dim:#c6ccda; --muted:#8b93a7; --faint:#5b6478;
    --accent:#8b5cf6; --accent-2:#a78bfa; --accent-soft:rgba(139,92,246,.14);
    --ok:#34d399; --ok-soft:rgba(52,211,153,.13);
    --warn:#fbbf24;
    --err:#f87171; --err-soft:rgba(248,113,113,.13);
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
    --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,"Cascadia Code","Roboto Mono",monospace;
    --r:14px; --r-sm:10px; --shadow:0 10px 30px -12px rgba(0,0,0,.6);
  }
  * { box-sizing:border-box; }
  html { -webkit-text-size-adjust:100%; }
  body {
    margin:0; min-height:100vh; color:var(--fg);
    font:15px/1.6 var(--sans);
    background:
      radial-gradient(1100px 520px at 80% -10%, rgba(139,92,246,.10), transparent 60%),
      radial-gradient(900px 500px at 0% 0%, rgba(52,211,153,.05), transparent 55%),
      var(--bg);
  }
  svg { display:block; }
  ::selection { background:rgba(139,92,246,.34); }

  /* ---- header ---------------------------------------------------------- */
  header {
    position:sticky; top:0; z-index:30;
    display:flex; align-items:center; gap:14px;
    padding:14px clamp(16px,4vw,28px);
    background:rgba(10,12,18,.72); backdrop-filter:blur(12px);
    border-bottom:1px solid var(--line);
  }
  .brand { display:flex; align-items:center; gap:11px; min-width:0; }
  .brand .logo {
    width:38px; height:38px; flex:none; border-radius:11px;
    display:grid; place-items:center; color:#fff;
    background:linear-gradient(150deg,var(--accent),#6d28d9);
    box-shadow:0 6px 18px -6px rgba(139,92,246,.7); }
  .brand .logo svg { width:22px; height:22px; }
  .brand h1 { margin:0; font-size:17px; font-weight:700; letter-spacing:-.01em; line-height:1.1; }
  .brand .tag { font-size:12px; color:var(--muted); }
  .spacer { flex:1; }
  .status {
    display:inline-flex; align-items:center; gap:8px; flex:none;
    padding:6px 12px 6px 10px; border-radius:999px; font-size:12.5px; font-weight:600;
    border:1px solid var(--line); background:var(--panel); color:var(--fg-dim);
    transition:color .18s, border-color .18s, background .18s; }
  .status .dot { width:8px; height:8px; border-radius:50%; background:var(--faint); flex:none; }
  .status[data-run="running"] { color:var(--accent-2); border-color:rgba(139,92,246,.5); background:var(--accent-soft); }
  .status[data-run="running"] .dot { background:var(--accent-2); animation:pulse 1.4s ease-in-out infinite; }
  .status[data-run="done"] { color:var(--ok); border-color:rgba(52,211,153,.45); background:var(--ok-soft); }
  .status[data-run="done"] .dot { background:var(--ok); }
  .status[data-run="failed"] { color:var(--err); border-color:rgba(248,113,113,.45); background:var(--err-soft); }
  .status[data-run="failed"] .dot { background:var(--err); }

  /* ---- layout ---------------------------------------------------------- */
  main { max-width:1080px; margin:0 auto; padding:clamp(18px,4vw,30px) clamp(16px,4vw,28px) 64px; }
  .card {
    background:linear-gradient(180deg,var(--panel-2),var(--panel));
    border:1px solid var(--line); border-radius:var(--r); box-shadow:var(--shadow); }

  /* ---- composer -------------------------------------------------------- */
  .composer { padding:16px; margin-bottom:18px; }
  .composer label { display:block; font-size:12px; font-weight:600; letter-spacing:.04em;
    text-transform:uppercase; color:var(--muted); margin-bottom:9px; }
  form { display:flex; gap:10px; }
  .input-wrap { position:relative; flex:1; display:flex; align-items:center; }
  .input-wrap svg { position:absolute; left:13px; width:18px; height:18px; color:var(--faint); pointer-events:none; }
  input#goal {
    width:100%; background:var(--bg); border:1px solid var(--line); color:var(--fg);
    border-radius:var(--r-sm); padding:13px 14px 13px 40px; font:15px/1.4 var(--sans);
    transition:border-color .18s, box-shadow .18s; }
  input#goal::placeholder { color:var(--faint); }
  input#goal:focus-visible { outline:none; border-color:var(--accent);
    box-shadow:0 0 0 3px var(--accent-soft); }
  button#run {
    display:inline-flex; align-items:center; gap:8px; flex:none; cursor:pointer;
    background:linear-gradient(180deg,var(--accent-2),var(--accent)); color:#fff;
    border:0; border-radius:var(--r-sm); padding:0 20px; min-height:48px;
    font:600 15px var(--sans); letter-spacing:.01em;
    box-shadow:0 8px 20px -8px rgba(139,92,246,.8);
    transition:filter .18s, box-shadow .18s, opacity .18s; }
  button#run svg { width:17px; height:17px; }
  button#run:hover { filter:brightness(1.08); }
  button#run:focus-visible { outline:none; box-shadow:0 0 0 3px var(--accent-soft),0 8px 20px -8px rgba(139,92,246,.8); }
  button#run:disabled { cursor:default; opacity:.55; filter:saturate(.6); box-shadow:none; }
  button#run .spin { display:none; }
  button#run[data-busy="1"] .ico-run { display:none; }
  button#run[data-busy="1"] .spin { display:block; animation:spin 1s linear infinite; }

  /* ---- stepper --------------------------------------------------------- */
  .stepper { display:flex; list-style:none; margin:0 0 18px; padding:16px 18px; gap:0; overflow-x:auto; }
  .step { position:relative; flex:1 1 0; min-width:104px; display:flex; flex-direction:column;
    align-items:center; text-align:center; gap:9px; }
  .step .bar { position:absolute; top:19px; left:calc(50% + 24px); right:calc(-50% + 24px); height:2px;
    background:var(--line); border-radius:2px; transition:background .3s; }
  .step[data-state="done"] .bar { background:linear-gradient(90deg,var(--ok),var(--accent)); }
  .step:last-child .bar { display:none; }
  .node {
    position:relative; z-index:1; width:38px; height:38px; border-radius:50%;
    display:grid; place-items:center; flex:none;
    background:var(--panel); border:1.5px solid var(--line); color:var(--muted);
    transition:color .2s, border-color .2s, background .2s, box-shadow .2s; }
  .node svg { width:19px; height:19px; }
  .node .done, .node .fail { display:none; }
  .step[data-state="active"] .node { color:var(--accent-2); border-color:var(--accent);
    background:var(--accent-soft); box-shadow:0 0 0 4px var(--accent-soft); animation:ring 1.6s ease-in-out infinite; }
  .step[data-state="done"] .node { color:var(--ok); border-color:rgba(52,211,153,.55); background:var(--ok-soft); }
  .step[data-state="done"] .node .base { display:none; }
  .step[data-state="done"] .node .done { display:block; }
  .step[data-state="failed"] .node { color:var(--err); border-color:rgba(248,113,113,.55); background:var(--err-soft); }
  .step[data-state="failed"] .node .base { display:none; }
  .step[data-state="failed"] .node .fail { display:block; }
  .step .name { font-size:13px; font-weight:600; color:var(--fg-dim); letter-spacing:.01em; }
  .step[data-state="active"] .name { color:var(--fg); }
  .step[data-state="pending"] .name { color:var(--muted); }
  .step .sub { font-size:11px; color:var(--faint); min-height:14px; }
  .step[data-state="active"] .sub { color:var(--accent-2); }
  .step[data-state="done"] .sub { color:var(--ok); }
  .step[data-state="failed"] .sub { color:var(--err); }

  /* ---- panels ---------------------------------------------------------- */
  .panel { margin-bottom:16px; overflow:hidden; }
  .panel > .head { display:flex; align-items:center; gap:10px; padding:13px 16px;
    border-bottom:1px solid var(--line-soft); }
  .panel > .head .ph-ico { width:18px; height:18px; color:var(--muted); flex:none; transition:color .2s; }
  .panel > .head h2 { margin:0; font-size:12.5px; font-weight:600; letter-spacing:.06em;
    text-transform:uppercase; color:var(--fg-dim); }
  .panel > .head .pill { margin-left:auto; font-size:11px; font-weight:600; color:var(--faint);
    padding:3px 10px; border-radius:999px; border:1px solid var(--line); background:var(--bg);
    letter-spacing:.02em; transition:color .2s, border-color .2s, background .2s; }
  .panel[data-state="active"] > .head .ph-ico { color:var(--accent-2); }
  .panel[data-state="active"] > .head .pill { color:var(--accent-2); border-color:rgba(139,92,246,.4); background:var(--accent-soft); }
  .panel[data-state="done"] > .head .ph-ico { color:var(--ok); }
  .panel[data-state="done"] > .head .pill { color:var(--ok); border-color:rgba(52,211,153,.4); background:var(--ok-soft); }
  .panel[data-state="failed"] > .head .ph-ico { color:var(--err); }
  .panel[data-state="failed"] > .head .pill { color:var(--err); border-color:rgba(248,113,113,.4); background:var(--err-soft); }
  .panel .body { padding:14px 16px; }
  pre { margin:0; white-space:pre-wrap; word-break:break-word; overflow-wrap:anywhere;
    font:13px/1.6 var(--mono); color:var(--fg-dim); }
  pre:empty::before { content:attr(data-empty); color:var(--faint); font-style:italic;
    font-family:var(--sans); font-size:13px; }

  /* ---- workers grid ---------------------------------------------------- */
  #workers { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px; }
  #workers:empty::before { content:attr(data-empty); color:var(--faint); font-style:italic; font-size:13px; }
  .task { background:var(--bg); border:1px solid var(--line-soft); border-radius:var(--r-sm);
    overflow:hidden; transition:border-color .2s; }
  .task .t-head { display:flex; align-items:center; gap:8px; padding:9px 12px;
    border-bottom:1px solid var(--line-soft); background:var(--panel); }
  .task .t-dot { width:7px; height:7px; border-radius:50%; background:var(--accent-2); flex:none;
    animation:pulse 1.4s ease-in-out infinite; }
  .task[data-state="done"] { border-color:rgba(52,211,153,.3); }
  .task[data-state="done"] .t-dot { background:var(--ok); animation:none; }
  .task[data-state="failed"] { border-color:rgba(248,113,113,.4); }
  .task[data-state="failed"] .t-dot { background:var(--err); animation:none; }
  .task .t-id { font:600 12px var(--mono); color:var(--accent-2); letter-spacing:.01em; }
  .task[data-state="done"] .t-id { color:var(--ok); }
  .task[data-state="failed"] .t-id { color:var(--err); }
  .task pre { padding:11px 12px; font-size:12.5px; max-height:340px; overflow:auto; }

  /* ---- result ---------------------------------------------------------- */
  #result .r-head { display:flex; align-items:center; flex-wrap:wrap; gap:10px; }
  .badge { display:inline-flex; align-items:center; gap:7px; padding:5px 13px; border-radius:999px;
    font-size:12.5px; font-weight:700; letter-spacing:.02em; text-transform:capitalize; }
  .badge svg { width:14px; height:14px; }
  .badge.ok { background:var(--ok-soft); color:var(--ok); }
  .badge.err { background:var(--err-soft); color:var(--err); }
  .chip { display:inline-flex; align-items:baseline; gap:6px; padding:5px 11px; border-radius:999px;
    border:1px solid var(--line); background:var(--bg); font-size:12px; }
  .chip .chip-k { color:var(--muted); letter-spacing:.03em; text-transform:uppercase; font-size:10.5px; font-weight:600; }
  .chip .chip-v { color:var(--fg-dim); font-family:var(--mono); font-weight:600; }
  .chip.cash .chip-v { color:var(--fg); }
  .chip.credit .chip-v { color:var(--accent-2); }
  #result .final { margin-top:14px; padding-top:14px; border-top:1px solid var(--line-soft); }
  #result .note { font-size:12px; color:var(--muted); margin-top:2px; }

  /* ---- responsive ------------------------------------------------------ */
  @media (max-width:640px) {
    form { flex-direction:column; }
    button#run { justify-content:center; min-height:46px; }
    .brand .tag { display:none; }
    .step { min-width:78px; }
    .step .name { font-size:12px; }
  }

  /* ---- motion ---------------------------------------------------------- */
  @keyframes spin { to { transform:rotate(360deg); } }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }
  @keyframes ring { 0%,100% { box-shadow:0 0 0 4px var(--accent-soft); } 50% { box-shadow:0 0 0 7px rgba(139,92,246,.05); } }
  @media (prefers-reduced-motion:reduce) {
    *, *::before, *::after { animation:none !important; transition:none !important; }
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="logo" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2.4"/><circle cx="19" cy="6" r="2.4"/><circle cx="12" cy="18" r="2.4"/><path d="M6.6 8 10.7 15.6M17.4 8 13.3 15.6"/></svg>
    </span>
    <div>
      <h1>Baton</h1>
      <div class="tag">Multi-model orchestration, streamed live</div>
    </div>
  </div>
  <div class="spacer"></div>
  <span class="status" id="status" data-run="idle" role="status" aria-live="polite">
    <span class="dot" aria-hidden="true"></span><span id="status-text">Idle</span>
  </span>
</header>

<main>
  <section class="card composer">
    <form id="f">
      <div style="flex:1">
        <label for="goal">Goal</label>
        <div class="input-wrap">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m7 8 4 4-4 4"/><path d="M13 16h4"/></svg>
          <input id="goal" name="goal" autocomplete="off" spellcheck="false"
            placeholder="Describe what you want Baton to accomplish…"
            value="Write a short brief about concurrency in Python" />
        </div>
      </div>
      <button id="run" type="submit">
        <svg class="ico-run" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>
        <svg class="spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.2-8.5" opacity=".9"/></svg>
        <span id="run-label">Run</span>
      </button>
    </form>
  </section>

  <ol class="stepper card" id="stepper" aria-label="Run progress">
    <li class="step" data-phase="plan" data-state="pending">
      <span class="bar" aria-hidden="true"></span>
      <span class="node" aria-hidden="true">
        <svg class="base" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M9 19h7a3 3 0 0 0 0-6H8a3 3 0 0 1 0-6h7"/></svg>
        <svg class="done" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        <svg class="fail" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </span>
      <span class="name">Plan</span><span class="sub" data-phase-sub="plan">pending</span>
    </li>
    <li class="step" data-phase="workers" data-state="pending">
      <span class="bar" aria-hidden="true"></span>
      <span class="node" aria-hidden="true">
        <svg class="base" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="7" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="14" width="7" height="7" rx="1.4"/></svg>
        <svg class="done" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        <svg class="fail" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </span>
      <span class="name">Workers</span><span class="sub" data-phase-sub="workers">pending</span>
    </li>
    <li class="step" data-phase="synthesis" data-state="pending">
      <span class="bar" aria-hidden="true"></span>
      <span class="node" aria-hidden="true">
        <svg class="base" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="18" r="2.5"/><circle cx="6" cy="6" r="2.5"/><path d="M6 8.5V10a8 8 0 0 0 8 8h1.5"/></svg>
        <svg class="done" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        <svg class="fail" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </span>
      <span class="name">Synthesis</span><span class="sub" data-phase-sub="synthesis">pending</span>
    </li>
    <li class="step" data-phase="result" data-state="pending">
      <span class="bar" aria-hidden="true"></span>
      <span class="node" aria-hidden="true">
        <svg class="base" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
        <svg class="done" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
        <svg class="fail" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </span>
      <span class="name">Result</span><span class="sub" data-phase-sub="result">pending</span>
    </li>
  </ol>

  <section class="card panel" data-phase="plan" data-state="pending">
    <div class="head">
      <svg class="ph-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="5" r="2.5"/><path d="M9 19h7a3 3 0 0 0 0-6H8a3 3 0 0 1 0-6h7"/></svg>
      <h2>Plan</h2><span class="pill" data-phase-pill="plan">idle</span>
    </div>
    <div class="body"><pre id="plan" role="log" aria-live="polite" aria-label="Plan (task DAG)" data-empty="The validated task DAG will appear here."></pre></div>
  </section>

  <section class="card panel" data-phase="workers" data-state="pending">
    <div class="head">
      <svg class="ph-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.4"/><rect x="14" y="3" width="7" height="7" rx="1.4"/><rect x="3" y="14" width="7" height="7" rx="1.4"/><rect x="14" y="14" width="7" height="7" rx="1.4"/></svg>
      <h2>Workers</h2><span class="pill" data-phase-pill="workers">idle</span>
    </div>
    <div class="body"><div id="workers" role="log" aria-live="polite" aria-label="Parallel workers" data-empty="Parallel per-task output will stream here."></div></div>
  </section>

  <section class="card panel" data-phase="synthesis" data-state="pending">
    <div class="head">
      <svg class="ph-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="18" cy="18" r="2.5"/><circle cx="6" cy="6" r="2.5"/><path d="M6 8.5V10a8 8 0 0 0 8 8h1.5"/></svg>
      <h2>Synthesis</h2><span class="pill" data-phase-pill="synthesis">idle</span>
    </div>
    <div class="body"><pre id="synth" role="log" aria-live="polite" aria-label="Synthesis" data-empty="The merged final answer will stream here."></pre></div>
  </section>

  <section class="card panel" data-phase="result" data-state="pending">
    <div class="head">
      <svg class="ph-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
      <h2>Result</h2><span class="pill" data-phase-pill="result">idle</span>
    </div>
    <div class="body"><div id="result" aria-live="polite"><span class="note">Enter a goal and press Run to start.</span></div></div>
  </section>
</main>

<script>
// Every dynamic value (model output, error text, task ids) is inserted via
// textContent / DOM nodes only — never raw markup — so streamed content can never
// inject executable markup into the page.
const $ = (id) => document.getElementById(id);
const PHASES = ["plan", "workers", "synthesis", "result"];
const SUB = { pending: "pending", active: "running…", done: "done", failed: "failed" };

function svgIcon(kind) {
  // Small inline SVG built via the SVG DOM (no markup strings).
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "2.4");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", kind === "ok" ? "M20 6 9 17l-5-5" : "M18 6 6 18M6 6l12 12");
  s.appendChild(p);
  return s;
}

function textNode(t, cls) {
  const s = document.createElement("span");
  s.textContent = t;
  if (cls) s.className = cls;
  return s;
}

function setPhase(name, state) {
  const step = document.querySelector('.step[data-phase="' + name + '"]');
  if (step) {
    step.dataset.state = state;
    if (state === "active") step.setAttribute("aria-current", "step");
    else step.removeAttribute("aria-current");
    const sub = step.querySelector('[data-phase-sub="' + name + '"]');
    if (sub) sub.textContent = SUB[state];
  }
  const panel = document.querySelector('.panel[data-phase="' + name + '"]');
  if (panel) {
    panel.dataset.state = state;
    const pill = panel.querySelector('[data-phase-pill="' + name + '"]');
    if (pill) pill.textContent = state === "pending" ? "idle" : SUB[state];
  }
}

function advanceTo(name) {
  // Everything before `name` is done, `name` is active, the rest pending.
  const i = PHASES.indexOf(name);
  PHASES.forEach((p, idx) => setPhase(p, idx < i ? "done" : idx === i ? "active" : "pending"));
}

function setStatus(run, label) {
  $("status").dataset.run = run;
  $("status-text").textContent = label;
}

function setBusy(busy) {
  const b = $("run");
  b.disabled = busy;
  b.dataset.busy = busy ? "1" : "0";
  $("run-label").textContent = busy ? "Running" : "Run";
}

function reset() {
  $("plan").textContent = "";
  $("synth").textContent = "";
  $("workers").textContent = "";
  $("result").textContent = "";
  PHASES.forEach((p) => setPhase(p, "pending"));
  advanceTo("plan");
  setStatus("running", "Running");
  setBusy(true);
}

function fmtUsd(x) { return "$" + Number(x || 0).toFixed(6); }
function fmtDur(ms) {
  ms = Number(ms || 0);
  return ms >= 1000 ? (ms / 1000).toFixed(1) + " s" : ms + " ms";
}

function chip(k, v, cls) {
  const c = document.createElement("span");
  c.className = "chip" + (cls ? " " + cls : "");
  c.appendChild(textNode(k, "chip-k"));
  c.appendChild(textNode(v, "chip-v"));
  return c;
}

function workerPane(task) {
  let pre = $("wp-" + task);
  if (!pre) {
    const card = document.createElement("div");
    card.className = "task";
    card.dataset.state = "active";
    card.setAttribute("data-task", task);
    const head = document.createElement("div");
    head.className = "t-head";
    head.appendChild(Object.assign(document.createElement("span"), { className: "t-dot" }));
    head.appendChild(textNode(task, "t-id"));
    pre = document.createElement("pre");
    pre.id = "wp-" + task;
    card.appendChild(head);
    card.appendChild(pre);
    $("workers").appendChild(card);
  }
  return pre;
}

function setWorkerState(task, state) {
  // Look up by id (getElementById is an exact-match, not a CSS selector) so an
  // unusual planner-supplied task id (quotes / backslashes / newlines) can't build
  // an invalid selector and throw. The card wraps <pre id="wp-<task>">.
  const pre = document.getElementById("wp-" + task);
  const card = pre && pre.closest(".task");
  if (card) card.dataset.state = state;
}
function allWorkers(state) {
  document.querySelectorAll("#workers .task").forEach((c) => { c.dataset.state = state; });
}

function renderResult(ev) {
  const r = $("result");
  r.textContent = "";
  const head = document.createElement("div");
  head.className = "r-head";
  const ok = ev.status === "success";
  const badge = document.createElement("span");
  badge.className = "badge " + (ok ? "ok" : "err");
  badge.appendChild(svgIcon(ok ? "ok" : "x"));
  badge.appendChild(textNode(ev.status, null));
  head.appendChild(badge);
  if (ev.failed_task) head.appendChild(chip("failed task", ev.failed_task, "credit"));
  head.appendChild(chip("cash", fmtUsd(ev.billed_usd), "cash"));
  head.appendChild(chip("plan credit", fmtUsd(ev.credit_usd), "credit"));
  head.appendChild(chip("time", fmtDur(ev.duration_ms)));
  r.appendChild(head);
  if (Number(ev.billed_usd || 0) === 0 && Number(ev.credit_usd || 0) > 0) {
    r.appendChild(Object.assign(document.createElement("div"),
      { className: "note", textContent: "Ran on your subscription — no cash billed; plan credit is the API-equivalent value." }));
  }
  if (ev.final) {
    const wrap = document.createElement("div");
    wrap.className = "final";
    const pre = document.createElement("pre");
    pre.textContent = ev.final;
    wrap.appendChild(pre);
    r.appendChild(wrap);
  }
}

function onEvent(ev) {
  if (ev.type === "stage") {
    advanceTo(ev.stage);
    if (ev.stage === "synthesis") allWorkers("done");
  } else if (ev.type === "worker") {
    if (document.querySelector('.step[data-phase="workers"]').dataset.state !== "active"
        && document.querySelector('.step[data-phase="synthesis"]').dataset.state === "pending") {
      advanceTo("workers");
    }
    workerPane(ev.task).textContent += ev.text;
  } else if (ev.type === "phase") {
    // Plan streams before workers; synthesis after. Route by the active phase.
    const synthActive = document.querySelector('.step[data-phase="synthesis"]').dataset.state === "active";
    (synthActive ? $("synth") : $("plan")).textContent += ev.text;
  } else if (ev.type === "result") {
    const ok = ev.status === "success";
    if (ok) {
      PHASES.forEach((p) => setPhase(p, "done"));
    } else {
      if (ev.failed_task) { setWorkerState(ev.failed_task, "failed"); setPhase("workers", "failed"); }
      setPhase("result", "failed");
    }
    renderResult(ev);
    setStatus(ok ? "done" : "failed", ok ? "Done" : "Failed");
    setBusy(false);
  } else if (ev.type === "error") {
    // Mark whichever phase was active as failed.
    const active = PHASES.find((p) =>
      document.querySelector('.step[data-phase="' + p + '"]').dataset.state === "active") || "plan";
    setPhase(active, "failed");
    setPhase("result", "failed");
    const r = $("result");
    r.textContent = "";
    const head = document.createElement("div");
    head.className = "r-head";
    const badge = document.createElement("span");
    badge.className = "badge err";
    badge.appendChild(svgIcon("x"));
    badge.appendChild(textNode("error", null));
    head.appendChild(badge);
    r.appendChild(head);
    r.appendChild(Object.assign(document.createElement("div"), { className: "note", textContent: ev.message }));
    setStatus("failed", "Failed");
    setBusy(false);
  }
}

$("f").addEventListener("submit", (e) => {
  e.preventDefault();
  const goal = $("goal").value.trim();
  if (!goal) { $("goal").focus(); return; }
  reset();
  const es = new EventSource("/stream?goal=" + encodeURIComponent(goal));
  es.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch (_) { return; }
    const terminal = ev.type === "result" || ev.type === "error";
    try {
      onEvent(ev);
    } catch (err) {
      // A render error must never swallow stream cleanup (else es.onerror would
      // fire and show a misleading "Connection lost" over the real result).
      console.error(err);
    } finally {
      if (terminal) { es.close(); setBusy(false); }
    }
  };
  es.onerror = () => {
    es.close();
    if ($("run").disabled) {
      onEvent({ type: "error", message: "Connection to the server was lost." });
    }
  };
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
