/**
 * ANIMA Desktop — Main Controller
 *
 * Key design:
 * - Both avatar canvases always in DOM, stacked via z-index (no display:none)
 * - Chat shows real-time activity like a terminal (thinking, tool calls, etc.)
 * - TTS only fires on final agent response, not intermediate
 */

import { VRMAvatar } from './vrm.js';
import { Live2DAvatar } from './live2d.js';
import { VoiceManager, setVrmAvatarGetter } from './voice.js';

let ws = null, lastData = null, lastChatLen = 0, lastActivityLen = 0, currentMode = 'vrm';
let bubbleTimer = null, vrmAvatar = null, live2dAvatar = null, voiceManager = null;
let currentPage = 'avatar';

// Track whether Eva is currently processing (for TTS gating)
let evaProcessing = false;
// Track which tts_urls we already played (by message index)
let playedTTS = new Set();

// Logs state
let logEntries = [];
let logsPaused = false;
let logFilter = 'all';
const MAX_LOG_ENTRIES = 500;

// Degradation tracking
let _lastDegradedState = false;

// ═══ BOOT ═══
const BOOT = [
  ['Loading core...', 100], ['Neural matrix...', 140], ['Heartbeat...', 100],
  ['Emotion state...', 80], ['Perception...', 80], ['Network...', 120],
  ['Avatar engines...', 80], ['Voice...', 60], ['Backend...', 160], ['Ready', 50],
];

async function boot() {
  const fill = document.getElementById('boot-fill');
  const msg = document.getElementById('boot-msg');
  for (let i = 0; i < BOOT.length; i++) {
    msg.textContent = BOOT[i][0];
    fill.style.width = ((i+1)/BOOT.length*100)+'%';
    await sleep(BOOT[i][1]);
  }
  await sleep(200);
  document.getElementById('boot').classList.add('done');
  const app = document.getElementById('app');
  app.style.display = 'flex';
  void app.offsetHeight;
  app.style.transition = 'opacity .4s';
  app.style.opacity = '1';
  await sleep(400);
  document.getElementById('boot').remove();
}

// ═══ INIT ═══
document.addEventListener('DOMContentLoaded', async () => {
  await boot();
  setupNav();
  setupChat();
  setupModeSwitch();
  setupLogs();
  voiceManager = new VoiceManager();
  voiceManager.init();
  setVrmAvatarGetter(() => currentMode === 'vrm' ? vrmAvatar : null);
  initAvatars();
  connect();
});

// ═══ NAV ═══
function setupNav() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentPage = btn.dataset.page;
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.page === currentPage));
      document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-'+currentPage));
      _lastPageRender = 0; // Force immediate render on page switch
      if (lastData) renderPage(lastData);
    });
  });
}

// ═══ AVATARS — both init at startup, switch via z-index ═══
function initAvatars() {
  const container = document.getElementById('avatar-vp');
  const vrmC = document.getElementById('vrm-canvas');
  const l2dC = document.getElementById('live2d-canvas');

  // VRM (starts in front)
  vrmAvatar = new VRMAvatar(vrmC, container);
  vrmAvatar.init().then(() => { console.log('VRM ready'); buildOutfitPanel(); }).catch(err => {
    console.warn('VRM failed:', err.message);
    // Fallback to Live2D
    showMode('live2d');
  });

  // Live2D (starts behind, paused)
  live2dAvatar = new Live2DAvatar(l2dC, container);
  live2dAvatar.init().then(() => console.log('Live2D ready')).catch(err => console.warn('Live2D failed:', err.message));
}

function setupModeSwitch() {
  document.querySelectorAll('.av-btn').forEach(btn => {
    btn.addEventListener('click', () => showMode(btn.dataset.mode));
  });
}

