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
  document.getElementById('chat-typing').className = 'chat-typing' + (isProcessing ? ' on' : '');
  evaProcessing = isProcessing;

  renderPage(d);
}

function renderPage(d) {
  if (currentPage === 'overview') renderOverview(d);
  else if (currentPage === 'network') renderNetwork(d);
  else if (currentPage === 'evolution') renderEvolution(d);
  else if (currentPage === 'settings') renderSettings(d);
}

// ═══ CHAT — shows messages + real-time activity interleaved ═══
function renderChat(d) {
  const history = d.chat_history || [];
  const activity = d.activity || [];

  // Check if anything changed
  const chatChanged = history.length !== lastChatLen;
  const actChanged = activity.length !== lastActivityLen;
  if (!chatChanged && !actChanged) return;

  const prevChatLen = lastChatLen;
  lastChatLen = history.length;
  lastActivityLen = activity.length;

  const el = document.getElementById('chat-msgs');

  // Build unified timeline: messages + recent activity
  let html = '';

  // All chat messages
  for (const m of history) {
    const role = m.role === 'user' ? 'user' : m.role === 'system' ? 'system' : 'agent';
    html += `<div class="msg ${role}">${role === 'agent' ? fmtMsg(m.content) : esc(m.content)}</div>`;
  }

  // Recent activity entries (show last 10 as system messages below chat)
  const recentAct = activity.slice(-10);
  for (const a of recentAct) {
    const stage = a.stage || '';
    const detail = a.detail || '';
    if (stage === 'heartbeat') continue; // Skip heartbeat noise
    const icon = stageIcon(stage);
    html += `<div class="msg activity">${icon} ${esc(stage)} ${esc(detail)}</div>`;
  }

  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;

  // New agent message → bubble + TTS (only if not still processing)
  if (chatChanged && history.length > prevChatLen) {
    const last = history[history.length - 1];
    if (last && last.role !== 'user' && last.role !== 'system') {
      showBubble(last.content);
      if (voiceManager?.autoTTS && !evaProcessing) {
        const msgIdx = history.length - 1;
        if (last.tts_url && !playedTTS.has(msgIdx)) {
          playedTTS.add(msgIdx);
          voiceManager.playUrl(last.tts_url);
        }
        // If no tts_url yet, it'll come on next WS push — handled below
      }
    }
  }

  // Check for newly available tts_url on recent agent messages
  if (voiceManager?.autoTTS && !evaProcessing) {
    for (let i = Math.max(0, history.length - 3); i < history.length; i++) {
      const m = history[i];
      if (m.role === 'agent' && m.tts_url && !playedTTS.has(i)) {
        playedTTS.add(i);
        voiceManager.playUrl(m.tts_url);
        break;
      }
    }
  }
}

function stageIcon(stage) {
  switch (stage) {
    case 'thinking': return '◐';
    case 'deciding': return '◑';
    case 'executing': return '▸';
    case 'responding': return '◉';
    case 'tool_call': return '⚙';
    default: return '·';
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

// ═══ CHAT INPUT ═══
function setupChat() {
  document.getElementById('btn-send').addEventListener('click', send);
  document.getElementById('chat-text').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
}

function send() {
  const el = document.getElementById('chat-text');
  const t = el.value.trim(); if (!t) return; el.value = '';
  fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: t }) }).catch(console.error);
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
function fmtMsg(t) { const cb=[]; let s=t.replace(/```(\w*)\n?([\s\S]*?)```/g,(_,l,c)=>{cb.push(`<pre><code>${esc(c)}</code></pre>`);return`\x00C${cb.length-1}\x00`;}); const ic=[]; s=s.replace(/`([^`]+)`/g,(_,c)=>{ic.push(`<code style="background:var(--w05);padding:1px 4px;border-radius:4px">${esc(c)}</code>`);return`\x00I${ic.length-1}\x00`;}); s=esc(s); s=s.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>'); s=s.replace(/\n/g,'<br>'); cb.forEach((b,i)=>s=s.replace(`\x00C${i}\x00`,b)); ic.forEach((b,i)=>s=s.replace(`\x00I${i}\x00`,b)); return s; }
function fmtUp(s) { const h=Math.floor(s/3600),m=Math.floor(s%3600/60),sec=Math.floor(s%60); return h?`${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${m}:${String(sec).padStart(2,'0')}`; }
function fmtN(n) { return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'K':String(n); }
function s(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function bar(id, pct) { const el = document.getElementById(id); if (el) el.style.width = Math.min(100, pct) + '%'; }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
