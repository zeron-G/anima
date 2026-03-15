"""Dashboard HTML — multi-page SPA with sidebar navigation."""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ANIMA Dashboard</title>
<style>
:root {
  --bg: #0d1117; --surface: #161b22; --surface2: #21262d; --border: #30363d;
  --text: #e6edf3; --text2: #8b949e; --accent: #58a6ff; --green: #3fb950;
  --yellow: #d29922; --red: #f85149; --purple: #bc8cff; --pink: #f778ba;
  --sidebar-w: 56px;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; font-size:14px; overflow:hidden; height:100vh; }

/* ── Sidebar ── */
.sidebar { position:fixed; left:0; top:0; width:var(--sidebar-w); height:100vh; background:var(--surface); border-right:1px solid var(--border); display:flex; flex-direction:column; z-index:100; }
.nav-item { width:56px; height:48px; display:flex; align-items:center; justify-content:center; font-size:22px; cursor:pointer; color:var(--text2); border-left:3px solid transparent; transition:all .15s; position:relative; text-decoration:none; }
.nav-item:hover { color:var(--text); background:var(--surface2); }
.nav-item.active { color:var(--accent); border-left-color:var(--accent); background:var(--surface2); }
.nav-item .tooltip { position:absolute; left:60px; background:var(--surface2); color:var(--text); padding:4px 10px; border-radius:4px; font-size:12px; white-space:nowrap; pointer-events:none; opacity:0; transition:opacity .15s; border:1px solid var(--border); z-index:200; }
.nav-item:hover .tooltip { opacity:1; }
.nav-spacer { flex:1; }

/* ── Header ── */
.header { background:var(--surface); border-bottom:1px solid var(--border); padding:10px 20px; display:flex; justify-content:space-between; align-items:center; height:48px; margin-left:var(--sidebar-w); }
.header h1 { font-size:18px; font-weight:600; }
.header h1 .name { color:var(--accent); }
.header .meta { color:var(--text2); font-size:13px; display:flex; align-items:center; gap:12px; }
.status-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:4px; }
.status-dot.alive { background:var(--green); box-shadow:0 0 6px var(--green); animation:pulse-dot 2s ease-in-out infinite; }
.status-dot.stopped { background:var(--red); }
@keyframes pulse-dot { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.5;transform:scale(1.3)} }
.page-indicator { color:var(--accent); font-weight:600; font-size:13px; }

/* ── Main content ── */
.main { margin-left:var(--sidebar-w); height:calc(100vh - 48px); overflow-y:auto; }
.page { display:none; padding:16px; height:100%; animation:fadeIn .15s ease; }
.page.active { display:block; }
@keyframes fadeIn { from{opacity:0} to{opacity:1} }

/* ── Panels ── */
.panel { background:var(--surface); border:1px solid var(--border); border-radius:8px; overflow:hidden; display:flex; flex-direction:column; }
.panel-title { background:var(--surface2); padding:8px 14px; font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.5px; color:var(--text2); border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; flex-shrink:0; }
.panel-body { padding:12px 14px; overflow-y:auto; flex:1; min-height:0; }

/* ── Overview page ── */
.overview-grid { display:grid; grid-template-columns:60% 40%; gap:16px; height:calc(100vh - 80px); }
.overview-left, .overview-right { display:flex; flex-direction:column; gap:12px; min-height:0; overflow-y:auto; }

/* Heartbeat pulse */
.pulse-container { display:flex; align-items:center; gap:20px; margin-bottom:12px; }
.pulse-circle { width:60px; height:60px; border-radius:50%; background:var(--green); position:relative; flex-shrink:0; }
.pulse-circle.alive { animation:pulseGlow 2s ease-in-out infinite; box-shadow:0 0 20px var(--green), 0 0 40px rgba(63,185,80,.3); }
.pulse-circle.stopped { background:var(--red); box-shadow:0 0 10px var(--red); animation:none; }
@keyframes pulseGlow {
  0%,100% { transform:scale(1); box-shadow:0 0 20px var(--green), 0 0 40px rgba(63,185,80,.3); }
  50% { transform:scale(1.12); box-shadow:0 0 30px var(--green), 0 0 60px rgba(63,185,80,.5); }
}
.pulse-info { font-size:13px; color:var(--text2); line-height:1.6; }
.pulse-info .val { color:var(--text); font-weight:600; }

/* Heartbeat stats */
.hb-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.hb-card { background:var(--surface2); border-radius:6px; padding:10px; text-align:center; }
.hb-card .val { font-size:22px; font-weight:700; color:var(--accent); }
.hb-card .label { font-size:11px; color:var(--text2); margin-top:2px; }
.hb-card.wide { grid-column:1/-1; }

/* Heartbeat config */
.hb-config { display:flex; gap:12px; margin-top:8px; flex-wrap:wrap; }
.hb-config-item { background:var(--surface2); border-radius:6px; padding:6px 10px; font-size:11px; color:var(--text2); }
.hb-config-item .cv { color:var(--accent); font-weight:600; }

/* Timeline */
.hb-timeline { display:flex; gap:3px; align-items:center; justify-content:center; margin-top:6px; flex-wrap:wrap; }
.hb-timeline .tick { width:10px; height:10px; border-radius:50%; transition:background .3s; cursor:default; }
.tick-normal { background:var(--green); }
.tick-significant { background:var(--yellow); }
.tick-alert { background:var(--red); }
.pulse-anim { animation:pulse-tick 2s ease-in-out infinite; }
@keyframes pulse-tick { 0%,100%{box-shadow:none} 50%{box-shadow:0 0 8px var(--green)} }

/* System meters */
.sys-meters { display:flex; gap:16px; align-items:flex-end; justify-content:center; padding:10px 0; }
.sys-vbar { display:flex; flex-direction:column; align-items:center; gap:4px; }
.sys-vbar-track { width:32px; height:100px; background:var(--surface2); border-radius:4px; position:relative; overflow:hidden; }
.sys-vbar-fill { position:absolute; bottom:0; width:100%; border-radius:0 0 4px 4px; transition:height .5s ease, background .3s; }
.sys-vbar-label { font-size:11px; color:var(--text2); }
.sys-vbar-val { font-size:12px; font-weight:600; }

/* Emotion */
.emo-row { display:flex; align-items:center; margin-bottom:8px; gap:8px; }
.emo-row .name { width:80px; font-size:12px; color:var(--text2); }
.emo-row .bar { flex:1; height:8px; background:var(--surface2); border-radius:4px; }
.emo-row .bar .fill { height:100%; border-radius:4px; transition:width .5s; }
.emo-row .val { width:36px; text-align:right; font-size:12px; font-weight:600; }