function showMode(mode) {
  if (mode === currentMode) return;
  currentMode = mode;
  document.querySelectorAll('.av-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));

  const vrmC = document.getElementById('vrm-canvas');
  const l2dC = document.getElementById('live2d-canvas');

  const outfitPanel = document.getElementById('outfit-panel');
  if (mode === 'vrm') {
    vrmC.classList.add('av-front');
    l2dC.classList.remove('av-front');
    if (live2dAvatar?.app) live2dAvatar.app.stop();
    if (vrmAvatar) vrmAvatar.resume();
    if (outfitPanel && outfitPanel.children.length) outfitPanel.style.display = 'flex';
  } else {
    l2dC.classList.add('av-front');
    vrmC.classList.remove('av-front');
    if (vrmAvatar) vrmAvatar.pause();
    if (live2dAvatar?.app) live2dAvatar.app.start();
    if (outfitPanel) outfitPanel.style.display = 'none';
  }
}

// Build outfit buttons after VRM loads
function buildOutfitPanel() {
  if (!vrmAvatar) return;
  const meshes = vrmAvatar.getMeshes();
  const panel = document.getElementById('outfit-panel');
  if (!panel) return;
  panel.innerHTML = '';
  // Only show Costume meshes
  const costumes = meshes.filter(m => /costume/i.test(m.name));
  for (const m of costumes) {
    const btn = document.createElement('button');
    btn.className = 'outfit-btn';
    btn.textContent = m.name;
    btn.onclick = () => {
      const vis = vrmAvatar.toggleMesh(m.name);
      btn.classList.toggle('off', !vis);
    };
    panel.appendChild(btn);
  }
  panel.style.display = 'flex';
}

export function setMouthOpen(v) {
  if (currentMode === 'vrm' && vrmAvatar) vrmAvatar.setMouthOpen(v);
  else if (currentMode === 'live2d' && live2dAvatar) live2dAvatar.setMouthOpen(v);
}

// ═══ WEBSOCKET ═══
function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => dot(true);
  ws.onmessage = (e) => { lastData = JSON.parse(e.data); render(lastData); };
  ws.onclose = () => { dot(false); setTimeout(connect, 3000); };
  ws.onerror = () => ws.close();
}

function dot(on) {
  document.getElementById('nav-dot').className = 'nav-dot' + (on ? ' on' : '');
  const pd = document.getElementById('pill-dot');
  if (pd) pd.className = 'pill-dot' + (on ? ' on' : '');
}

// ═══ RENDER ═══
function render(d) {
  const a = d.agent || {}, hb = d.heartbeat || {};
  dot(a.status === 'alive');

  // Pills
  s('pill-status', (a.status === 'alive' ? 'Online' : 'Offline'));
  const emo = getEmo(d.emotion);
  s('pill-emo', emo.charAt(0).toUpperCase() + emo.slice(1));
  s('pill-hb', '#' + (hb.tick_count || 0));
  s('chat-agent', a.name || 'Eva');

  // Status bar
  updateStatusBar(d);

  // Degradation monitoring
  monitorDegradation(d);

  // Chat (messages + activity stream)
  renderChat(d);

  // Avatar emotion
  if (d.emotion) {
    if (currentMode === 'vrm' && vrmAvatar) vrmAvatar.updateEmotion(d.emotion);
    else if (currentMode === 'live2d' && live2dAvatar) live2dAvatar.updateEmotion(d.emotion);
  }

  // Track processing state for TTS gating
  const act = d.activity || [];
  const lastAct = act.length ? act[act.length - 1] : null;
  const isProcessing = lastAct && ['thinking', 'executing', 'responding', 'deciding'].includes(lastAct.stage);
  const statusEl = document.getElementById('chat-status');
  const statusInd = document.getElementById('status-indicator');
  const statusText = document.getElementById('status-text');
  if (statusEl) {
    statusEl.className = 'chat-status' + (isProcessing ? ' on' : '');
    if (isProcessing && statusInd && statusText) {
      const stage = lastAct.stage;
      statusInd.className = 'status-indicator ' + (stage === 'thinking' || stage === 'deciding' ? 'thinking' : stage === 'executing' ? 'executing' : 'streaming');
      statusText.textContent = stage.charAt(0).toUpperCase() + stage.slice(1) + '...';
    }
  }
  evaProcessing = isProcessing;

  // Collect log entries from activity
  collectLogs(d);

  renderPage(d);
}

// ═══ STATUS BAR ═══
function updateStatusBar(d) {
  const llm = d.llm_status || {};
  const idle = d.idle_scheduler || {};
  const usage = d.usage || {};
  const net = d.network || {};

  // Model name + status dot color
  document.getElementById('sb-model-name').textContent = llm.active_model || '?';
  const dotEl = document.getElementById('sb-dot');
  if (dotEl) {
    dotEl.className = 'status-dot ' + (llm.degraded ? 'red' : 'green');
  }

  // Idle bar
  const score = idle.idle_score || 0;
  const idleFill = document.getElementById('sb-idle-fill');
  if (idleFill) idleFill.style.width = (score * 100) + '%';
  document.getElementById('sb-idle-level').textContent = (idle.idle_level || '\u2014').toUpperCase();

  // Cost
  document.getElementById('sb-cost').textContent = '$' + (usage.cost || 0).toFixed(2);

  // Nodes
  document.getElementById('sb-nodes').textContent = net.alive_count || 0;

  // Degradation badge
  const badge = document.getElementById('sb-degraded');
  if (badge) {
    badge.classList.toggle('visible', !!llm.degraded);
  }
}

