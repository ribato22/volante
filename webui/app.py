from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from volante.observability import read_runs, record_run, summarize, usage_log_path
from webui.runner import stream_events

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="dark" />
<title>Volante — live orchestration</title>
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
  .navlink {
    display:inline-flex; align-items:center; gap:7px; flex:none; cursor:pointer;
    padding:6px 13px; border-radius:999px; font-size:12.5px; font-weight:600;
    text-decoration:none; color:var(--fg-dim);
    border:1px solid var(--line); background:var(--panel);
    transition:color .18s, border-color .18s, background .18s; }
  .navlink:hover { color:var(--accent-2); border-color:rgba(139,92,246,.5); background:var(--accent-soft); }
  .navlink:focus-visible { outline:none; box-shadow:0 0 0 3px var(--accent-soft); }
  .navlink svg { width:15px; height:15px; }

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
  #result .routes { margin-top:12px; padding:10px 12px; border:1px solid var(--line-soft);
    border-radius:var(--r-sm); background:var(--bg); }
  #result .routes-title { color:var(--muted); font-size:10.5px; font-weight:700;
    letter-spacing:.06em; text-transform:uppercase; margin-bottom:5px; }
  #result .route { color:var(--fg-dim); font:12px/1.55 var(--mono); }

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
      <h1>Volante</h1>
      <div class="tag">Multi-model orchestration, streamed live</div>
    </div>
  </div>
  <div class="spacer"></div>
  <a class="navlink" href="/usage">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg>
    Usage
  </a>
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
            placeholder="Describe what you want Volante to accomplish…"
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
  // "~" marks amounts Volante inferred because a provider reported no token usage;
  // showing the figure without it would present an estimate as an authoritative cost.
  const estimated = !!ev.cost_estimated;
  const approx = estimated ? "~" : "";
  head.appendChild(chip("cash", approx + fmtUsd(ev.billed_usd), "cash"));
  head.appendChild(chip("plan credit", approx + fmtUsd(ev.credit_usd), "credit"));
  head.appendChild(chip("subscription calls", ev.subscription_calls || 0));
  head.appendChild(chip("time", fmtDur(ev.duration_ms)));
  r.appendChild(head);
  if (estimated) {
    r.appendChild(Object.assign(document.createElement("div"),
      { className: "note", textContent: "A provider reported no token counts for at least one call in this run, so some of the amounts above are inferred from configured rates rather than reported by the provider." }));
  }
  // A capability the run did NOT have (e.g. code execution withheld) must be visible on
  // success too, or a degraded answer reads like a full-capability one.
  if (ev.capability_notice) {
    r.appendChild(Object.assign(document.createElement("div"),
      { className: "note", textContent: ev.capability_notice }));
  }
  if (Number(ev.billed_usd || 0) === 0 && Number(ev.credit_usd || 0) > 0) {
    r.appendChild(Object.assign(document.createElement("div"),
      { className: "note", textContent: "Ran on your subscription — no cash billed; plan credit is the API-equivalent value." }));
  }
  if (ev.error_code || ev.error_message) {
    const errorText = [ev.error_code, ev.error_message].filter(Boolean).join(": ");
    r.appendChild(Object.assign(document.createElement("div"),
      { className: "note", textContent: errorText }));
  }
  const decisions = ev.routing_decisions && typeof ev.routing_decisions === "object"
    ? Object.entries(ev.routing_decisions) : [];
  if (decisions.length) {
    const routes = document.createElement("div");
    routes.className = "routes";
    routes.appendChild(Object.assign(document.createElement("div"),
      { className: "routes-title", textContent: "Model routing" }));
    decisions.forEach(([task, decision]) => {
      if (!decision || typeof decision !== "object") return;
      const selected = decision.selected_model_id || "unknown";
      const executed = decision.executed_model_id || selected;
      const fallback = executed !== selected ? " → " + executed : "";
      const objective = decision.objective || "quality";
      routes.appendChild(Object.assign(document.createElement("div"), {
        className: "route",
        textContent: task + ": " + selected + fallback + " · " + objective
      }));
    });
    r.appendChild(routes);
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

$("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const goal = $("goal").value.trim();
  if (!goal) { $("goal").focus(); return; }
  reset();
  let token = sessionStorage.getItem("volante-ui-token") || "";
  let response = await fetch("/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "Authorization": "Bearer " + token } : {})
    },
    body: JSON.stringify({goal})
  });
  if (response.status === 401) {
    token = window.prompt("Volante UI access token") || "";
    if (token) sessionStorage.setItem("volante-ui-token", token);
    response = await fetch("/runs", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { "Authorization": "Bearer " + token } : {})
      },
      body: JSON.stringify({goal})
    });
  }
  if (!response.ok) {
    let detail = "Unable to start the run.";
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    onEvent({ type: "error", message: detail });
    return;
  }
  const created = await response.json();
  const es = new EventSource("/runs/" + encodeURIComponent(created.run_id) + "/events");
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


_USAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="dark" />
<title>Volante — usage</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238b5cf6' stroke-width='2.4' stroke-linecap='round'%3E%3Ccircle cx='5' cy='6' r='2.2'/%3E%3Ccircle cx='19' cy='6' r='2.2'/%3E%3Ccircle cx='12' cy='18' r='2.2'/%3E%3Cpath d='M6.6 8 10.7 15.6M17.4 8 13.3 15.6'/%3E%3C/svg%3E" />
<style>
  :root {
    --bg:#0a0c12; --panel:#12151e; --panel-2:#161a25; --elev:#1a1f2c;
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
    margin:0; min-height:100vh; color:var(--fg); font:15px/1.6 var(--sans);
    background:
      radial-gradient(1100px 520px at 80% -10%, rgba(139,92,246,.10), transparent 60%),
      radial-gradient(900px 500px at 0% 0%, rgba(52,211,153,.05), transparent 55%),
      var(--bg); }
  svg { display:block; }
  ::selection { background:rgba(139,92,246,.34); }

  header {
    position:sticky; top:0; z-index:30; display:flex; align-items:center; gap:14px;
    padding:14px clamp(16px,4vw,28px);
    background:rgba(10,12,18,.72); backdrop-filter:blur(12px);
    border-bottom:1px solid var(--line); }
  .brand { display:flex; align-items:center; gap:11px; min-width:0; }
  .brand .logo {
    width:38px; height:38px; flex:none; border-radius:11px; display:grid; place-items:center;
    color:#fff; background:linear-gradient(150deg,var(--accent),#6d28d9);
    box-shadow:0 6px 18px -6px rgba(139,92,246,.7); }
  .brand .logo svg { width:22px; height:22px; }
  .brand h1 { margin:0; font-size:17px; font-weight:700; letter-spacing:-.01em; line-height:1.1; }
  .brand .tag { font-size:12px; color:var(--muted); }
  .spacer { flex:1; }
  .navlink {
    display:inline-flex; align-items:center; gap:7px; flex:none; cursor:pointer;
    padding:6px 13px; border-radius:999px; font-size:12.5px; font-weight:600;
    text-decoration:none; color:var(--fg-dim);
    border:1px solid var(--line); background:var(--panel);
    transition:color .18s, border-color .18s, background .18s; }
  .navlink:hover { color:var(--accent-2); border-color:rgba(139,92,246,.5); background:var(--accent-soft); }
  .navlink:focus-visible { outline:none; box-shadow:0 0 0 3px var(--accent-soft); }
  .navlink svg { width:15px; height:15px; }
  button.navlink { font:600 12.5px var(--sans); }

  main { max-width:1080px; margin:0 auto; padding:clamp(18px,4vw,30px) clamp(16px,4vw,28px) 64px; }
  .page-head { display:flex; align-items:baseline; gap:12px; margin:2px 0 18px; flex-wrap:wrap; }
  .page-head h2 { margin:0; font-size:20px; font-weight:700; letter-spacing:-.01em; }
  .page-head .sub { color:var(--muted); font-size:13px; }
  .card {
    background:linear-gradient(180deg,var(--panel-2),var(--panel));
    border:1px solid var(--line); border-radius:var(--r); box-shadow:var(--shadow); }

  .tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:18px; }
  .tile { padding:15px 16px; }
  .tile .k { font-size:11.5px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:var(--muted); }
  .tile .v { margin-top:7px; font:600 24px/1.1 var(--mono); letter-spacing:-.02em; }
  .tile .v.small { font-size:19px; }
  .tile .v.accent { color:var(--accent-2); }
  .tile .v.ok { color:var(--ok); }

  .table-wrap { padding:6px; overflow-x:auto; }
  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  thead th {
    position:sticky; top:0; text-align:left; font-size:11px; font-weight:600;
    letter-spacing:.05em; text-transform:uppercase; color:var(--muted);
    padding:11px 12px; border-bottom:1px solid var(--line); background:var(--panel-2); white-space:nowrap; }
  tbody td { padding:11px 12px; border-bottom:1px solid var(--line-soft); vertical-align:top; }
  tbody tr:last-child td { border-bottom:0; }
  tbody tr:hover { background:rgba(139,92,246,.05); }
  td.num, th.num { text-align:right; font-family:var(--mono); white-space:nowrap; }
  td.mono { font-family:var(--mono); font-size:12.5px; color:var(--fg-dim); }
  td.ts { color:var(--fg-dim); white-space:nowrap; font-family:var(--mono); font-size:12.5px; }
  td.goal { max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--fg-dim); }
  td.models { max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .badge {
    display:inline-flex; align-items:center; gap:6px; padding:3px 9px; border-radius:999px;
    font-size:11.5px; font-weight:600; border:1px solid var(--line); color:var(--fg-dim); background:var(--panel); }
  .badge .dot { width:7px; height:7px; border-radius:50%; background:var(--faint); flex:none; }
  .badge.ok { color:var(--ok); border-color:rgba(52,211,153,.4); background:var(--ok-soft); }
  .badge.ok .dot { background:var(--ok); }
  .badge.err { color:var(--err); border-color:rgba(248,113,113,.4); background:var(--err-soft); }
  .badge.err .dot { background:var(--err); }
  .src { font-size:11px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .err-code { display:block; margin-top:3px; font:11px var(--mono); color:var(--err); }
  .empty { padding:44px 20px; text-align:center; color:var(--muted); }
  .empty svg { width:40px; height:40px; margin:0 auto 12px; color:var(--faint); }
  .empty code { font-family:var(--mono); color:var(--fg-dim); background:var(--bg); padding:2px 6px; border-radius:6px; }
  .notice { margin:0 0 16px; padding:12px 14px; border-radius:var(--r-sm); font-size:13px;
    border:1px solid rgba(251,191,36,.35); background:rgba(251,191,36,.09); color:#f5d98b; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .spin { animation:spin 1s linear infinite; }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="logo" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5" cy="6" r="2.4"/><circle cx="19" cy="6" r="2.4"/><circle cx="12" cy="18" r="2.4"/><path d="M6.6 8 10.7 15.6M17.4 8 13.3 15.6"/></svg>
    </span>
    <div>
      <h1>Volante</h1>
      <div class="tag">Usage ledger</div>
    </div>
  </div>
  <div class="spacer"></div>
  <button class="navlink" id="refresh" type="button">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
    <span id="refresh-label">Refresh</span>
  </button>
  <a class="navlink" href="/">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>
    Live run
  </a>
</header>

<main>
  <div class="page-head">
    <h2>Usage</h2>
    <span class="sub" id="ledger-path">Every CLI, Web UI, and MCP run is recorded here.</span>
  </div>
  <div id="notice" class="notice" hidden></div>
  <div class="tiles" id="tiles" aria-live="polite"></div>
  <section class="card">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Source</th>
            <th scope="col">Status</th>
            <th scope="col">Models</th>
            <th scope="col" class="num">Cash</th>
            <th scope="col" class="num">Plan credit</th>
            <th scope="col" class="num">Sub&nbsp;calls</th>
            <th scope="col" class="num">Duration</th>
            <th scope="col">Goal</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
      <div class="empty" id="empty" hidden>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/></svg>
        <div id="empty-text">No runs recorded yet.</div>
      </div>
    </div>
  </section>
</main>

<script>
  "use strict";
  const $ = (id) => document.getElementById(id);

  function money(n) {
    const v = Number(n) || 0;
    if (v === 0) return "$0";
    if (v < 0.01) return "$" + v.toFixed(6);
    return "$" + v.toFixed(4);
  }
  function dur(ms) {
    const v = Number(ms) || 0;
    if (v >= 1000) return (v / 1000).toFixed(1) + "s";
    return v + "ms";
  }
  function when(ts) {
    if (!ts) return "—";
    const d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function tile(k, v, cls, note) {
    const box = document.createElement("div");
    box.className = "card tile";
    const kEl = document.createElement("div");
    kEl.className = "k";
    kEl.textContent = k;
    const vEl = document.createElement("div");
    vEl.className = "v" + (cls ? " " + cls : "");
    vEl.textContent = v;
    box.append(kEl, vEl);
    if (note) {
      const nEl = document.createElement("div");
      nEl.className = "k";
      nEl.textContent = note;
      box.append(nEl);
      box.title = "Estimated: a provider reported no token counts for those runs, so part of this total is inferred from configured rates.";
    }
    return box;
  }

  function renderTiles(s) {
    const tiles = $("tiles");
    tiles.replaceChildren();
    const total = s.total_runs || 0;
    const rate = total ? Math.round((s.success_rate || 0) * 100) + "%" : "—";
    // Totals carry the same estimate signal as the rows below them, quantified rather
    // than blanket-marked so a mostly-authoritative total is not written off as inferred.
    const estRuns = s.estimated_runs || 0;
    const cashNote = estRuns ? "incl. ~" + money(s.estimated_billed_usd) + " estimated" : "";
    const creditNote = estRuns ? "incl. ~" + money(s.estimated_credit_usd) + " estimated" : "";
    tiles.append(
      tile("Runs", String(total)),
      tile("Success", (s.successes || 0) + " / " + total + "  ·  " + rate, "small ok"),
      tile("Cash spent", money(s.total_billed_usd), "accent", cashNote),
      tile("Plan credit", money(s.total_credit_usd), "small", creditNote),
      tile("Subscription calls", String(s.total_subscription_calls || 0), "small"),
      tile("Avg duration", dur(s.avg_duration_ms), "small"),
    );
  }

  function statusBadge(run) {
    const ok = run.status === "success";
    const b = document.createElement("span");
    b.className = "badge " + (ok ? "ok" : "err");
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.setAttribute("aria-hidden", "true");
    b.append(dot, document.createTextNode(ok ? "success" : (run.status || "failed")));
    return b;
  }

  function cell(text, cls, title) {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    if (title) td.title = title;
    return td;
  }

  function renderRows(runs) {
    const rows = $("rows");
    rows.replaceChildren();
    for (const run of runs) {
      const tr = document.createElement("tr");
      tr.append(cell(when(run.ts), "ts", run.ts || ""));

      const src = document.createElement("td");
      const srcSpan = document.createElement("span");
      srcSpan.className = "src";
      srcSpan.textContent = run.source || "—";
      src.append(srcSpan);
      tr.append(src);

      const st = document.createElement("td");
      st.append(statusBadge(run));
      if (run.status !== "success" && run.error_code) {
        const ec = document.createElement("code");
        ec.className = "err-code";
        ec.textContent = run.error_code;
        st.append(ec);
      }
      tr.append(st);

      const models = Array.isArray(run.models) ? run.models.join(", ") : "";
      tr.append(cell(models || "—", "models mono", models));
      // "~" + a tooltip keep inferred amounts distinguishable from provider-reported
      // ones in the ledger view, matching the live result panel.
      const est = !!run.cost_estimated;
      const estTitle = est ? "Estimated: the provider reported no token usage for this run." : "";
      tr.append(cell((est ? "~" : "") + money(run.billed_usd), "num", estTitle));
      tr.append(cell((est ? "~" : "") + money(run.credit_usd), "num", estTitle));
      tr.append(cell(String(run.subscription_calls || 0), "num"));
      tr.append(cell(dur(run.duration_ms), "num"));

      let goal = run.goal || "";
      if (run.goal_truncated) goal += "…";
      tr.append(cell(goal || "—", "goal", goal));
      rows.append(tr);
    }
  }

  async function apiUsage() {
    let token = sessionStorage.getItem("volante-ui-token") || "";
    const opts = () => ({ headers: token ? { "Authorization": "Bearer " + token } : {} });
    let res = await fetch("/api/usage", opts());
    if (res.status === 401) {
      token = window.prompt("Volante UI access token") || "";
      if (token) sessionStorage.setItem("volante-ui-token", token);
      res = await fetch("/api/usage", opts());
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    return await res.json();
  }

  function showNotice(msg) {
    const n = $("notice");
    if (!msg) { n.hidden = true; return; }
    n.textContent = msg;
    n.hidden = false;
  }

  async function load() {
    const btn = $("refresh");
    const label = $("refresh-label");
    btn.disabled = true;
    label.textContent = "Loading…";
    try {
      const data = await apiUsage();
      const runs = Array.isArray(data.runs) ? data.runs : [];
      renderTiles(data.summary || {});
      renderRows(runs);
      const hasRows = runs.length > 0;
      $("empty").hidden = hasRows;
      if (!data.enabled) {
        showNotice("Usage logging is disabled (VOLANTE_USAGE_LOG is empty). No runs are being recorded.");
        $("empty-text").textContent = "Usage logging is disabled.";
      } else {
        showNotice("");
        $("empty-text").textContent = "No runs recorded yet. Runs from the CLI, this Web UI, and the MCP server will appear here.";
      }
    } catch (err) {
      showNotice("Could not load usage: " + (err && err.message ? err.message : String(err)));
    } finally {
      btn.disabled = false;
      label.textContent = "Refresh";
    }
  }

  $("refresh").addEventListener("click", load);
  load();
</script>
</body>
</html>
"""


def create_app(
    runtime_factory: Callable[[], Any],
    *,
    auth_token: str | None = None,
    max_goal_chars: int = 20_000,
    max_concurrent_runs: int = 2,
    pending_ttl_s: float = 60.0,
    allowed_hosts: list[str] | None = None,
    usage_limit: int = 200,
) -> Any:
    """Build the FastAPI app. `runtime_factory` returns a fresh Runtime per run
    (the Supervisor is non-re-entrant). fastapi is imported lazily so `import
    webui.app` works without the `ui` extra installed.

    Goals are created with POST and exchanged for a one-use opaque run id before
    SSE starts. This keeps prompts out of URLs/access logs and bounds queued plus
    active runs. Set ``auth_token`` before exposing the UI beyond loopback.
    """
    if max_goal_chars < 1:
        raise ValueError("max_goal_chars must be positive")
    if max_concurrent_runs < 1:
        raise ValueError("max_concurrent_runs must be positive")
    if pending_ttl_s <= 0:
        raise ValueError("pending_ttl_s must be positive")
    if usage_limit < 1:
        raise ValueError("usage_limit must be positive")

    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.responses import HTMLResponse, StreamingResponse
    from starlette.background import BackgroundTask

    # With ``from __future__ import annotations`` FastAPI resolves endpoint
    # annotations against module globals. Keep the optional dependency lazy, but
    # expose Request only after create_app() has imported the UI extra.
    globals()["Request"] = Request

    app = FastAPI(title="Volante")
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts or ["127.0.0.1", "localhost", "testserver", "[::1]"],
    )
    pending: dict[str, tuple[str, float]] = {}
    active = 0
    state_lock = asyncio.Lock()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src data:; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    def _constant_time_equal(supplied: str, expected: str) -> bool:
        # BYTES, not str. `hmac.compare_digest` raises TypeError when a str holds a
        # character above U+007F, and uvicorn decodes header bytes as latin-1 — so a
        # single high byte in a header escaped the guard entirely and became a 500
        # with a traceback where the guard meant 401. Encoding first keeps the
        # comparison constant-time and makes every byte sequence a comparison rather
        # than an exception. surrogateescape carries through anything latin-1
        # produced that utf-8 cannot represent.
        return hmac.compare_digest(
            supplied.encode("utf-8", "surrogateescape"), expected.encode("utf-8")
        )

    def require_authorized(request: Request) -> None:
        if auth_token is None:
            return
        authorization = request.headers.get("authorization", "")
        if not _constant_time_equal(authorization, f"Bearer {auth_token}"):
            raise HTTPException(
                status_code=401,
                detail="A valid Volante UI bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def require_same_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin is None:
            return
        # HOST, not scheme://host. The CSRF property this guard exists for comes from
        # the origin's host matching ours; the scheme adds nothing to it and breaks
        # the deployment 0.4.0 explicitly blessed. Behind a TLS-terminating proxy the
        # browser sends `Origin: https://host` while the app is served over http, and
        # uvicorn only rewrites the scheme from X-Forwarded-Proto when the peer is in
        # forwarded_allow_ips (default 127.0.0.1) — so every Docker, k8s or CDN
        # deployment failed a check that was never about the scheme.
        supplied = urlsplit(origin.rstrip("/")).netloc
        expected = request.headers.get("host", "")
        if not supplied or not _constant_time_equal(supplied, expected):
            raise HTTPException(status_code=403, detail="Cross-origin run creation is forbidden.")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _INDEX_HTML

    @app.post("/runs", status_code=201)
    async def create_run(request: Request) -> dict[str, str]:
        nonlocal active
        require_authorized(request)
        require_same_origin(request)
        # JSON escaping can expand one goal character to several bytes. This
        # ceiling admits the configured character limit while bounding the raw
        # request before parsing/allocating an arbitrary body.
        max_body_bytes = max_goal_chars * 12 + 1_024
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid Content-Length header."
                ) from exc
            if declared_length > max_body_bytes:
                raise HTTPException(
                    status_code=413, detail="Request body is too large."
                )
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > max_body_bytes:
                raise HTTPException(
                    status_code=413, detail="Request body is too large."
                )
            raw.extend(chunk)
        try:
            body = json.loads(bytes(raw))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Request body must be JSON.") from exc
        goal = body.get("goal") if isinstance(body, dict) else None
        if not isinstance(goal, str) or not goal.strip():
            raise HTTPException(status_code=422, detail="goal must be a non-empty string.")
        goal = goal.strip()
        if len(goal) > max_goal_chars:
            raise HTTPException(
                status_code=413,
                detail=f"goal exceeds the {max_goal_chars}-character limit.",
            )
        now = time.monotonic()
        async with state_lock:
            expired = [run_id for run_id, (_, expiry) in pending.items() if expiry <= now]
            for run_id in expired:
                pending.pop(run_id, None)
            if active + len(pending) >= max_concurrent_runs:
                raise HTTPException(
                    status_code=429,
                    detail="The Volante UI run limit has been reached; retry after a run completes.",
                )
            run_id = secrets.token_urlsafe(32)
            pending[run_id] = (goal, now + pending_ttl_s)
        return {"run_id": run_id}

    @app.get("/runs/{run_id}/events")
    async def stream(run_id: str) -> StreamingResponse:
        nonlocal active
        now = time.monotonic()
        async with state_lock:
            item = pending.pop(run_id, None)
            if item is None or item[1] <= now:
                raise HTTPException(status_code=404, detail="Unknown or expired run id.")
            goal = item[0]
            active += 1

        async def release() -> None:
            nonlocal active
            async with state_lock:
                active -= 1

        async def gen():
            async for event in stream_events(runtime_factory(), goal):
                if isinstance(event, dict) and event.get("type") == "result":
                    # Persist to the shared usage ledger so browser runs show up in
                    # the Usage dashboard alongside CLI/MCP runs. Best-effort.
                    record_run(event, goal=goal, prefer=None, source="webui")
                yield f"data: {json.dumps(event)}\n\n"

        # The slot is released by the response's BACKGROUND task, not by a `finally`
        # inside the generator. Starlette runs `background` after its task group
        # unwinds, which covers the disconnect path as well as normal completion; a
        # generator that the client walked away from is merely ABANDONED, and its
        # finally then runs whenever CPython gets round to finalizing it. That was
        # measurable rather than theoretical: the same probe returned the slot with an
        # explicit gc.collect() and never returned it without one. At the default
        # max_concurrent_runs=2, two page reloads wedged the UI until restart.
        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
            background=BackgroundTask(release),
        )

    @app.get("/usage", response_class=HTMLResponse)
    async def usage_page() -> str:
        return _USAGE_HTML

    @app.get("/api/usage")
    async def usage_api(request: Request) -> dict[str, Any]:
        # Read-only ledger view; gated by the same bearer token as run creation so it is
        # not exposed beyond loopback without a token. GET is safe, so no origin check.
        require_authorized(request)
        runs = read_runs(limit=usage_limit)
        return {
            "enabled": usage_log_path() is not None,
            "summary": summarize(runs),
            "runs": runs,
        }

    return app