/* Quick stats */
.quick-stats { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
.qs-card { background:var(--surface2); border-radius:6px; padding:10px; text-align:center; }
.qs-card .val { font-size:18px; font-weight:700; }
.qs-card .label { font-size:11px; color:var(--text2); margin-top:2px; }

/* Activity feed */
.activity-feed { overflow-y:auto; font-size:12px; font-family:'Cascadia Code','Fira Code',monospace; }
.activity-line { padding:2px 0; color:var(--text2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.activity-line .ts { color:var(--border); margin-right:6px; }

/* ── Chat page — Live2D main + side panel ── */
.chat-layout { display:grid; grid-template-columns:1fr 320px; gap:0; height:calc(100vh - 80px); }
.chat-layout.collapsed { grid-template-columns:1fr 0; }
.chat-main { display:flex; flex-direction:column; min-height:0; border-right:1px solid var(--border); }

/* Live2D fills main area */
.live2d-container { flex:1; background:linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); position:relative; overflow:hidden; min-height:0; }
.live2d-container canvas { width:100% !important; height:100% !important; }

/* Speech bubble floating over Live2D */
.speech-bubble { position:absolute; top:12px; left:12px; right:12px; max-width:80%; background:rgba(255,255,255,0.12); backdrop-filter:blur(12px); border:1px solid rgba(255,255,255,0.15); border-radius:16px; padding:12px 16px; color:#fff; font-size:14px; line-height:1.6; word-wrap:break-word; white-space:pre-wrap; animation:bubbleIn 0.3s ease; z-index:10; max-height:40%; overflow-y:auto; }
.speech-bubble::after { content:''; position:absolute; bottom:-8px; left:30px; width:16px; height:16px; background:rgba(255,255,255,0.12); border:1px solid rgba(255,255,255,0.15); border-top:none; border-left:none; transform:rotate(45deg); backdrop-filter:blur(12px); }
@keyframes bubbleIn { from{opacity:0;transform:translateY(-10px)} to{opacity:1;transform:translateY(0)} }

/* Emotion badge */
.emotion-badge { position:absolute; top:8px; right:8px; background:rgba(0,0,0,0.5); color:#fff; padding:4px 10px; border-radius:12px; font-size:12px; backdrop-filter:blur(4px); z-index:10; }

/* Chat input */
.chat-input { display:flex; border-top:1px solid var(--border); flex-shrink:0; background:var(--surface); }
.chat-input input { flex:1; background:var(--surface2); border:none; color:var(--text); padding:12px 14px; font-size:14px; outline:none; }
.chat-input input::placeholder { color:var(--text2); }
.chat-input button { background:var(--accent); color:#fff; border:none; padding:12px 20px; font-weight:600; cursor:pointer; }
.chat-input button:hover { opacity:.85; }
.upload-btn { cursor:pointer; padding:8px 12px; color:var(--text2); display:flex; align-items:center; border-left:1px solid var(--border); transition:.2s; }
.upload-btn:hover { color:var(--accent); background:var(--surface2); }

/* Voice buttons */
.voice-btn { background:transparent; border:none; color:var(--text2); padding:8px 10px; cursor:pointer; display:flex; align-items:center; border-right:1px solid var(--border); transition:.2s; }
.voice-btn:hover { color:var(--accent); background:var(--surface2); }
.voice-btn.active { color:var(--green); background:var(--surface2); }
.voice-btn.recording { color:var(--red); animation:pulse 1s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }

/* Side panel: history + activity */
.chat-side { display:flex; flex-direction:column; min-height:0; overflow:hidden; transition:width .15s; }
.chat-side-header { background:var(--surface2); padding:8px 14px; font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.5px; color:var(--text2); border-bottom:1px solid var(--border); display:flex; justify-content:space-between; align-items:center; flex-shrink:0; }
.chat-side-body { flex:1; overflow-y:auto; display:flex; flex-direction:column; }
.chat-messages { flex:1; overflow-y:auto; padding:8px; display:flex; flex-direction:column; gap:4px; min-height:0; }
.msg { max-width:95%; padding:6px 10px; border-radius:8px; font-size:12px; line-height:1.5; word-wrap:break-word; white-space:pre-wrap; }
.msg.user { align-self:flex-end; background:var(--accent); color:#fff; border-bottom-right-radius:2px; }
.msg.agent { align-self:flex-start; background:var(--surface2); border-bottom-left-radius:2px; }
.msg.agent pre { background:var(--bg); border:1px solid var(--border); border-radius:4px; padding:4px; margin:4px 0; overflow-x:auto; font-size:11px; }
.msg.agent code { font-family:'Cascadia Code','Fira Code',monospace; font-size:11px; }
.msg.system { align-self:center; background:transparent; color:var(--text2); font-size:10px; font-style:italic; }
.side-divider { background:var(--surface2); padding:4px 8px; font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:.5px; color:var(--text2); border-top:1px solid var(--border); border-bottom:1px solid var(--border); flex-shrink:0; }
.activity-feed-side { overflow-y:auto; max-height:200px; padding:4px 8px; font-size:11px; }
.chat-toggle { cursor:pointer; font-size:16px; color:var(--text2); }
.chat-toggle:hover { color:var(--text); }

/* Typing indicator */
.typing-indicator { display:none; align-items:center; gap:6px; padding:6px 14px; font-size:12px; color:var(--text2); flex-shrink:0; }
.typing-dot { width:6px; height:6px; background:var(--text2); border-radius:50%; animation:typingBounce 1.2s infinite; }
.typing-dot:nth-child(2) { animation-delay:.2s; }
.typing-dot:nth-child(3) { animation-delay:.4s; }
@keyframes typingBounce { 0%,80%,100%{opacity:.3;transform:scale(.8)} 40%{opacity:1;transform:scale(1)} }

/* ── Usage page ── */
.usage-top { display:grid; grid-template-columns:repeat(5, 1fr); gap:12px; margin-bottom:16px; }
.usage-card { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:14px; text-align:center; }
.usage-card .val { font-size:24px; font-weight:700; }
.usage-card .label { font-size:11px; color:var(--text2); margin-top:4px; }

.usage-table { width:100%; font-size:12px; border-collapse:collapse; }
.usage-table th { text-align:left; color:var(--text2); font-weight:600; padding:6px 8px; border-bottom:1px solid var(--border); cursor:pointer; user-select:none; white-space:nowrap; }
.usage-table th:hover { color:var(--accent); }
.usage-table th .sort-arrow { font-size:10px; margin-left:2px; }
.usage-table td { padding:5px 8px; border-bottom:1px solid var(--border); white-space:nowrap; }
.usage-table .model-haiku { color:var(--green); }
.usage-table .model-sonnet { color:var(--accent); }
.usage-table .model-opus { color:var(--purple); }
.usage-table .fail { color:var(--red); }
.usage-table-wrap { max-height:calc(100vh - 380px); overflow-y:auto; margin-bottom:16px; }

.usage-bottom { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.summary-table { width:100%; font-size:12px; border-collapse:collapse; }
.summary-table th { text-align:left; color:var(--text2); font-weight:600; padding:5px 8px; border-bottom:1px solid var(--border); }
.summary-table td { padding:5px 8px; border-bottom:1px solid var(--border); }

/* ── Settings page ── */
.settings-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.settings-grid .panel { min-height:0; }
.auth-row { display:flex; justify-content:space-between; padding:6px 0; border-bottom:1px solid var(--border); font-size:13px; align-items:center; }
.auth-row:last-child { border:none; }
.auth-row .k { color:var(--text2); }
.auth-row .v { color:var(--text); font-weight:500; max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
.badge.oauth { background:#1a3a2a; color:var(--green); }
.badge.apikey { background:#3a2a1a; color:var(--yellow); }
.badge.model { background:#1a2a3a; color:var(--accent); }
.model-row { display:flex; align-items:center; gap:10px; margin-bottom:8px; }
.model-row .tier-label { width:50px; font-size:12px; color:var(--text2); font-weight:600; }
.model-select { background:var(--surface2); color:var(--text); border:1px solid var(--border); border-radius:4px; padding:5px 8px; font-size:12px; flex:1; cursor:pointer; outline:none; }
.model-select:focus { border-color:var(--accent); }
.save-btn { background:var(--accent); color:#fff; border:none; border-radius:6px; padding:8px 16px; font-size:12px; font-weight:600; cursor:pointer; width:100%; margin-top:8px; }
.save-btn:hover { opacity:.85; }

.tool-card { display:inline-flex; flex-direction:column; background:var(--surface2); border:1px solid var(--border); border-radius:8px; padding:8px 12px; margin:4px; min-width:140px; }
.tool-card .tool-name { font-size:12px; font-weight:600; margin-bottom:2px; }
.tool-card .tool-desc { font-size:11px; color:var(--text2); margin-bottom:4px; max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tool-chip .risk { font-size:10px; margin-left:4px; }
.risk-badge { display:inline-block; padding:1px 6px; border-radius:8px; font-size:10px; font-weight:600; }
.risk-SAFE, .risk-LOW { background:#1a3a2a; color:var(--green); }
.risk-MEDIUM { background:#3a2a1a; color:var(--yellow); }
.risk-HIGH { background:#3a1a1a; color:var(--red); }

.ctrl-btn { display:block; width:100%; padding:10px; margin-bottom:8px; border:1px solid var(--border); border-radius:6px; background:var(--surface2); color:var(--text); font-size:13px; cursor:pointer; text-align:center; transition:.15s; }
.ctrl-btn:hover { background:var(--border); }
.ctrl-btn.danger { border-color:var(--red); color:var(--red); }
.ctrl-btn.danger:hover { background:rgba(248,81,73,.15); }
.ctrl-btn.warn { border-color:var(--yellow); color:var(--yellow); }
.ctrl-feedback { font-size:12px; color:var(--green); text-align:center; margin-top:4px; min-height:18px; }

.config-raw { background:var(--bg); border:1px solid var(--border); border-radius:4px; padding:10px; font-family:'Cascadia Code','Fira Code',monospace; font-size:11px; color:var(--text2); white-space:pre-wrap; word-break:break-all; max-height:250px; overflow-y:auto; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width:6px; height:6px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:var(--border); border-radius:3px; }

/* ── Responsive ── */
@media (max-width:900px) {
  .sidebar { position:fixed; bottom:0; left:0; top:auto; width:100%; height:48px; flex-direction:row; border-right:none; border-top:1px solid var(--border); padding-bottom:env(safe-area-inset-bottom, 0px); z-index:200; }
  .nav-item { flex:1; height:48px; border-left:none; border-top:3px solid transparent; }
  .nav-item.active { border-left-color:transparent; border-top-color:var(--accent); }
  .nav-item .tooltip { display:none; }
  .nav-spacer { display:none; }
  .header { margin-left:0; }
  .main { margin-left:0; height:calc(100vh - 48px - 48px - env(safe-area-inset-bottom, 0px)); overflow-y:auto; }
  :root { --sidebar-w:0px; }
  .overview-grid { grid-template-columns:1fr; height:auto; padding-bottom:60px; }
  .overview-left, .overview-right { overflow-y:visible; }
  .page { overflow-y:auto; height:100%; padding-bottom:calc(16px + env(safe-area-inset-bottom, 0px)); }
  #page-chat { overflow:hidden; padding:0; height:calc(100vh - 48px - 48px - env(safe-area-inset-bottom, 0px)); }
  .chat-layout { grid-template-columns:1fr; height:100%; display:flex; flex-direction:column; }
  .chat-main { flex:1; display:flex; flex-direction:column; min-height:0; }
  .live2d-container { flex:1; min-height:0; }
  .speech-bubble { max-width:90%; font-size:13px; max-height:35%; }
  .chat-input { flex-shrink:0; padding-bottom:calc(56px + env(safe-area-inset-bottom, 0px)); }
  .chat-side { display:none; }
  .usage-top { grid-template-columns:repeat(3, 1fr); }
  .usage-bottom { grid-template-columns:1fr; }
  .settings-grid { grid-template-columns:1fr; }
}
@media (max-width:600px) {
  .header h1 { font-size:14px; }
  .header .meta { font-size:11px; gap:6px; }
  .panel-body { padding:8px; }
  .overview-grid { gap:8px; }
  .hb-grid { grid-template-columns:1fr 1fr; gap:4px; }
  .hb-card { padding:6px; }
  .hb-card .val { font-size:18px; }
  .usage-top { grid-template-columns:1fr 1fr; gap:6px; }
  .usage-card .val { font-size:16px; }
  .msg { max-width:92%; font-size:13px; padding:8px 10px; }
  .chat-input input { padding:10px; font-size:14px; }
  .chat-input button { padding:10px 14px; font-size:13px; }
  .upload-btn { padding:6px 8px; }
  .auth-row { font-size:12px; flex-wrap:wrap; }
  .auth-row .v { max-width:150px; font-size:11px; }
  .model-select { font-size:11px; }
  .ctrl-btn { padding:10px; font-size:13px; }
  .tool-card { min-width:100px; padding:6px 8px; }
  .activity-feed { font-size:11px; max-height:100px; }
  .usage-table { font-size:10px; }
  .usage-table th, .usage-table td { padding:3px 4px; }
  .wm-item { font-size:11px; }
  body { -webkit-text-size-adjust:100%; }
}
</style>
</head>
<body>

<!-- Sidebar -->
<nav class="sidebar">
  <a class="nav-item active" data-page="overview" href="#/">
    &#x1F3E0;<span class="tooltip">Overview</span>
  </a>
  <a class="nav-item" data-page="chat" href="#/chat">
    &#x1F4AC;<span class="tooltip">Chat</span>
  </a>
  <a class="nav-item" data-page="usage" href="#/usage">
    &#x1F4CA;<span class="tooltip">Usage</span>
  </a>
  <a class="nav-item" data-page="network" href="#/network">
    &#x1F310;<span class="tooltip">Network</span>
  </a>
  <a class="nav-item" data-page="settings" href="#/settings">
    &#x2699;<span class="tooltip">Settings</span>
  </a>
  <div class="nav-spacer"></div>
</nav>

<!-- Header -->
<div class="header">
  <h1><span class="name" id="agent-name">ANIMA</span> <span>Dashboard</span></h1>
  <div class="meta">
    <span><span class="status-dot alive" id="status-dot"></span><span id="status-text">connecting...</span></span>
    <span>&middot; Uptime: <span id="uptime">--</span></span>
    <span>&middot; <span class="page-indicator" id="page-indicator">Overview</span></span>
  </div>
</div>

<!-- Main content -->
<div class="main">

  <!-- ══ OVERVIEW PAGE ══ -->
  <div class="page active" id="page-overview">
    <div class="overview-grid">
      <div class="overview-left">
        <!-- Heartbeat -->
        <div class="panel">
          <div class="panel-title">Heartbeat</div>
          <div class="panel-body">
            <div class="pulse-container">
              <div class="pulse-circle alive" id="pulse-circle"></div>
              <div class="pulse-info">
                <div>Ticks: <span class="val" id="hb-ticks">0</span></div>
                <div>Interval: <span class="val" id="hb-interval">15s</span></div>
                <div>Status: <span class="val" id="hb-status">--</span></div>
                <div>LLM Skips: <span class="val" id="hb-llm-skips">0</span></div>
              </div>
            </div>
            <div class="hb-config" id="hb-config"></div>
            <div style="margin-top:10px;">
              <div style="font-size:11px;color:var(--text2);margin-bottom:6px;text-align:center">Last 30 Ticks</div>
              <div class="hb-timeline" id="hb-timeline"></div>
            </div>
          </div>
        </div>

        <!-- Activity Feed -->
        <div class="panel" style="flex:1;min-height:200px">
          <div class="panel-title">Activity Feed <span id="queue-size-ov" style="font-size:11px;color:var(--text2)">Queue: 0</span></div>
          <div class="panel-body activity-feed" id="activity-feed-ov" style="max-height:none">
            <div class="activity-line" style="color:var(--text2);font-style:italic">Waiting for activity...</div>
          </div>
        </div>
      </div>

      <div class="overview-right">
        <!-- System -->
        <div class="panel">
          <div class="panel-title">System <span style="font-size:11px;color:var(--text2)">Processes: <span id="sys-proc">--</span></span></div>
          <div class="panel-body">
            <div class="sys-meters">
              <div class="sys-vbar">
                <div class="sys-vbar-val" id="sys-cpu-v">--%</div>
                <div class="sys-vbar-track"><div class="sys-vbar-fill" id="cpu-bar" style="height:0%"></div></div>
                <div class="sys-vbar-label">CPU</div>
              </div>
              <div class="sys-vbar">
                <div class="sys-vbar-val" id="sys-mem-v">--%</div>
                <div class="sys-vbar-track"><div class="sys-vbar-fill" id="mem-bar" style="height:0%"></div></div>
                <div class="sys-vbar-label">MEM</div>
              </div>
              <div class="sys-vbar">
                <div class="sys-vbar-val" id="sys-disk-v">--%</div>
                <div class="sys-vbar-track"><div class="sys-vbar-fill" id="disk-bar" style="height:0%"></div></div>
                <div class="sys-vbar-label">DISK</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Emotion -->
        <div class="panel">
          <div class="panel-title">Emotion</div>
          <div class="panel-body">
            <div class="emo-row"><span class="name">Engagement</span><div class="bar"><div class="fill" id="emo-eng" style="width:50%;background:var(--accent)"></div></div><span class="val" id="emo-eng-v">0.50</span></div>
            <div class="emo-row"><span class="name">Confidence</span><div class="bar"><div class="fill" id="emo-conf" style="width:60%;background:var(--green)"></div></div><span class="val" id="emo-conf-v">0.60</span></div>
            <div class="emo-row"><span class="name">Curiosity</span><div class="bar"><div class="fill" id="emo-cur" style="width:70%;background:var(--purple)"></div></div><span class="val" id="emo-cur-v">0.70</span></div>
            <div class="emo-row"><span class="name">Concern</span><div class="bar"><div class="fill" id="emo-con" style="width:20%;background:var(--red)"></div></div><span class="val" id="emo-con-v">0.20</span></div>
          </div>
        </div>

        <!-- Quick Stats -->
        <div class="panel">
          <div class="panel-title">Quick Stats</div>
          <div class="panel-body">
            <div class="quick-stats">
              <div class="qs-card"><div class="val" id="qs-calls" style="color:var(--accent)">0</div><div class="label">LLM Calls</div></div>
              <div class="qs-card"><div class="val" id="qs-tokens" style="color:var(--green)">0</div><div class="label">Tokens</div></div>
              <div class="qs-card"><div class="val" id="qs-cost" style="color:var(--yellow)">$0</div><div class="label">Cost</div></div>
              <div class="qs-card"><div class="val" id="qs-queue" style="color:var(--text2)">0</div><div class="label">Queue</div></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ══ CHAT PAGE — Live2D main + speech bubble + side panel ══ -->
  <div class="page" id="page-chat">
    <div class="chat-layout" id="chat-layout">
      <!-- Main: Live2D full area + floating speech bubble -->
      <div class="chat-main">
        <div class="live2d-container" id="live2d-container">
          <canvas id="live2d-canvas"></canvas>
          <!-- Speech bubble overlay -->
          <div class="speech-bubble" id="speech-bubble" style="display:none">
            <div class="speech-bubble-text" id="speech-bubble-text"></div>
          </div>
          <!-- Emotion badge -->
          <div class="emotion-badge" id="emotion-badge"></div>
        </div>
        <!-- Input bar at bottom -->
        <div class="chat-input">
          <button class="voice-btn" id="voice-toggle" title="Toggle voice (TTS)">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07"/></svg>
          </button>
          <button class="voice-btn" id="mic-btn" title="Voice input (STT)">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
          </button>
          <input type="text" id="chat-input" placeholder="Type a message..." autocomplete="off" />
          <label class="upload-btn" title="Upload file">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>
            <input type="file" id="file-upload" style="display:none" multiple />
          </label>
          <button id="chat-send">Send</button>
        </div>
      </div>
      <!-- Right side: chat history + activity -->
      <div class="chat-side" id="chat-side">
        <div class="chat-side-header">
          History <span class="chat-toggle" id="chat-toggle" title="Toggle panel">&#x25B6;</span>
        </div>
        <div class="chat-side-body">
          <div class="chat-messages" id="chat-messages">
            <div class="msg system">Chat history</div>
          </div>
          <div class="side-divider">Activity</div>
          <div class="activity-feed-side" id="activity-feed-chat">
            <div class="activity-line" style="color:var(--text2);font-style:italic">Waiting...</div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <!-- Live2D SDK (Cubism Core + PixiJS + pixi-live2d-display) -->
  <script src="/static/live2dcubismcore.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/pixi.js@7.x/dist/pixi.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js"></script>
  <!-- TTS audio element -->
  <audio id="tts-audio" style="display:none"></audio>

  <!-- ══ USAGE PAGE ══ -->
  <div class="page" id="page-usage">
    <div class="usage-top">
      <div class="usage-card"><div class="val" id="u-calls" style="color:var(--accent)">0</div><div class="label">Total Calls</div></div>
      <div class="usage-card"><div class="val" id="u-tokens" style="color:var(--green)">0</div><div class="label">Total Tokens</div></div>
      <div class="usage-card"><div class="val" id="u-prompt" style="color:var(--text2)">0</div><div class="label">Prompt Tokens</div></div>
      <div class="usage-card"><div class="val" id="u-completion" style="color:var(--text2)">0</div><div class="label">Completion Tokens</div></div>
      <div class="usage-card"><div class="val" id="u-cost" style="color:var(--yellow)">$0</div><div class="label">Total Cost</div></div>
    </div>

    <div class="panel" style="margin-bottom:16px">
      <div class="panel-title">Usage History</div>
      <div class="usage-table-wrap" id="usage-table-wrap">
        <table class="usage-table" id="usage-table">
          <thead><tr>
            <th data-col="timestamp">Time <span class="sort-arrow"></span></th>
            <th data-col="model">Model <span class="sort-arrow"></span></th>
            <th data-col="tier">Tier <span class="sort-arrow"></span></th>
            <th data-col="auth_mode">Auth <span class="sort-arrow"></span></th>
            <th data-col="prompt_tokens" data-num="1">Prompt <span class="sort-arrow"></span></th>
            <th data-col="completion_tokens" data-num="1">Completion <span class="sort-arrow"></span></th>
            <th data-col="total_tokens" data-num="1">Total <span class="sort-arrow"></span></th>
            <th data-col="estimated_cost_usd" data-num="1">Cost <span class="sort-arrow"></span></th>
            <th data-col="success">Status <span class="sort-arrow"></span></th>
          </tr></thead>
          <tbody id="usage-tbody"></tbody>
        </table>
      </div>
    </div>

    <div class="usage-bottom">
      <div class="panel">
        <div class="panel-title">By Model</div>
        <div class="panel-body">
          <table class="summary-table">
            <thead><tr><th>Model</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>
            <tbody id="by-model-tbody"></tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="panel-title">By Day</div>
        <div class="panel-body">
          <table class="summary-table">
            <thead><tr><th>Date</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>
            <tbody id="by-day-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- ══ NETWORK PAGE ══ -->
  <div class="page" id="page-network">
    <div class="settings-grid" style="grid-template-columns:1fr 1fr">
      <!-- This Node -->
      <div class="panel">
        <div class="panel-title">This Node</div>
        <div class="panel-body">
          <div class="auth-row"><span class="k">Node ID</span><span class="v" id="net-node-id">--</span></div>
          <div class="auth-row"><span class="k">Status</span><span class="v"><span class="badge oauth" id="net-status">offline</span></span></div>
          <div class="auth-row"><span class="k">Alive Nodes</span><span class="v" id="net-alive">0</span></div>
        </div>
      </div>
      <!-- Topology -->
      <div class="panel">
        <div class="panel-title">Topology</div>
        <div class="panel-body" id="net-topology" style="min-height:120px;position:relative">
          <div style="color:var(--text2);font-size:12px;text-align:center;padding:20px">Network disabled</div>
        </div>
      </div>
      <!-- Peers Table -->
      <div class="panel" style="grid-column:1/-1">
        <div class="panel-title">Peers</div>
        <div class="panel-body">
          <table class="usage-table" style="width:100%">
            <thead><tr><th>Node</th><th>Host</th><th>IP</th><th>Status</th><th>Load</th><th>Capabilities</th></tr></thead>
            <tbody id="net-peers-tbody"><tr><td colspan="6" style="color:var(--text2)">No peers</td></tr></tbody>
          </table>
        </div>
      </div>
      <!-- Sessions -->
      <div class="panel" style="grid-column:1/-1">
        <div class="panel-title">Sessions</div>
        <div class="panel-body" id="net-sessions" style="font-size:12px;color:var(--text2)">No active sessions</div>
      </div>
    </div>
  </div>

  <!-- ══ SETTINGS PAGE ══ -->
  <div class="page" id="page-settings">
    <div class="settings-grid">
      <!-- Auth -->
      <div class="panel">
        <div class="panel-title">Authentication</div>
        <div class="panel-body">
          <div class="auth-row"><span class="k">Mode</span><span class="v"><span class="badge oauth" id="auth-badge">--</span></span></div>
          <div class="auth-row"><span class="k">Source</span><span class="v" id="auth-source">--</span></div>
          <div class="auth-row"><span class="k">Token</span><span class="v" id="auth-token" style="font-family:monospace;font-size:11px">--</span></div>
          <div class="auth-row"><span class="k">CLI Version</span><span class="v" id="auth-cli-ver">--</span></div>
          <div class="auth-row"><span class="k">Provider</span><span class="v" id="auth-provider">--</span></div>
        </div>
      </div>

      <!-- Models -->
      <div class="panel">
        <div class="panel-title">Models</div>
        <div class="panel-body">
          <div class="model-row">
            <span class="tier-label">Tier1</span>
            <select class="model-select" id="sel-tier1">
              <option value="claude-haiku-4-5-20251001">claude-haiku-4-5-20251001</option>
              <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
              <option value="claude-opus-4-6">claude-opus-4-6</option>
            </select>
          </div>
          <div class="model-row">
            <span class="tier-label">Tier2</span>
            <select class="model-select" id="sel-tier2">
              <option value="claude-haiku-4-5-20251001">claude-haiku-4-5-20251001</option>
              <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
              <option value="claude-opus-4-6">claude-opus-4-6</option>
            </select>
          </div>
          <div class="model-row">
            <span class="tier-label">Opus</span>
            <select class="model-select" id="sel-opus">
              <option value="claude-haiku-4-5-20251001">claude-haiku-4-5-20251001</option>
              <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
              <option value="claude-opus-4-6">claude-opus-4-6</option>
            </select>
          </div>
          <button class="save-btn" id="save-models">Save Models</button>
        </div>
      </div>

      <!-- Tools -->
      <div class="panel">
        <div class="panel-title">Tools</div>
        <div class="panel-body" id="tools-list" style="max-height:300px;overflow-y:auto"></div>
      </div>

      <!-- Controls -->
      <div class="panel">
        <div class="panel-title">Controls</div>
        <div class="panel-body">
          <button class="ctrl-btn" onclick="control('pause_heartbeat')">&#x23F8; Pause Heartbeat</button>
          <button class="ctrl-btn" onclick="control('resume_heartbeat')">&#x25B6; Resume Heartbeat</button>
          <button class="ctrl-btn warn" onclick="control('clear_working_memory')">&#x1F5D1; Clear Working Memory</button>
          <button class="ctrl-btn" onclick="if(confirm('Restart ANIMA?'))control('restart')">&#x1F504; Restart</button>
          <button class="ctrl-btn danger" onclick="if(confirm('Are you sure you want to shutdown ANIMA?'))control('shutdown')">&#x23FB; Shutdown</button>
          <div class="ctrl-feedback" id="ctrl-feedback"></div>
        </div>
      </div>

      <!-- Config -->
      <div class="panel" style="grid-column:1/-1">
        <div class="panel-title">Configuration <button style="background:var(--surface2);color:var(--text2);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer" onclick="refreshConfig()">Refresh</button></div>
        <div class="panel-body">
          <div class="config-raw" id="config-raw">Loading configuration...</div>
        </div>
      </div>
    </div>
  </div>

</div>

<script>
/* ═══════════════════════════════════════════════════════════
   ANIMA Dashboard — Multi-page SPA
   ═══════════════════════════════════════════════════════════ */

let ws = null;
let lastData = null;
let modelSyncDone = false;
let lastChatLen = 0;
let isTyping = false;
let currentPage = 'overview';
let usageSortCol = 'timestamp';
let usageSortAsc = false;
let cachedUsageHistory = [];

// ── Routing ──

const routes = {
  '#/': 'overview',
  '#/chat': 'chat',
  '#/usage': 'usage',
  '#/network': 'network',
  '#/settings': 'settings',
};
const pageNames = { overview:'Overview', chat:'Chat', usage:'Usage', network:'Network', settings:'Settings' };

function navigate() {
  const hash = location.hash || '#/';
  currentPage = routes[hash] || 'overview';

  // Update nav
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('active', n.dataset.page === currentPage);
  });

  // Update pages
  document.querySelectorAll('.page').forEach(p => {
    p.classList.toggle('active', p.id === 'page-' + currentPage);
  });

  // Update header indicator
  document.getElementById('page-indicator').textContent = pageNames[currentPage] || 'Overview';

  // Re-render current page if we have data
  if (lastData) render(lastData);
}

window.addEventListener('hashchange', navigate);
window.addEventListener('load', navigate);

// ── WebSocket ──

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onmessage = function(e) { lastData = JSON.parse(e.data); render(lastData); };
  ws.onclose = function() {
    document.getElementById('status-text').textContent = 'disconnected';
    document.getElementById('status-dot').className = 'status-dot stopped';
    modelSyncDone = false;
    setTimeout(connect, 3000);
  };
  ws.onerror = function() { ws.close(); };
}

// ── Main render dispatcher ──

function render(d) {
  renderHeader(d);
  if (currentPage === 'overview') renderOverview(d);
  else if (currentPage === 'chat') renderChat(d);
  else if (currentPage === 'usage') renderUsage(d);
  else if (currentPage === 'network') renderNetwork(d);
  else if (currentPage === 'settings') renderSettings(d);
  // Update Live2D expression from emotion
  if (d.emotion) updateLive2DExpression(d.emotion);
}

// ── Header (always rendered) ──

function renderHeader(d) {
  var a = d.agent || {};
  document.getElementById('agent-name').textContent = (a.name || 'ANIMA').toUpperCase();
  document.title = (a.name || 'ANIMA').toUpperCase() + ' Dashboard';
  document.getElementById('status-text').textContent = a.status || '--';
  document.getElementById('status-dot').className = 'status-dot ' + (a.status === 'alive' ? 'alive' : 'stopped');
  document.getElementById('uptime').textContent = fmtUptime(d.uptime_s || 0);
}

// ── Page 1: Overview ──

function renderOverview(d) {
  var a = d.agent || {};
  var hb = d.heartbeat || {};

  // Pulse circle
  var pc = document.getElementById('pulse-circle');
  pc.className = 'pulse-circle ' + (a.status === 'alive' ? 'alive' : 'stopped');

  // Heartbeat info
  document.getElementById('hb-ticks').textContent = hb.tick_count != null ? hb.tick_count : '--';
  document.getElementById('hb-interval').textContent = (hb.script_interval_s || 15) + 's';
  document.getElementById('hb-status').textContent = hb.status || '--';
  document.getElementById('hb-llm-skips').textContent = hb.consecutive_llm_skips != null ? hb.consecutive_llm_skips : 0;

  // Heartbeat config
  var hbConfig = document.getElementById('hb-config');
  hbConfig.innerHTML = '<div class="hb-config-item">Script: <span class="cv">' + (hb.script_interval_s || 15) + 's</span></div>' +
    '<div class="hb-config-item">LLM: <span class="cv">' + (hb.llm_interval_s || 300) + 's</span></div>' +
    '<div class="hb-config-item">Major: <span class="cv">' + (hb.major_interval_s || 3600) + 's</span></div>';

  // Timeline — last 30 ticks
  var ticks = hb.tick_history || [];
  var timeline = document.getElementById('hb-timeline');
  var last30 = ticks.slice(-30);
  var tlHtml = '';
  for (var i = 0; i < last30.length; i++) {
    var t = last30[i];
    var s = t.significance || 0;
    var cls = t.has_alerts ? 'tick-alert' : s > 0.3 ? 'tick-significant' : 'tick-normal';
    var isLast = i === last30.length - 1 && a.status === 'alive';
    var tip = '#' + t.tick + ' | CPU ' + (t.cpu || 0).toFixed(0) + '% MEM ' + (t.mem || 0).toFixed(0) + '% | sig ' + s.toFixed(2) + (t.file_changes ? ' | files:' + t.file_changes : '') + (t.has_alerts ? ' | ALERT' : '');
    tlHtml += '<div class="tick ' + cls + (isLast ? ' pulse-anim' : '') + '" title="' + esc(tip) + '"></div>';
  }
  timeline.innerHTML = tlHtml || '<span style="font-size:11px;color:var(--text2)">Waiting for first heartbeat...</span>';

  // System (vertical bars)
  var sys = d.system || {};
  setVBar(sys.cpu_percent, 'sys-cpu-v', 'cpu-bar');
  setVBar(sys.memory_percent, 'sys-mem-v', 'mem-bar');
  setVBar(sys.disk_percent, 'sys-disk-v', 'disk-bar');
  document.getElementById('sys-proc').textContent = sys.process_count != null ? sys.process_count : '--';

  // Emotion
  var emo = d.emotion || {};
  setEmo('eng', emo.engagement); setEmo('conf', emo.confidence);
  setEmo('cur', emo.curiosity); setEmo('con', emo.concern);

  // Quick stats
  var summary = d.llm_usage_summary || {};
  var u = d.usage || {};
  document.getElementById('qs-calls').textContent = summary.total_calls || u.calls || 0;
  document.getElementById('qs-tokens').textContent = fmtNum(summary.total_tokens || u.total_tokens || 0);
  var cost = summary.total_cost_usd || 0;
  document.getElementById('qs-cost').textContent = '$' + cost.toFixed(4);
  document.getElementById('qs-queue').textContent = (d.event_queue || {}).size || 0;
  document.getElementById('queue-size-ov').textContent = 'Queue: ' + ((d.event_queue || {}).size || 0);

  // Activity feed (overview)
  renderActivityFeed(d.activity || [], 'activity-feed-ov', 30);
}

// ── Page 2: Chat ──

var speechBubbleTimer = null;

function renderChat(d) {
  var history = d.chat_history || [];

  if (history.length !== lastChatLen) {
    var prevLen = lastChatLen;
    lastChatLen = history.length;

    // 1. Update side panel chat history
    var el = document.getElementById('chat-messages');
    var html = '';
    for (var i = 0; i < history.length; i++) {
      var m = history[i];
      var role = m.role === 'user' ? 'user' : m.role === 'system' ? 'system' : 'agent';
      var content = role === 'agent' ? formatAgentMsg(m.content) : esc(m.content);
      html += '<div class="msg ' + role + '">' + content + '</div>';
    }
    el.innerHTML = html;
    el.scrollTop = el.scrollHeight;

    // 2. Show speech bubble for new agent message
    if (history.length > prevLen) {
      var lastMsg = history[history.length - 1];
      if (lastMsg && lastMsg.role !== 'user' && lastMsg.role !== 'system') {
        showSpeechBubble(lastMsg.content);
      }
    }
  }

  // Activity feed (side panel)
  renderActivityFeed(d.activity || [], 'activity-feed-chat', 30);

  // Emotion badge
  if (d.emotion) {
    var badge = document.getElementById('emotion-badge');
    if (badge) {
      var emoName = getDominantEmotion(d.emotion);
      var emojiMap = {happy:'😊', excited:'🤩', sad:'😢', angry:'😠', curious:'🤔', embarrassed:'😳', sleepy:'😴', neutral:'😐', confident:'😤', worried:'😟'};
      badge.textContent = (emojiMap[emoName] || '😐') + ' ' + emoName;
    }
  }

  // Thinking indicator in speech bubble
  var activity = d.activity || [];
  var lastActivity = activity.length > 0 ? activity[activity.length - 1] : null;
  var isActive = lastActivity && (lastActivity.stage === 'thinking' || lastActivity.stage === 'executing' || lastActivity.stage === 'responding');
  if (isActive && !document.getElementById('speech-bubble').style.display !== 'none') {
    var bubble = document.getElementById('speech-bubble');
    var bubbleText = document.getElementById('speech-bubble-text');
    if (bubble.style.display === 'none') {
      bubbleText.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span>';
      bubble.style.display = 'block';
    }
  }
}

function showSpeechBubble(text) {
  var bubble = document.getElementById('speech-bubble');
  var bubbleText = document.getElementById('speech-bubble-text');
  if (!bubble || !bubbleText) return;

  // Truncate for bubble display
  var short = text.length > 200 ? text.slice(0, 200) + '...' : text;
  bubbleText.textContent = short;
  bubble.style.display = 'block';

  // Auto-hide after 8 seconds
  if (speechBubbleTimer) clearTimeout(speechBubbleTimer);
  speechBubbleTimer = setTimeout(function() {
    bubble.style.display = 'none';
  }, 8000);
}

function formatAgentMsg(text) {
  // Process markdown BEFORE escaping (order matters!)
  // 1. Extract code blocks first (protect from further processing)
  var codeBlocks = [];
  var s = text.replace(/```(\w*)\n?([\s\S]*?)```/g, function(m, lang, code) {
    var i = codeBlocks.length;
    codeBlocks.push('<pre style="background:var(--surface2);padding:8px;border-radius:4px;overflow-x:auto;margin:4px 0"><code>' + esc(code) + '</code></pre>');
    return '\x00CB' + i + '\x00';
  });
  // 2. Extract inline code
  var inlineCodes = [];
  s = s.replace(/`([^`]+)`/g, function(m, code) {
    var i = inlineCodes.length;
    inlineCodes.push('<code style="background:var(--surface2);padding:1px 4px;border-radius:3px">' + esc(code) + '</code>');
    return '\x00IC' + i + '\x00';
  });
  // 3. Now escape the rest
  s = esc(s);
  // 4. Bold **...**
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // 5. Italic *...*  (not inside bold)
  s = s.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
  // 6. Headers
  s = s.replace(/^### (.+)$/gm, '<strong style="color:var(--accent)">$1</strong>');
  s = s.replace(/^## (.+)$/gm, '<strong style="color:var(--accent);font-size:15px">$1</strong>');
  // 7. Newlines
  s = s.replace(/\n/g, '<br>');
  // 8. Restore code blocks and inline code
  for (var i = 0; i < codeBlocks.length; i++) {
    s = s.replace('\x00CB' + i + '\x00', codeBlocks[i]);
  }
  for (var i = 0; i < inlineCodes.length; i++) {
    s = s.replace('\x00IC' + i + '\x00', inlineCodes[i]);
  }
  return s;
}

// ── Page 3: Usage ──

function renderUsage(d) {
  var summary = d.llm_usage_summary || {};
  var u = d.usage || {};

  // Top cards
  document.getElementById('u-calls').textContent = summary.total_calls || u.calls || 0;
  document.getElementById('u-tokens').textContent = fmtNum(summary.total_tokens || u.total_tokens || 0);
  document.getElementById('u-prompt').textContent = fmtNum(summary.total_prompt_tokens || u.prompt_tokens || 0);
  document.getElementById('u-completion').textContent = fmtNum(summary.total_completion_tokens || u.completion_tokens || 0);
  var cost = summary.total_cost_usd || 0;
  document.getElementById('u-cost').textContent = '$' + cost.toFixed(4);

  // Usage history table (ALL records)
  var history = d.llm_usage_history || [];
  if (history.length > 0) {
    cachedUsageHistory = history;
    renderUsageTable();
  }

  // By Model
  var byModel = summary.by_model || {};
  var bmHtml = '';
  for (var model in byModel) {
    var m = byModel[model];
    var cls = modelColorClass(model);
    bmHtml += '<tr><td class="' + cls + '">' + shortModelName(model) + '</td><td>' + (m.calls || 0) + '</td><td>' + fmtNum(m.total_tokens || 0) + '</td><td>$' + (m.cost_usd || 0).toFixed(4) + '</td></tr>';
  }
  document.getElementById('by-model-tbody').innerHTML = bmHtml || '<tr><td colspan="4" style="color:var(--text2)">No data</td></tr>';

  // By Day
  var byDay = summary.by_day || {};
  var bdHtml = '';
  for (var day in byDay) {
    var dd = byDay[day];
    bdHtml += '<tr><td>' + day + '</td><td>' + (dd.calls || 0) + '</td><td>' + fmtNum(dd.total_tokens || 0) + '</td><td>$' + (dd.cost_usd || 0).toFixed(4) + '</td></tr>';
  }
  document.getElementById('by-day-tbody').innerHTML = bdHtml || '<tr><td colspan="4" style="color:var(--text2)">No data</td></tr>';
}

function renderUsageTable() {
  var sorted = cachedUsageHistory.slice();
  var col = usageSortCol;
  var asc = usageSortAsc;
  sorted.sort(function(a, b) {
    var va = a[col], vb = b[col];
    if (va == null) va = '';
    if (vb == null) vb = '';
    if (typeof va === 'number' && typeof vb === 'number') return asc ? va - vb : vb - va;
    va = String(va); vb = String(vb);
    return asc ? va.localeCompare(vb) : vb.localeCompare(va);
  });

  var tHtml = '';
  for (var i = 0; i < sorted.length; i++) {
    var r = sorted[i];
    var ts = new Date(r.timestamp * 1000);
    var tStr = ts.toLocaleString('en-US', {hour12:false, month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit'});
    var shortModel = shortModelName(r.model || '');
    var cls = modelColorClass(r.model || '');
    var ok = r.success ? '&#x2705;' : '&#x274C;';
    tHtml += '<tr>' +
      '<td>' + tStr + '</td>' +
      '<td class="' + cls + '">' + shortModel + '</td>' +
      '<td>' + (r.tier || '--') + '</td>' +
      '<td>' + (r.auth_mode || '--') + '</td>' +
      '<td>' + fmtNum(r.prompt_tokens || 0) + '</td>' +
      '<td>' + fmtNum(r.completion_tokens || 0) + '</td>' +
      '<td>' + fmtNum(r.total_tokens || 0) + '</td>' +
      '<td>$' + (r.estimated_cost_usd || 0).toFixed(4) + '</td>' +
      '<td>' + ok + '</td>' +
      '</tr>';
  }
  document.getElementById('usage-tbody').innerHTML = tHtml;

  // Update sort arrows
  document.querySelectorAll('#usage-table th').forEach(function(th) {
    var arrow = th.querySelector('.sort-arrow');
    if (th.dataset.col === usageSortCol) {
      arrow.textContent = usageSortAsc ? ' \u25B2' : ' \u25BC';
    } else {
      arrow.textContent = '';
    }
  });
}

// ── Page 4: Settings ──

function renderNetwork(d) {
  var net = d.network || {};
  var enabled = net.enabled || false;

  document.getElementById('net-node-id').textContent = net.node_id || '--';
  var statusBadge = document.getElementById('net-status');
  statusBadge.textContent = enabled ? 'online' : 'disabled';
  statusBadge.className = 'badge ' + (enabled ? 'oauth' : 'apikey');
  document.getElementById('net-alive').textContent = net.alive_count || 0;

  // Peers table
  var peers = net.peers || {};
  var peerIds = Object.keys(peers);
  var tbody = document.getElementById('net-peers-tbody');
  if (peerIds.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:var(--text2)">No peers connected</td></tr>';
  } else {
    var html = '';
    for (var i = 0; i < peerIds.length; i++) {
      var pid = peerIds[i];
      var p = peers[pid];
      var statusCls = p.status === 'alive' ? 'color:var(--green)' : p.status === 'suspect' ? 'color:var(--yellow)' : 'color:var(--red)';
      var caps = (p.capabilities || []).slice(0, 5).join(', ');
      var load = p.current_load != null ? (p.current_load * 100).toFixed(0) + '%' : '--';
      html += '<tr><td style="font-family:monospace;font-size:11px">' + esc(pid.slice(0, 20)) + '</td>';
      html += '<td>' + esc(p.hostname || '?') + '</td>';
      var peerDashUrl = 'http://' + (p.ip || '?') + ':8420';
      html += '<td style="font-family:monospace"><a href="' + peerDashUrl + '" target="_blank" style="color:var(--accent)">' + esc((p.ip || '?') + ':' + (p.port || '?')) + '</a></td>';
      html += '<td style="' + statusCls + '">' + esc(p.status || '?') + '</td>';
      html += '<td>' + load + '</td>';
      html += '<td style="font-size:11px">' + esc(caps) + '</td></tr>';
    }
    tbody.innerHTML = html;
  }

  // Topology visualization (simple node circles)
  var topo = document.getElementById('net-topology');
  if (!enabled) {
    topo.innerHTML = '<div style="color:var(--text2);font-size:12px;text-align:center;padding:20px">Network disabled. Set network.enabled: true in config.</div>';
    return;
  }
  var allNodes = [{id: net.node_id || 'self', status: 'alive', hostname: 'this node'}];
  for (var j = 0; j < peerIds.length; j++) {
    allNodes.push({id: peerIds[j], status: peers[peerIds[j]].status, hostname: peers[peerIds[j]].hostname});
  }
  var topoHtml = '<div style="display:flex;gap:16px;align-items:center;justify-content:center;flex-wrap:wrap;padding:10px">';
  for (var k = 0; k < allNodes.length; k++) {
    var n = allNodes[k];
    var bg = n.status === 'alive' ? 'var(--green)' : n.status === 'suspect' ? 'var(--yellow)' : 'var(--red)';
    var glow = n.status === 'alive' ? 'box-shadow:0 0 10px ' + bg : '';
    var isSelf = k === 0;
    topoHtml += '<div style="text-align:center">';
    topoHtml += '<div style="width:' + (isSelf ? 48 : 36) + 'px;height:' + (isSelf ? 48 : 36) + 'px;border-radius:50%;background:' + bg + ';' + glow + ';margin:0 auto 4px;display:flex;align-items:center;justify-content:center;font-size:' + (isSelf ? 20 : 16) + 'px">';
    topoHtml += isSelf ? '&#x2B50;' : '&#x1F4BB;';
    topoHtml += '</div>';
    topoHtml += '<div style="font-size:10px;color:var(--text2)">' + esc(n.hostname || n.id.slice(0, 12)) + '</div>';
    topoHtml += '</div>';
    if (k < allNodes.length - 1) {
      topoHtml += '<div style="color:var(--border);font-size:20px">&#x2194;</div>';
    }
  }
  topoHtml += '</div>';
  topo.innerHTML = topoHtml;
}

function renderSettings(d) {
  // Auth
  var au = d.auth || {};
  var b = document.getElementById('auth-badge');
  b.textContent = au.mode || '--';
  b.className = 'badge ' + (au.mode && au.mode.includes('OAuth') ? 'oauth' : 'apikey');
  document.getElementById('auth-source').textContent = au.source || '--';
  document.getElementById('auth-token').textContent = au.token_masked || '--';
  document.getElementById('auth-cli-ver').textContent = au.claude_code_version || '--';
  document.getElementById('auth-provider').textContent = au.provider || '--';

  // Sync model dropdowns once
  if (!modelSyncDone && au.models) {
    var m = au.models;
    setSelectVal('sel-tier1', m.tier1);
    setSelectVal('sel-tier2', m.tier2);
    setSelectVal('sel-opus', m.opus);
    modelSyncDone = true;
  }

  // Tools
  var tools = d.tools || [];
  var toolsEl = document.getElementById('tools-list');
  var toolHtml = '';
  for (var i = 0; i < tools.length; i++) {
    var t = tools[i];
    toolHtml += '<div class="tool-card"><div class="tool-name">' + esc(t.name) + '</div>' +
      '<div class="tool-desc">' + esc(t.description || '') + '</div>' +
      '<span class="risk-badge risk-' + t.risk + '">' + t.risk + '</span></div>';
  }
  toolsEl.innerHTML = toolHtml || '<span style="font-size:12px;color:var(--text2)">No tools registered</span>';

  // Config display
  if (d._configRendered !== true) {
    var configEl = document.getElementById('config-raw');
    try {
      var cfg = {};
      // Build config from available data
      if (d.auth && d.auth.models) {
        cfg.llm = { tier1: { model: d.auth.models.tier1 }, tier2: { model: d.auth.models.tier2 }, opus: { model: d.auth.models.opus } };
      }
      if (d.heartbeat) {
        cfg.heartbeat = {
          script_interval_s: d.heartbeat.script_interval_s,
          llm_interval_s: d.heartbeat.llm_interval_s,
          major_interval_s: d.heartbeat.major_interval_s,
          status: d.heartbeat.status
        };
      }
      if (d.agent) {
        cfg.agent = d.agent;
      }
      configEl.textContent = JSON.stringify(cfg, null, 2);
    } catch(e) {
      configEl.textContent = 'Error loading config';
    }
  }
}

// ── Shared: Activity Feed renderer ──

var stageIcons = {
  'perceiving':'&#x1F50D;','orienting':'&#x1F9ED;','deciding':'&#x1F9E0;',
  'executing':'&#x26A1;','responding':'&#x1F4AC;','done':'&#x2705;',
  'error':'&#x274C;'
};

function renderActivityFeed(acts, elementId, limit) {
  var feed = document.getElementById(elementId);
  if (!feed) return;
  if (acts.length > 0) {
    var items = acts.slice(-(limit || 30));
    var fHtml = '';
    for (var i = 0; i < items.length; i++) {
      var act = items[i];
      var ts = new Date(act.timestamp * 1000);
      var tStr = ts.toLocaleTimeString('en-US', {hour12:false});
      var icon = stageIcons[act.stage] || '&#x1F4E1;';
      fHtml += '<div class="activity-line"><span class="ts">' + tStr + '</span>' + icon + ' ' + esc(act.stage) + ': ' + esc(act.detail || '') + '</div>';
    }
    feed.innerHTML = fHtml;
    feed.scrollTop = feed.scrollHeight;
  }
}

// ── Chat actions ──

function sendChat() {
  var input = document.getElementById('chat-input');
  var text = input.value.trim();
  if (!text) return;
  input.value = '';
  fetch('/api/chat', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text:text}) });
}

// ── Settings actions ──

function saveModels() {
  var tier1 = document.getElementById('sel-tier1').value;
  var tier2 = document.getElementById('sel-tier2').value;
  var opus = document.getElementById('sel-opus').value;
  var configs = [
    {key:'llm.tier1.model', value:tier1},
    {key:'llm.tier2.model', value:tier2},
    {key:'llm.opus.model', value:opus},
  ];
  for (var i = 0; i < configs.length; i++) {
    fetch('/api/config', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(configs[i]) });
  }
  var btn = document.getElementById('save-models');
  btn.textContent = 'Saved!';
  setTimeout(function() { btn.textContent = 'Save Models'; }, 1500);
}

function control(action) {
  fetch('/api/control', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:action}) })
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var fb = document.getElementById('ctrl-feedback');
      if (fb) {
        fb.textContent = d.ok ? 'Action: ' + (d.action || action) + ' OK' : 'Error: ' + (d.error || 'unknown');
        fb.style.color = d.ok ? 'var(--green)' : 'var(--red)';
        setTimeout(function() { fb.textContent = ''; }, 3000);
      }
    })
    .catch(function() {});
}

function refreshConfig() {
  if (lastData) {
    lastData._configRendered = false;
    renderSettings(lastData);
  }
}

// ── Helpers ──

function setVBar(val, textId, barId) {
  var v = val != null ? val.toFixed(1) : '--';
  document.getElementById(textId).textContent = v + '%';
  var bar = document.getElementById(barId);
  bar.style.height = (val || 0) + '%';
  if (val >= 85) bar.style.background = 'var(--red)';
  else if (val >= 60) bar.style.background = 'var(--yellow)';
  else bar.style.background = 'var(--green)';
}

function setMeter(val, textId, barId) {
  var v = val != null ? val.toFixed(1) : '--';
  document.getElementById(textId).textContent = v + '%';
  var bar = document.getElementById(barId);
  bar.style.width = (val || 0) + '%';
  if (val >= 85) bar.style.background = 'var(--red)';
  else if (val >= 60) bar.style.background = 'var(--yellow)';
  else bar.style.background = 'var(--green)';
}

function setEmo(key, val) {
  var v = val != null ? val : 0;
  document.getElementById('emo-' + key).style.width = (v * 100) + '%';
  document.getElementById('emo-' + key + '-v').textContent = v.toFixed(2);
}

function fmtUptime(s) {
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
}

function fmtNum(n) { return n > 9999 ? (n/1000).toFixed(1)+'k' : n.toString(); }

function esc(s) { var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

function setSelectVal(id, val) {
  var el = document.getElementById(id);
  if (el && val) { for (var i = 0; i < el.options.length; i++) { if (el.options[i].value === val) { el.value = val; return; } } }
}

function shortModelName(m) {
  if (m.includes('haiku')) return 'haiku';
  if (m.includes('sonnet')) return 'sonnet';
  if (m.includes('opus')) return 'opus';
  return m.slice(0,12);
}

function modelColorClass(m) {
  if (m.includes('haiku')) return 'model-haiku';
  if (m.includes('sonnet')) return 'model-sonnet';
  if (m.includes('opus')) return 'model-opus';
  return '';
}

// ── Event listeners ──

document.getElementById('chat-input').addEventListener('keydown', function(e) { if (e.key === 'Enter') sendChat(); });
document.getElementById('chat-send').addEventListener('click', sendChat);
document.getElementById('save-models').addEventListener('click', saveModels);
document.getElementById('file-upload').addEventListener('change', function(e) {
  var files = e.target.files;
  if (!files || files.length === 0) return;
  var form = new FormData();
  for (var i = 0; i < files.length; i++) {
    form.append('file', files[i]);
  }
  fetch('/api/upload', { method: 'POST', body: form }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      var names = d.files.map(function(f) { return f.name; }).join(', ');
      var chatEl = document.getElementById('chat-messages');
      chatEl.innerHTML += '<div class="msg system">Uploaded: ' + esc(names) + '</div>';
      chatEl.scrollTop = chatEl.scrollHeight;
    }
  });
  e.target.value = '';
});

// Chat side panel toggle
document.getElementById('chat-toggle').addEventListener('click', function() {
  var layout = document.getElementById('chat-layout');
  layout.classList.toggle('collapsed');
  var toggle = document.getElementById('chat-toggle');
  toggle.innerHTML = layout.classList.contains('collapsed') ? '&#x25C0;' : '&#x25B6;';
});

// Usage table sorting
document.querySelectorAll('#usage-table th[data-col]').forEach(function(th) {
  th.addEventListener('click', function() {
    var col = th.dataset.col;
    if (usageSortCol === col) {
      usageSortAsc = !usageSortAsc;
    } else {
      usageSortCol = col;
      usageSortAsc = th.dataset.num ? false : true;
    }
    renderUsageTable();
  });
});

// ── TTS (Text-to-Speech) ──
var ttsEnabled = false;
var ttsAudio = null;

document.getElementById('voice-toggle').addEventListener('click', function() {
  ttsEnabled = !ttsEnabled;
  this.classList.toggle('active', ttsEnabled);
  if (!ttsEnabled && ttsAudio) {
    ttsAudio.pause();
  }
});

function playTTS(text) {
  if (!ttsEnabled || !text || text.length < 5) return;
  fetch('/api/tts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text: text})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok && d.url) {
      // Use Web Audio API for lipsync analysis
      fetch(d.url).then(function(r) { return r.arrayBuffer(); }).then(function(buf) {
        var audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        audioCtx.decodeAudioData(buf, function(audioBuffer) {
          var analyser = audioCtx.createAnalyser();
          analyser.fftSize = 256;
          analyser.smoothingTimeConstant = 0.7;
          var source = audioCtx.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(analyser);
          analyser.connect(audioCtx.destination);
          var dataArray = new Float32Array(analyser.fftSize);
          var stopped = false;

          function updateMouth() {
            if (stopped) return;
            analyser.getFloatTimeDomainData(dataArray);
            var sum = 0;
            for (var i = 0; i < dataArray.length; i++) sum += dataArray[i] * dataArray[i];
            var rms = Math.sqrt(sum / dataArray.length);
            live2dMouthOpen = Math.min(1, rms / 0.15);
            requestAnimationFrame(updateMouth);
          }

          source.onended = function() {
            stopped = true;
            live2dMouthOpen = 0;
            audioCtx.close();
          };
          source.start(0);
          updateMouth();
        });
      });
    }
  }).catch(function() {});
}

// Hook TTS into chat rendering — play new agent messages
var _origLastChatLen = 0;
var _ttsObserver = setInterval(function() {
  if (!ttsEnabled || !lastData) return;
  var history = lastData.chat_history || [];
  if (history.length > _origLastChatLen) {
    var newMsg = history[history.length - 1];
    if (newMsg && newMsg.role === 'agent') {
      playTTS(newMsg.content);
    }
    _origLastChatLen = history.length;
  }
}, 2000);

// ── STT (Speech-to-Text) via Web Speech API ──
var sttActive = false;
var recognition = null;

if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
  var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = 'zh-CN';

  recognition.onresult = function(event) {
    var transcript = '';
    for (var i = event.resultIndex; i < event.results.length; i++) {
      transcript += event.results[i][0].transcript;
    }
    document.getElementById('chat-input').value = transcript;
    if (event.results[event.results.length - 1].isFinal) {
      sendChat();
      sttActive = false;
      document.getElementById('mic-btn').classList.remove('recording');
    }
  };

  recognition.onend = function() {
    sttActive = false;
    document.getElementById('mic-btn').classList.remove('recording');
  };

  recognition.onerror = function() {
    sttActive = false;
    document.getElementById('mic-btn').classList.remove('recording');
  };
}

document.getElementById('mic-btn').addEventListener('click', function() {
  if (!recognition) {
    alert('Speech recognition not supported in this browser. Use Chrome or Edge.');
    return;
  }
  if (sttActive) {
    recognition.stop();
    sttActive = false;
    this.classList.remove('recording');
  } else {
    recognition.start();
    sttActive = true;
    this.classList.add('recording');
  }
});

// ── Live2D Avatar (PurpleBird model with emotion driver) ──
var live2dReady = false;
var live2dMouthOpen = 0;

// Emotion → Live2D parameter mapping (from eva-live2d emotion-driver)
var EMOTION_MAP = {
  happy: { expression: null, params: { ParamEyeLSmile: 0.7, ParamEyeRSmile: 0.7, ParamMouthForm: 0.6, Param7: 0.3 }},
  excited: { expression: '星星眼', params: { ParamEyeLSmile: 0.5, ParamEyeRSmile: 0.5, ParamMouthForm: 0.8 }},
  sad: { expression: 'QAQ', params: { ParamBrowLY: -0.4, ParamBrowRY: -0.4, ParamMouthForm: -0.2 }},
  angry: { expression: '生气', params: { Param8: 0.8, ParamBrowLAngle: -0.5, ParamBrowRAngle: -0.5, ParamMouthForm: -0.4 }},
  embarrassed: { expression: '脸红', params: { Param7: 1.0, ParamEyeLOpen: 0.6, ParamEyeROpen: 0.6 }},
  curious: { expression: '问号', params: { ParamAngleZ: 8.0, ParamBrowLY: 0.3, ParamBrowRY: 0.3 }},
  sleepy: { expression: null, params: { ParamEyeLOpen: 0.3, ParamEyeROpen: 0.3, ParamEyeLSmile: 0.3, ParamEyeRSmile: 0.3 }},
  neutral: { expression: null, params: {}},
};

// Map ANIMA's 4-dim emotion to dominant emotion name
function getDominantEmotion(emotion) {
  if (!emotion) return 'neutral';
  var e = emotion.engagement || 0, co = emotion.confidence || 0,
      cu = emotion.curiosity || 0, cn = emotion.concern || 0;
  if (cn > 0.6) return 'sad';
  if (cu > 0.7) return 'curious';
  if (e > 0.7 && co > 0.6) return 'excited';
  if (e > 0.6) return 'happy';
  if (co > 0.7) return 'confident';
  if (cn > 0.4) return 'worried';
  return 'neutral';
}

function initLive2D() {
  if (typeof PIXI === 'undefined' || typeof PIXI.live2d === 'undefined') {
    setTimeout(initLive2D, 1000);
    return;
  }

  var container = document.getElementById('live2d-container');
  var canvas = document.getElementById('live2d-canvas');
  if (!container || !canvas) return;

  try {
    var app = new PIXI.Application({
      view: canvas,
      autoStart: true,
      backgroundAlpha: 0,
      resizeTo: container,
    });

    PIXI.live2d.Live2DModel.from('/static/model/PurpleBird/PurpleBird.model3.json', {
      autoInteract: false,
    }).then(function(model) {
      app.stage.addChild(model);

      // Position model
      function positionModel() {
        var w = container.clientWidth, h = container.clientHeight;
        var scale = Math.min(w / model.width, h / model.height) * 0.7;
        model.scale.set(scale);
        model.anchor.set(0.5, 0.5);
        model.x = w / 2;
        model.y = h * 0.55;
      }
      positionModel();

      // Resize handler
      new ResizeObserver(positionModel).observe(container);

      // Mouse tracking
      document.addEventListener('mousemove', function(e) {
        model.focus(e.clientX, e.clientY);
      });

      // Mouth sync via ticker
      app.ticker.add(function() {
        try {
          var cm = model.internalModel && model.internalModel.coreModel;
          if (cm) {
            cm.setParameterValueById('ParamMouthOpenY', live2dMouthOpen);
          }
        } catch(e) {}
      });

      window.live2dModel = model;
      live2dReady = true;
      console.log('Live2D PurpleBird loaded');
    }).catch(function(e) {
      console.log('Live2D load failed:', e);
      container.style.display = 'none';
    });
  } catch(e) {
    console.log('Live2D init error:', e);
    container.style.display = 'none';
  }
}

var lastEmotionName = 'neutral';
function updateLive2DExpression(emotion) {
  if (!live2dReady || !window.live2dModel) return;
  var model = window.live2dModel;
  var emoName = getDominantEmotion(emotion);
  if (emoName === lastEmotionName) return;
  lastEmotionName = emoName;

  var mapping = EMOTION_MAP[emoName] || EMOTION_MAP['neutral'];
  try {
    // Set expression if mapped
    if (mapping.expression) {
      var em = model.internalModel && model.internalModel.motionManager &&
               model.internalModel.motionManager.expressionManager;
      if (em) em.setExpression(mapping.expression);
    }
    // Set parameters
    var cm = model.internalModel && model.internalModel.coreModel;
    if (cm) {
      for (var param in mapping.params) {
        try { cm.setParameterValueById(param, mapping.params[param]); } catch(e) {}
      }
    }
  } catch(e) {}
}

setTimeout(initLive2D, 1500);

// ── Start ──
connect();
</script>
</body>
</html>"""