// ═══ DEGRADATION MONITOR ═══
function monitorDegradation(d) {
  const llm = d.llm_status || {};
  const degraded = !!llm.degraded;

  if (degraded && !_lastDegradedState) {
    // Just entered degraded state
    showNotificationToast('LLM Degraded', llm.degraded_reason || 'model cascade fallback', 'warning');
  } else if (!degraded && _lastDegradedState) {
    // Recovered
    showNotificationToast('LLM Recovered', llm.active_model || 'primary model restored', 'success');
  }

  _lastDegradedState = degraded;
}

// ═══ TOAST NOTIFICATIONS ═══
function showNotificationToast(title, message, type) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const icons = { info: '\u2139', warning: '\u26A0', error: '\u2718', success: '\u2714' };

  const toast = document.createElement('div');
  toast.className = 'toast ' + (type || 'info');
  toast.innerHTML = `<div class="toast-icon">${icons[type] || icons.info}</div>
    <div class="toast-body">
      <div class="toast-title">${esc(title)}</div>
      <div class="toast-message">${esc(message)}</div>
    </div>
    <button class="toast-close" onclick="this.parentElement.classList.add('dismissing');setTimeout(()=>this.parentElement.remove(),250)">\u00D7</button>
    <div class="toast-progress"><div class="toast-progress-fill" style="width:100%"></div></div>`;

  container.appendChild(toast);

  // Animate progress bar
  const progressFill = toast.querySelector('.toast-progress-fill');
  if (progressFill) {
    progressFill.style.transitionDuration = '5s';
    requestAnimationFrame(() => { progressFill.style.width = '0%'; });
  }

  // Auto-remove after 5s
  setTimeout(() => {
    toast.classList.add('dismissing');
    setTimeout(() => toast.remove(), 250);
  }, 5000);
}

let _lastPageRender = 0;
function renderPage(d) {
  // Throttle non-avatar pages to every 5s (prevent flashing from 2s WS push)
  const now = Date.now();
  if (currentPage !== 'avatar' && now - _lastPageRender < 5000) return;
  _lastPageRender = now;

  if (currentPage === 'overview') renderOverview(d);
  else if (currentPage === 'network') renderNetwork(d);
  else if (currentPage === 'evolution') renderEvolution(d);
  else if (currentPage === 'settings') renderSettings(d);
  else if (currentPage === 'logs') renderLogs(d);
}

// ═══ MARKDOWN RENDERER (fallback only — primary is streaming-markdown) ═══
function renderMd(text) {
  if (typeof marked !== 'undefined') {
    try { return marked.parse(text); } catch(_) {}
  }
  // Ultra-simple fallback
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
}

// Copy helpers
window._copyCode = function(btn) {
  const code = btn.closest('.code-block').querySelector('code').textContent;
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  }).catch(() => {
    // fallback
    const ta = document.createElement('textarea'); ta.value = code; ta.style.cssText='position:fixed;opacity:0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
    btn.textContent = 'Copied!'; setTimeout(() => btn.textContent = 'Copy', 2000);
  });
};

function copyMessage(markdown) {
  navigator.clipboard.writeText(markdown).catch(() => {
    const ta = document.createElement('textarea'); ta.value = markdown; ta.style.cssText='position:fixed;opacity:0';
    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
  });
  showToast('Copied');
}

function showToast(text) {
  const t = document.createElement('div'); t.className='copy-toast'; t.textContent=text;
  document.body.appendChild(t); setTimeout(()=>t.remove(),2000);
}

// ═══ CHAT RENDERING — terminal style, zero flicker ═══
let _renderedMsgCount = 0;
let _lastActLine = null;     // reusable activity line at bottom

