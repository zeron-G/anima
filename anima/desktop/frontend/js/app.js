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

  renderPage(d);
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
}

// ═══ MARKDOWN RENDERER ═══
const markedInstance = new marked.Marked();
markedInstance.use({
  renderer: {
    code({text, lang}) {
      const language = (typeof hljs !== 'undefined' && hljs.getLanguage(lang)) ? lang : 'plaintext';
      const highlighted = (typeof hljs !== 'undefined') ? hljs.highlight(text, {language}).value : esc(text);
      return `<div class="code-block"><div class="code-header"><span class="code-lang">${lang||'text'}</span><button class="code-copy" onclick="window._copyCode(this)">Copy</button></div><pre><code class="hljs language-${language}">${highlighted}</code></pre></div>`;
    }
  }
});

function renderMd(text) {
  try { return markedInstance.parse(text); }
  catch { return esc(text).replace(/\n/g, '<br>'); }
}

function renderMdStreaming(text) {
  // Auto-close open constructs for safe parsing during stream
  let safe = text;
  if ((safe.match(/```/g)||[]).length % 2 !== 0) safe += '\n```';
  if ((safe.match(/\*\*/g)||[]).length % 2 !== 0) safe += '**';
  try { return markedInstance.parse(safe) + '<span class="streaming-cursor"></span>'; }
  catch { return esc(text).replace(/\n/g,'<br>') + '<span class="streaming-cursor"></span>'; }
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

// ═══ CHAT RENDERING ═══
let _lastRenderedChat = 0;

function renderChat(d) {
  const history = d.chat_history || [];
  const activity = d.activity || [];
  if (history.length === _lastRenderedChat && activity.length === lastActivityLen) return;

  const el = document.getElementById('chat-msgs');
  const isNew = history.length > _lastRenderedChat;
  _lastRenderedChat = history.length;
  lastActivityLen = activity.length;

  let html = '';

  // Chat messages
  for (const m of history) {
    const role = m.role === 'user' ? 'user' : m.role === 'system' ? 'system' : 'agent';
    const content = role === 'agent' ? renderMd(m.content) : esc(m.content);
    const actions = role === 'agent' ? `<div class="msg-actions"><button class="msg-action-btn" onclick="copyMessage(${JSON.stringify(JSON.stringify(m.content))})"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg> Copy</button></div>` : '';
    html += `<div class="msg ${role}">${content}${actions}</div>`;
  }

  // Recent activity — only show last 3 non-idle items, compact
  const recentAct = activity.slice(-6);
  let actHtml = '';
  let actCount = 0;
  for (const a of recentAct) {
    const stage = a.stage || '';
    if (stage === 'heartbeat' || stage === 'idle' || stage === 'self_thought' || stage === 'delegation_result') continue;
    if (actCount >= 3) break;
    const detail = a.detail || '';
    const tool = a.tool || '';
    if (stage === 'executing' || stage === 'tool_done') {
      actHtml += `<details class="msg-block tool-block"><summary><span style="color:#7ec8e3">⚙</span> ${esc(tool||'tool')}<span class="block-status ${stage==='tool_done'?'done':'running'}">${stage==='tool_done'?'done':'running'}</span></summary><div class="block-content">${esc(detail)}</div></details>`;
    } else if (stage === 'thinking') {
      actHtml += `<details class="msg-block thinking-block"><summary><span style="color:#c4a7e7">◐</span> Thinking...<span class="block-spinner"></span></summary><div class="block-content">${esc(detail)}</div></details>`;
    }
    actCount++;
  }
  html += actHtml;

  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;

  // Bubble + TTS for new messages
  if (isNew && history.length > 0) {
    const last = history[history.length - 1];
    if (last && last.role !== 'user' && last.role !== 'system') {
      showBubble(last.content);
      if (voiceManager?.autoTTS && !evaProcessing && last.tts_url && !playedTTS.has(history.length-1)) {
        playedTTS.add(history.length-1);
        voiceManager.playUrl(last.tts_url);
      }
    }
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
    // Use streaming endpoint
    fetch('/api/chat/stream', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})
    }).catch(err => {
      // Fallback to non-streaming
      console.warn('Stream failed, using fallback:', err);
      fetch('/api/chat', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text})
      }).catch(console.error);
    });
  }
}

// ═══ OVERVIEW ═══
function renderOverview(d) {
  const a = d.agent || {}, hb = d.heartbeat || {}, sys = d.system || {}, emo = d.emotion || {};
  const usage = d.llm_usage_summary || {}, u = d.usage || {};
  s('ov-status', a.status === 'alive' ? 'Online' : 'Offline');
  s('ov-uptime', fmtUp(d.uptime_s || 0));
  s('ov-tick', '#' + (hb.tick_count || 0));
  s('ov-queue', (d.event_queue || {}).size || 0);
  s('ov-cpu', (sys.cpu_percent || 0).toFixed(0) + '%'); bar('ov-cpu-bar', sys.cpu_percent || 0);
  s('ov-mem', (sys.memory_percent || 0).toFixed(0) + '%'); bar('ov-mem-bar', sys.memory_percent || 0);
  s('ov-disk', (sys.disk_percent || 0).toFixed(0) + '%'); bar('ov-disk-bar', sys.disk_percent || 0);
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

  // Goals
  const goalsEl = document.getElementById('evo-goals');
  const goals = mem.goals || [];
  if (goals.length) {
    goalsEl.innerHTML = goals.map(g => {
      const pct = Math.round((g.progress || 0) * 100);
      const color = g.status === 'completed' ? 'var(--green)' : g.status === 'in_progress' ? 'var(--white)' : 'var(--w40)';
      return `<div style="padding:6px 0;border-bottom:1px solid var(--border);font-size:12px">
        <div style="display:flex;justify-content:space-between">
          <span style="color:${color}">${esc(g.title || '?')}</span>
          <span style="color:var(--w20)">${pct}%</span>
        </div>
        <div class="card-bar" style="margin-top:4px"><div class="card-bar-fill" style="width:${pct}%"></div></div>
      </div>`;
    }).join('');
  }
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

// ═══ UTILS ═══
function getEmo(e) { if(!e)return'neutral';if(e.concern>.6)return'sad';if(e.curiosity>.7)return'curious';if(e.engagement>.7&&e.confidence>.6)return'excited';if(e.engagement>.6)return'happy';if(e.confidence>.7)return'confident';if(e.concern>.4)return'worried';return'neutral'; }
function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function fmtUp(s) { const h=Math.floor(s/3600),m=Math.floor(s%3600/60),sec=Math.floor(s%60); return h?`${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${m}:${String(sec).padStart(2,'0')}`; }
function fmtN(n) { return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'K':String(n); }
function s(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function bar(id, pct) { const el = document.getElementById(id); if (el) el.style.width = Math.min(100, pct) + '%'; }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