let _chatWasAtBottom = true;
function renderChat(d) {
  const history = d.chat_history || [];
  const activity = d.activity || [];
  const el = document.getElementById('chat-msgs');
  // Detect scroll position BEFORE DOM update — if user scrolled up, don't auto-scroll
  _chatWasAtBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;

  // ── Handle history reset/clear ──
  if (history.length < _renderedMsgCount) {
    el.innerHTML = '';
    _renderedMsgCount = 0;
    _lastActLine = null;
  }

  // ── Messages: append only NEW ones ──
  if (history.length > _renderedMsgCount) {
    // Remove activity line before appending (it goes back at the bottom)
    if (_lastActLine && _lastActLine.parentNode) _lastActLine.remove();

    for (let i = _renderedMsgCount; i < history.length; i++) {
      const m = history[i];
      const role = m.role === 'user' ? 'user' : m.role === 'system' ? 'system' : 'agent';
      const div = document.createElement('div');
      div.className = `msg ${role}`;

      if (role === 'agent') {
        if (window.smd) {
          const renderer = window.smd.default_renderer(div);
          const parser = window.smd.parser(renderer);
          window.smd.parser_write(parser, m.content || '');
          window.smd.parser_end(parser);
        } else {
          div.innerHTML = renderMd(m.content || '');
        }
        div.querySelectorAll('pre code').forEach(block => {
          if (typeof hljs !== 'undefined') try { hljs.highlightElement(block); } catch(_) {}
        });
      } else {
        div.textContent = m.content || '';
      }
      el.appendChild(div);
    }

    _renderedMsgCount = history.length;

    // Bubble + TTS for latest new agent message
    const last = history[history.length - 1];
    if (last && last.role !== 'user' && last.role !== 'system') {
      showBubble(last.content);
      if (voiceManager?.autoTTS && !evaProcessing && last.tts_url && !playedTTS.has(history.length - 1)) {
        playedTTS.add(history.length - 1);
        voiceManager.playUrl(last.tts_url);
      }
    }
  }

  // ── Activity: single reusable line at bottom of terminal ──
  if (activity.length !== lastActivityLen) {
    lastActivityLen = activity.length;
    const last = activity.length ? activity[activity.length - 1] : null;
    if (last && last.stage && last.stage !== 'idle') {
      if (!_lastActLine) {
        _lastActLine = document.createElement('div');
        _lastActLine.className = 'msg activity';
      }
      const stg = last.stage;
      const det = (last.detail || '').substring(0, 80);
      const tool = last.tool || '';
      if (stg === 'executing' && tool) _lastActLine.innerHTML = `<span class="act-ok">\u2699</span> ${esc(tool)} ${esc(det)}`;
      else if (stg === 'thinking' || stg === 'deciding') _lastActLine.textContent = '\u25D0 thinking...';
      else if (stg === 'tool_done') _lastActLine.innerHTML = `<span class="act-ok">\u2713</span> ${esc(det)}`;
      else if (stg === 'error') _lastActLine.innerHTML = `<span class="act-err">\u2715</span> ${esc(det)}`;
      else _lastActLine.textContent = `${stg} ${det}`;
      if (!_lastActLine.parentNode) el.appendChild(_lastActLine);
    } else if (_lastActLine && _lastActLine.parentNode) {
      _lastActLine.remove();
    }
  }

  // Only auto-scroll if user was already at the bottom — don't interrupt
  // manual scroll-back to read history. 40px threshold for rounding.
  if (_chatWasAtBottom) {
    el.scrollTop = el.scrollHeight;
  }

  // ── Processing state for TTS gating ──
  if (activity.length > 0) {
    const lastAct = activity[activity.length - 1];
    evaProcessing = lastAct && lastAct.stage && !['idle', 'error'].includes(lastAct.stage);
  }
}

function showBubble(text) {
  const b = document.getElementById('bubble'), bt = document.getElementById('bubble-text');
  if (!b) return;
  bt.textContent = text.length > 280 ? text.slice(0, 280) + '...' : text;
  b.style.display = 'block'; b.style.animation = 'none'; void b.offsetHeight; b.style.animation = '';
  if (bubbleTimer) clearTimeout(bubbleTimer);
  bubbleTimer = setTimeout(() => { b.style.display = 'none'; }, 10000);
}

// ═══ CHAT INPUT + STREAMING ═══
let pendingFiles = [];

function setupChat() {
  document.getElementById('btn-send').addEventListener('click', send);
  document.getElementById('chat-text').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  setupFileUpload();
}

function setupFileUpload() {
  const chatEl = document.querySelector('.chat');
  const dropOverlay = document.getElementById('drop-overlay');
  const fileInput = document.getElementById('file-input');
  if (!chatEl || !dropOverlay || !fileInput) return;

  document.getElementById('btn-attach')?.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => { addFiles(fileInput.files); fileInput.value=''; });

  let dragCount = 0;
  chatEl.addEventListener('dragenter', e => { e.preventDefault(); dragCount++; dropOverlay.classList.add('visible'); });
  chatEl.addEventListener('dragover', e => e.preventDefault());
  chatEl.addEventListener('dragleave', e => { e.preventDefault(); dragCount--; if(!dragCount) dropOverlay.classList.remove('visible'); });
  chatEl.addEventListener('drop', e => { e.preventDefault(); dragCount=0; dropOverlay.classList.remove('visible'); if(e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
}

function addFiles(fileList) {
  const previewsEl = document.getElementById('file-previews');
  if (!previewsEl) return;
  for (const file of fileList) {
    if (file.size > 10*1024*1024) { alert(`${file.name} exceeds 10MB`); continue; }
    pendingFiles.push(file);
    const item = document.createElement('div');
    item.className = 'file-preview-item';
    if (file.type.startsWith('image/')) {
      const img = document.createElement('img');
      const reader = new FileReader();
      reader.onload = e => img.src = e.target.result;
      reader.readAsDataURL(file);
      item.appendChild(img);
    } else {
      const ext = document.createElement('span');
      ext.className = 'file-ext';
      ext.textContent = file.name.split('.').pop().toUpperCase();
      item.appendChild(ext);
    }
    const name = document.createElement('span');
    name.className = 'file-name';
    name.textContent = file.name;
    item.appendChild(name);
    const rm = document.createElement('button');
    rm.className = 'file-remove';
    rm.textContent = '\u00D7';
    rm.onclick = () => { pendingFiles.splice(pendingFiles.indexOf(file),1); item.remove(); };
    item.appendChild(rm);
    previewsEl.appendChild(item);
  }
}

async function send() {
  const el = document.getElementById('chat-text');
  const text = el.value.trim();
  if (!text && !pendingFiles.length) return;
  el.value = '';

  if (pendingFiles.length > 0) {
    const fd = new FormData();
    fd.append('text', text);
    for (const f of pendingFiles) fd.append('files', f);
    pendingFiles = [];
    document.getElementById('file-previews').innerHTML = '';
    fetch('/api/chat/upload', {method:'POST', body:fd}).catch(console.error);
  } else {
    // Fire-and-forget — response arrives via WebSocket push
    fetch('/api/chat', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})
    }).catch(console.error);
  }
}

// ═══ OVERVIEW ═══
function renderOverview(d) {
  const a = d.agent || {}, hb = d.heartbeat || {}, sys = d.system || {}, emo = d.emotion || {};
  const usage = d.llm_usage_summary || {}, u = d.usage || {};
  const llm = d.llm_status || {};
  const idle = d.idle_scheduler || {};

  // Model Cascade visualization
  renderModelCascade(llm);

  // Idle Scheduler visualization
  renderIdleScheduler(idle);

  s('ov-status', a.status === 'alive' ? 'Online' : 'Offline');
  s('ov-uptime', fmtUp(d.uptime_s || 0));
  s('ov-tick', '#' + (hb.tick_count || 0));
  s('ov-queue', (d.event_queue || {}).size || 0);

  // Circular gauges
  const cpuPct = sys.cpu_percent || 0;
  const memPct = sys.memory_percent || 0;
  const diskPct = sys.disk_percent || 0;
  s('ov-cpu', cpuPct.toFixed(0) + '%');
  s('ov-mem', memPct.toFixed(0) + '%');
  s('ov-disk', diskPct.toFixed(0) + '%');
  setGauge('gauge-cpu', cpuPct);
  setGauge('gauge-mem', memPct);
  setGauge('gauge-disk', diskPct);

  s('ov-proc', sys.process_count || '--');
  bar('ov-eng', (emo.engagement || 0) * 100); bar('ov-conf', (emo.confidence || 0) * 100);
  bar('ov-cur', (emo.curiosity || 0) * 100); bar('ov-con', (emo.concern || 0) * 100);
  s('ov-calls', usage.total_calls || u.calls || 0);
  s('ov-tokens', fmtN(usage.total_tokens || u.total_tokens || 0));
  s('ov-cost', '$' + (usage.total_cost_usd || 0).toFixed(4));
  s('ov-budget', u.daily_budget_usd ? '$' + u.daily_budget_usd.toFixed(2) + '/day' : '--');
  const wm = d.working_memory || {};
  const wmEl = document.getElementById('ov-wm');
  if (wm.items?.length) {
    wmEl.innerHTML = wm.items.map(i => `<div class="wm-item"><span class="wm-type">${esc(i.type)}</span> ${esc(i.content)} <span style="color:var(--w20)">(${i.importance})</span></div>`).join('');
  } else { wmEl.innerHTML = `<span class="card-label">Empty (${wm.size||0}/${wm.capacity||0})</span>`; }
  const actEl = document.getElementById('ov-activity');
  const acts = d.activity || [];
  if (acts.length) {
    actEl.textContent = acts.slice(-15).map(a => {
      const t = new Date(a.timestamp * 1000).toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
      return `${t}  ${a.stage}  ${a.detail || ''}`;
    }).join('\n');
  }
}

// ═══ MODEL CASCADE VISUALIZATION ═══
function renderModelCascade(llm) {
  const el = document.getElementById('ov-cascade');
  if (!el) return;

  const activeModel = llm.active_model || '?';
  const degraded = llm.degraded || false;
  const cascade = llm.cascade || llm.models || [];
  const reason = llm.degraded_reason || '';

  if (cascade.length) {
    el.innerHTML = `<div class="model-cascade">${cascade.map((m, i) => {
      const isActive = m === activeModel || (m.name && m.name === activeModel);
      const name = typeof m === 'string' ? m : m.name || '?';
      const tierLabel = 'T' + (i + 1);
      return `<div class="cascade-node ${isActive ? 'active' : ''}">
        <span class="cascade-node-tier">${tierLabel}</span>
        <span class="cascade-node-name">${esc(name)}</span>
      </div>${i < cascade.length - 1 ? '<div class="cascade-arrow"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 10h8M11 6l4 4-4 4"/></svg></div>' : ''}`;
    }).join('')}</div>${degraded ? `<div style="color:var(--red);font-size:11px;margin-top:8px">\u26A0 ${esc(reason)}</div>` : ''}`;
  } else {
    el.innerHTML = `<div style="display:flex;align-items:center;gap:8px;font-size:13px">
      <span class="status-dot ${degraded ? 'red' : 'green'}" style="display:inline-block"></span>
      <span style="color:var(--w60)">${esc(activeModel)}</span>
      ${degraded ? `<span style="color:var(--red);font-size:11px;margin-left:8px">\u26A0 ${esc(reason)}</span>` : ''}
    </div>`;
  }
}

// ═══ IDLE SCHEDULER VISUALIZATION ═══
function renderIdleScheduler(idle) {
  const el = document.getElementById('ov-idle-sched');
  if (!el) return;

  const score = idle.idle_score || 0;
  const level = idle.idle_level || 'unknown';
  const tasks = idle.pending_tasks || idle.queue || [];
  const current = idle.current_task || null;

  // Determine level class for color
  const lvl = level.toLowerCase();
  const levelClass = lvl === 'low' ? 'low' : lvl === 'medium' ? 'medium' : lvl === 'high' ? 'high' : lvl === 'critical' ? 'critical' : '';

  let html = `<div class="idle-meter">
    <div class="idle-meter-bar"><div class="idle-meter-fill" style="width:${score * 100}%"></div></div>
    <span class="idle-meter-label ${levelClass}">${(score * 100).toFixed(0)}% ${esc(level.toUpperCase())}</span>
  </div>`;

  if (current) {
    html += `<div style="font-size:12px;color:var(--accent);margin-top:4px">\u25B6 ${esc(typeof current === 'string' ? current : current.name || current.title || '?')}</div>`;
  }

  if (tasks.length) {
    html += `<div style="font-size:11px;color:var(--w40);margin-top:6px">Queue: ${tasks.map(t => `<span class="tool-chip" style="margin:1px 2px">${esc(typeof t === 'string' ? t : t.name || t.title || '?')}</span>`).join('')}</div>`;
  }

  el.innerHTML = html;
}

// ═══ CIRCULAR GAUGE HELPER ═══
function setGauge(id, pct) {
  const el = document.getElementById(id);
  if (!el) return;
  // r=36, circumference = 2*PI*36 = ~226.2
  const circumference = 2 * Math.PI * 36;
  const offset = circumference - (Math.min(100, pct) / 100) * circumference;
  el.style.strokeDashoffset = offset;
}

// ═══ NETWORK ═══
function renderNetwork(d) {
  const net = d.network || {};
  s('net-id', net.node_id || '--');
  s('net-status', net.enabled ? 'Online' : 'Disabled');
  s('net-alive', net.alive_count || 0);
  const detailEl = document.getElementById('net-peers-detail');
  const peers = net.peers || {};
  const ids = Object.keys(peers);
  if (ids.length) {
    detailEl.innerHTML = ids.map(id => {
      const p = peers[id];
      const alive = p.status === 'ALIVE' || p.status === 'alive';
      const cpu = p.current_load ? (p.current_load * 100).toFixed(0) + '%' : '--';
      const emo = p.emotion || {};
      const emoName = getEmo(emo);
      return `<div class="card" style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <span style="font-weight:600">${p.hostname || id.substring(0,12)}</span>
          <span style="color:${alive ? 'var(--green)' : 'var(--red)'};font-size:11px">${alive ? 'ALIVE' : p.status || 'DEAD'}</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:11px;color:var(--w40)">
          <div>Agent: <span style="color:var(--w60)">${p.agent_name || '?'}</span></div>
          <div>IP: <span style="color:var(--w60)">${p.ip || '?'}:${p.port || '?'}</span></div>
          <div>Load: <span style="color:var(--w60)">${cpu}</span></div>
          <div>Uptime: <span style="color:var(--w60)">${p.uptime_s ? fmtUp(p.uptime_s) : '--'}</span></div>
          <div>Emotion: <span style="color:var(--w60)">${emoName}</span></div>
          <div>Tier: <span style="color:var(--w60)">${p.compute_tier || '?'}</span></div>
        </div>
      </div>`;
    }).join('');
  } else {
    detailEl.innerHTML = '<div class="card"><span class="card-label">No peers connected</span></div>';
  }
}

// Remote restart
window._remoteRestart = function() {
  const msg = 'Eva, use remote_exec to restart ANIMA on the laptop node. Command: taskkill /F /IM python.exe & timeout 2 & schtasks /Run /TN "ANIMA_START"';
  fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: msg }) });
};

// ═══ EVOLUTION ═══
function renderEvolution(d) {
  const evo = d.evolution || {};
  const mem = evo.memory || {};
  const git = d.git || {};

  // Timeline visualization
  renderEvolutionTimeline(mem);

  s('evo-successes', (mem.successes || []).length);
  s('evo-failures', (mem.failures || []).length);
  s('evo-cooldown', evo.cooldown_remaining > 0 ? evo.cooldown_remaining + 's' : 'Ready');
  s('evo-current', evo.current ? evo.current.title : 'Idle');
  s('evo-queue', evo.queue_size || 0);
  s('git-branch', git.branch || '--');
  s('evo-goals-count', (mem.goals || []).length);

  // Git log
  const gitLog = document.getElementById('git-log');
  if (git.recent_commits && git.recent_commits.length) {
    gitLog.textContent = git.recent_commits.join('\n');
  }

  // Evolution history
  const histEl = document.getElementById('evo-history');
  const successes = mem.successes || [];
  if (successes.length) {
    histEl.innerHTML = successes.slice().reverse().map(s => {
      const dt = s.timestamp ? new Date(s.timestamp * 1000).toLocaleString('en', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : '';
      return `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
        <div style="display:flex;justify-content:space-between">
          <span style="color:var(--white)">${esc(s.title || '?')}</span>
          <span style="color:var(--w20)">${dt}</span>
        </div>
        <div style="color:var(--w40);font-size:11px;margin-top:2px">${s.type || '?'} | ${(s.files||[]).join(', ') || 'no files'}</div>
      </div>`;
    }).join('');
  }

  // Goals with progress bars
  const goalsEl = document.getElementById('evo-goals');
  const goals = mem.goals || [];
  if (goals.length) {
    goalsEl.innerHTML = goals.map(g => {
      const pct = Math.round((g.progress || 0) * 100);
      const statusClass = g.status === 'completed' ? 'complete' : g.status === 'stalled' ? 'stalled' : '';
      const color = g.status === 'completed' ? 'var(--green)' : g.status === 'in_progress' ? 'var(--white)' : 'var(--w40)';
      return `<div class="goal-progress ${statusClass}">
        <div class="goal-progress-header">
          <span class="goal-progress-name" style="color:${color}">${esc(g.title || '?')}</span>
          <span class="goal-progress-value">${pct}%</span>
        </div>
        <div class="goal-progress-bar"><div class="goal-progress-fill" style="width:${pct}%"></div></div>
      </div>`;
    }).join('');
  }
}

// ═══ EVOLUTION TIMELINE ═══
function renderEvolutionTimeline(mem) {
  const el = document.getElementById('evo-timeline');
  if (!el) return;

  const successes = (mem.successes || []).slice(-10);
  const failures = (mem.failures || []).slice(-5);

  // Merge and sort by timestamp
  const events = [
    ...successes.map(e => ({ ...e, _type: 'success' })),
    ...failures.map(e => ({ ...e, _type: 'failure' }))
  ].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0)).slice(-10);

  if (!events.length) {
    el.innerHTML = '<span class="card-label">No evolution events yet</span>';
    return;
  }

  el.innerHTML = `<div class="evo-timeline">${events.map(ev => {
    const dt = ev.timestamp ? new Date(ev.timestamp * 1000).toLocaleString('en', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : '';
    const isSuccess = ev._type === 'success';
    return `<div class="evo-timeline-item ${isSuccess ? 'success' : 'failure'}">
      <div class="evo-timeline-dot"></div>
      <div class="evo-timeline-header">
        <span class="evo-timeline-title">${esc((ev.title || '?').substring(0, 50))}</span>
        <span class="evo-timeline-time">${dt}</span>
      </div>
      <div class="evo-timeline-body">${esc(ev.type || '?')} ${ev.files ? '| ' + ev.files.join(', ') : ''}</div>
    </div>`;
  }).join('')}</div>`;
}

// ═══ SETTINGS ═══
function renderSettings(d) {
  const auth = d.auth || {}, models = auth.models || {};
  s('set-t1', models.tier1 || '--'); s('set-t2', models.tier2 || '--');
  s('set-budget', d.usage?.daily_budget_usd ? '$' + d.usage.daily_budget_usd.toFixed(2) + '/day' : '--');
  const authEl = document.getElementById('set-auth');
  authEl.innerHTML = `<div style="display:flex;flex-direction:column;gap:8px;font-size:12px">
    <div style="display:flex;justify-content:space-between"><span class="card-label" style="margin:0">Mode</span><span style="color:var(--w60)">${auth.mode||'--'}</span></div>
    <div style="display:flex;justify-content:space-between"><span class="card-label" style="margin:0">Source</span><span style="color:var(--w60)">${auth.source||'--'}</span></div>
    <div style="display:flex;justify-content:space-between"><span class="card-label" style="margin:0">Provider</span><span style="color:var(--w60)">${auth.provider||'--'}</span></div>
  </div>`;
  const tools = d.tools || [];
  const toolsEl = document.getElementById('set-tools');
  if (tools.length) {
    toolsEl.innerHTML = tools.map(t => `<span class="tool-chip" title="${esc(t.description||'')}">${esc(t.name)}</span>`).join('');
  }
}

// ═══ LOGS ═══
function setupLogs() {
  // Filter buttons
  document.querySelectorAll('.log-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      logFilter = btn.dataset.level;
      document.querySelectorAll('.log-filter-btn').forEach(b => b.classList.toggle('active', b === btn));
      renderLogEntries();
    });
  });

  // Clear button
  const clearBtn = document.getElementById('btn-logs-clear');
  if (clearBtn) clearBtn.addEventListener('click', () => {
    logEntries = [];
    renderLogEntries();
  });

  // Pause button
  const pauseBtn = document.getElementById('btn-logs-pause');
  if (pauseBtn) pauseBtn.addEventListener('click', () => {
    logsPaused = !logsPaused;
    pauseBtn.textContent = logsPaused ? 'Resume' : 'Pause';
  });
}

function collectLogs(d) {
  if (logsPaused) return;

  const activity = d.activity || [];
  const logs = d.logs || [];

  // Collect from activity stream
  for (const act of activity) {
    const key = act.timestamp + ':' + act.stage + ':' + (act.detail || '');
    if (!logEntries.some(e => e._key === key)) {
      const level = act.stage === 'error' ? 'error' : 'info';
      logEntries.push({
        _key: key,
        timestamp: act.timestamp,
        level: level,
        source: act.tool || 'agent',
        message: `[${act.stage}] ${act.detail || act.tool || ''}`
      });
    }
  }

  // Collect from dedicated logs array if present
  for (const log of logs) {
    const key = (log.timestamp || 0) + ':' + (log.message || log.msg || '');
    if (!logEntries.some(e => e._key === key)) {
      logEntries.push({
        _key: key,
        timestamp: log.timestamp || Date.now() / 1000,
        level: log.level || 'info',
        source: log.source || 'system',
        message: log.message || log.msg || ''
      });
    }
  }

  // Cap entries
  if (logEntries.length > MAX_LOG_ENTRIES) {
    logEntries = logEntries.slice(-MAX_LOG_ENTRIES);
  }
}

function renderLogs(_d) {
  renderLogEntries();
}

function renderLogEntries() {
  const el = document.getElementById('logs-container');
  if (!el) return;

  const filtered = logFilter === 'all' ? logEntries : logEntries.filter(e => e.level === logFilter);

  if (!filtered.length) {
    el.innerHTML = '<div class="log-empty">No log entries</div>';
    return;
  }

  const wasScrolledToBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;

  el.innerHTML = filtered.map(entry => {
    const t = entry.timestamp ? new Date(entry.timestamp * 1000).toLocaleTimeString('en', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '--:--:--';
    const lvl = entry.level || 'info';
    return `<div class="log-line"><span class="log-time">${t}</span><span class="log-level ${lvl}">${lvl.toUpperCase()}</span><span class="log-source">${esc(entry.source)}</span><span class="log-msg">${esc(entry.message)}</span></div>`;
  }).join('');

  // Auto-scroll if was at bottom
  if (wasScrolledToBottom) {
    el.scrollTop = el.scrollHeight;
  }
}

// ═══ UTILS ═══
function getEmo(e) { if(!e)return'neutral';if(e.concern>.6)return'sad';if(e.curiosity>.7)return'curious';if(e.engagement>.7&&e.confidence>.6)return'excited';if(e.engagement>.6)return'happy';if(e.confidence>.7)return'confident';if(e.concern>.4)return'worried';return'neutral'; }
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function fmtUp(s) { const h=Math.floor(s/3600),m=Math.floor(s%3600/60),sec=Math.floor(s%60); return h?`${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${m}:${String(sec).padStart(2,'0')}`; }
function fmtN(n) { return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'K':String(n); }
function s(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function bar(id, pct) { const el = document.getElementById(id); if (el) el.style.width = Math.min(100, pct) + '%'; }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
