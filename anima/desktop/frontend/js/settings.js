/**
 * ANIMA Desktop — Settings Panel
 *
 * Builds and updates the settings page with live snapshot data.
 * Configuration changes POST to /api/config immediately.
 */

// ═══ STATE ═══
let _initialized = false;
let _saveIndicators = {};   // key -> timeout id

const MODEL_OPTIONS = [
  'claude-opus-4-6',
  'claude-sonnet-4-6',
  'openai/gpt-5.4',
  'openai/o4-mini',
  'local/',
];

// ═══ HELPERS ═══
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function postConfig(key, value) {
  return fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, value }),
  });
}

function flashSaved(el) {
  if (!el) return;
  const indicator = el.querySelector('.setting-saved');
  if (!indicator) return;
  indicator.style.opacity = '1';
  const key = el.dataset.settingKey || '';
  if (_saveIndicators[key]) clearTimeout(_saveIndicators[key]);
  _saveIndicators[key] = setTimeout(() => { indicator.style.opacity = '0'; }, 2000);
}

// ═══ INJECT STYLES ═══
function injectStyles() {
  if (document.getElementById('settings-styles')) return;
  const style = document.createElement('style');
  style.id = 'settings-styles';
  style.textContent = `
    .setting-group {
      background: rgba(255,255,255,.04);
      border: 1px solid rgba(255,255,255,.06);
      border-radius: 14px;
      padding: 18px 20px;
      margin-bottom: 12px;
    }
    .setting-group-title {
      font-size: 13px;
      font-weight: 600;
      color: rgba(255,255,255,.5);
      letter-spacing: .03em;
      text-transform: uppercase;
      margin-bottom: 14px;
    }
    .setting-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 0;
      border-bottom: 1px solid rgba(255,255,255,.04);
      font-size: 13px;
      min-height: 38px;
    }
    .setting-row:last-child {
      border-bottom: none;
    }
    .setting-label {
      color: rgba(255,255,255,.6);
      flex-shrink: 0;
      margin-right: 12px;
    }
    .setting-value {
      color: rgba(255,255,255,.85);
      font-family: 'SF Mono','Cascadia Code','Consolas',monospace;
      font-size: 12px;
      text-align: right;
      word-break: break-all;
    }
    .setting-saved {
      font-size: 11px;
      color: #34d399;
      margin-left: 8px;
      opacity: 0;
      transition: opacity .2s;
      flex-shrink: 0;
    }
    .select-model {
      background: rgba(255,255,255,.06);
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 8px;
      color: rgba(255,255,255,.85);
      font-family: 'SF Mono','Cascadia Code','Consolas',monospace;
      font-size: 12px;
      padding: 5px 10px;
      outline: none;
      cursor: pointer;
      transition: border-color .2s;
    }
    .select-model:hover, .select-model:focus {
      border-color: rgba(255,255,255,.2);
    }
    .select-model option {
      background: #111;
      color: #eee;
    }
    .toggle-switch {
      position: relative;
      width: 40px;
      height: 22px;
      background: rgba(255,255,255,.1);
      border-radius: 11px;
      cursor: pointer;
      transition: background .2s;
      flex-shrink: 0;
    }
    .toggle-switch.on {
      background: #60c8d0;
    }
    .toggle-switch::after {
      content: '';
      position: absolute;
      top: 2px;
      left: 2px;
      width: 18px;
      height: 18px;
      background: #fff;
      border-radius: 50%;
      transition: transform .2s;
    }
    .toggle-switch.on::after {
      transform: translateX(18px);
    }
    .setting-badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 500;
      letter-spacing: .02em;
    }
    .setting-badge.green {
      background: rgba(52,211,153,.15);
      color: #34d399;
    }
    .setting-badge.red {
      background: rgba(255,59,48,.15);
      color: #ff3b30;
    }
    .setting-badge.yellow {
      background: rgba(250,176,5,.15);
      color: #fab005;
    }
    .setting-badge.cyan {
      background: rgba(96,200,208,.15);
      color: #60c8d0;
    }
    .setting-badge.muted {
      background: rgba(255,255,255,.06);
      color: rgba(255,255,255,.4);
    }
    .setting-tasks {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-top: 4px;
    }
    .setting-task-item {
      font-size: 11px;
      color: rgba(255,255,255,.5);
      font-family: 'SF Mono','Cascadia Code','Consolas',monospace;
      padding: 4px 8px;
      background: rgba(255,255,255,.03);
      border-radius: 6px;
    }
    .setting-readonly {
      color: rgba(255,255,255,.45);
      font-family: 'SF Mono','Cascadia Code','Consolas',monospace;
      font-size: 12px;
      background: rgba(255,255,255,.03);
      border: 1px solid rgba(255,255,255,.05);
      border-radius: 6px;
      padding: 4px 10px;
      max-width: 220px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  `;
  document.head.appendChild(style);
}

// ═══ BUILD SETTINGS DOM ═══
function buildSettingsDOM(container) {
  container.innerHTML = '';

  // 1. LLM Configuration
  const llmGroup = group('LLM Configuration');
  llmGroup.innerHTML += `
    <div class="setting-row" data-setting-key="llm.tier1.model">
      <span class="setting-label">Tier 1 Model</span>
      <div style="display:flex;align-items:center;gap:6px">
        <select class="select-model" id="set-tier1-select"></select>
        <span class="setting-saved">Saved &#x2713;</span>
      </div>
    </div>
    <div class="setting-row" data-setting-key="llm.tier2.model">
      <span class="setting-label">Tier 2 Model</span>
      <div style="display:flex;align-items:center;gap:6px">
        <select class="select-model" id="set-tier2-select"></select>
        <span class="setting-saved">Saved &#x2713;</span>
      </div>
    </div>
    <div class="setting-row">
      <span class="setting-label">Active Model</span>
      <span class="setting-value" id="set-active-model">--</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Degradation</span>
      <span id="set-degradation-badge"></span>
    </div>
  `;
  container.appendChild(llmGroup);

  // Populate dropdowns
  populateSelect('set-tier1-select', MODEL_OPTIONS);
  populateSelect('set-tier2-select', MODEL_OPTIONS);

  // Wire change handlers
  document.getElementById('set-tier1-select').addEventListener('change', (e) => {
    postConfig('llm.tier1.model', e.target.value).then(() => {
      flashSaved(e.target.closest('.setting-row'));
    });
  });
  document.getElementById('set-tier2-select').addEventListener('change', (e) => {
    postConfig('llm.tier2.model', e.target.value).then(() => {
      flashSaved(e.target.closest('.setting-row'));
    });
  });

  // 2. Local Model
  const localGroup = group('Local Model');
  localGroup.innerHTML += `
    <div class="setting-row">
      <span class="setting-label">Server Path</span>
      <span class="setting-readonly" id="set-local-server-path" title="">--</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Model Path</span>
      <span class="setting-readonly" id="set-local-model-path" title="">--</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Context Size</span>
      <span class="setting-value" id="set-local-ctx">--</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Server Status</span>
      <span id="set-local-status-badge"></span>
    </div>
  `;
  container.appendChild(localGroup);

  // 3. Idle Scheduler
  const idleGroup = group('Idle Scheduler');
  idleGroup.innerHTML += `
    <div class="setting-row" data-setting-key="idle_scheduler.enabled">
      <span class="setting-label">Enabled</span>
      <div style="display:flex;align-items:center;gap:6px">
        <div class="toggle-switch" id="set-idle-toggle"></div>
        <span class="setting-saved">Saved &#x2713;</span>
      </div>
    </div>
    <div class="setting-row">
      <span class="setting-label">Idle Score</span>
      <span class="setting-value" id="set-idle-score">--</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Idle Level</span>
      <span id="set-idle-level-badge"></span>
    </div>
    <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:6px">
      <span class="setting-label">Running Tasks</span>
      <div class="setting-tasks" id="set-idle-tasks"></div>
    </div>
    <div class="setting-row">
      <span class="setting-label">Hourly Spend</span>
      <span class="setting-value" id="set-idle-spend">--</span>
    </div>
  `;
  container.appendChild(idleGroup);

  // Wire toggle
  document.getElementById('set-idle-toggle').addEventListener('click', function() {
    const isOn = this.classList.toggle('on');
    postConfig('idle_scheduler.enabled', isOn).then(() => {
      flashSaved(this.closest('.setting-row'));
    });
  });

  // 4. Heartbeat
  const hbGroup = group('Heartbeat');
  hbGroup.innerHTML += `
    <div class="setting-row">
      <span class="setting-label">Script Interval</span>
      <span class="setting-value" id="set-hb-script">--</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">LLM Interval</span>
      <span class="setting-value" id="set-hb-llm">--</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Major Interval</span>
      <span class="setting-value" id="set-hb-major">--</span>
    </div>
  `;
  container.appendChild(hbGroup);

  // 5. Network
  const netGroup = group('Network');
  netGroup.innerHTML += `
    <div class="setting-row">
      <span class="setting-label">Node ID</span>
      <span class="setting-value" id="set-net-node" style="font-size:11px">--</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Alive Peers</span>
      <span class="setting-value" id="set-net-alive">0</span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Connection</span>
      <span id="set-net-status-badge"></span>
    </div>
  `;
  container.appendChild(netGroup);

  // 6. Authentication
  const authGroup = group('Authentication');
  authGroup.innerHTML += `
    <div class="setting-row">
      <span class="setting-label">Auth Mode</span>
      <span id="set-auth-mode-badge"></span>
    </div>
    <div class="setting-row">
      <span class="setting-label">Token Status</span>
      <span class="setting-value" id="set-auth-token">--</span>
    </div>
  `;
  container.appendChild(authGroup);
}

function group(title) {
  const el = document.createElement('div');
  el.className = 'setting-group';
  el.innerHTML = `<div class="setting-group-title">${esc(title)}</div>`;
  return el;
}

function populateSelect(id, options) {
  const sel = document.getElementById(id);
  if (!sel) return;
  sel.innerHTML = '';
  for (const opt of options) {
    const o = document.createElement('option');
    o.value = opt;
    o.textContent = opt;
    sel.appendChild(o);
  }
}

function badge(text, variant) {
  return `<span class="setting-badge ${variant}">${esc(text)}</span>`;
}

function setVal(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setReadonly(id, text) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = text || '--';
    el.title = text || '';
  }
}

// ═══ INIT ═══
/**
 * One-time setup. Call after DOMContentLoaded.
 * Builds the settings DOM inside the existing settings page container.
 */
export function initSettings() {
  if (_initialized) return;
  injectStyles();

  const container = document.querySelector('#page-settings .page-scroll');
  if (!container) return;
  buildSettingsDOM(container);
  _initialized = true;
}

// ═══ RENDER ═══
/**
 * Update settings display with latest snapshot data.
 * @param {object} data      - Full WebSocket snapshot
 * @param {Element} container - (unused, kept for API compat) settings container element
 */
export function renderSettings(data, container) {
  if (!_initialized) {
    initSettings();
  }

  const llm = data.llm_status || {};
  const sys = data.system || {};
  const idle = data.idle_scheduler || {};
  const usage = data.usage || {};
  const net = data.network || {};
  const auth = data.auth || {};
  const hb = data.heartbeat || {};
  const localModel = data.local_model || {};
  const models = auth.models || {};

  // ── 1. LLM Configuration ──
  const t1Select = document.getElementById('set-tier1-select');
  const t2Select = document.getElementById('set-tier2-select');
  // Only update selects if user isn't actively focusing them
  if (t1Select && document.activeElement !== t1Select && models.tier1) {
    setSelectValue(t1Select, models.tier1);
  }
  if (t2Select && document.activeElement !== t2Select && models.tier2) {
    setSelectValue(t2Select, models.tier2);
  }

  setVal('set-active-model', llm.active_model || '--');

  const degradEl = document.getElementById('set-degradation-badge');
  if (degradEl) {
    if (llm.circuit_open) {
      degradEl.innerHTML = badge('Circuit Open', 'red');
    } else if (llm.degraded) {
      degradEl.innerHTML = badge(`Degraded (${llm.consecutive_failures || 0} failures)`, 'yellow');
    } else {
      degradEl.innerHTML = badge('Healthy', 'green');
    }
  }

  // ── 2. Local Model ──
  setReadonly('set-local-server-path', localModel.server_path);
  setReadonly('set-local-model-path', localModel.model_path);
  setVal('set-local-ctx', localModel.context_size ? String(localModel.context_size) : '--');

  const localStatusEl = document.getElementById('set-local-status-badge');
  if (localStatusEl) {
    const running = localModel.running || false;
    localStatusEl.innerHTML = badge(running ? 'Running' : 'Stopped', running ? 'green' : 'muted');
  }

  // ── 3. Idle Scheduler ──
  const idleToggle = document.getElementById('set-idle-toggle');
  if (idleToggle) {
    // Only update if not mid-click
    const enabled = idle.enabled !== undefined ? idle.enabled : false;
    idleToggle.classList.toggle('on', enabled);
  }

  setVal('set-idle-score', idle.idle_score !== undefined ? idle.idle_score.toFixed(2) : '--');

  const idleLevelEl = document.getElementById('set-idle-level-badge');
  if (idleLevelEl) {
    const level = idle.idle_level || '--';
    const variant = level === 'active' ? 'cyan' : level === 'idle' ? 'green' : level === 'deep_idle' ? 'muted' : 'muted';
    idleLevelEl.innerHTML = badge(level, variant);
  }

  const tasksEl = document.getElementById('set-idle-tasks');
  if (tasksEl) {
    const tasks = idle.running_tasks || [];
    if (tasks.length) {
      tasksEl.innerHTML = tasks.map(t =>
        `<div class="setting-task-item">${esc(typeof t === 'string' ? t : t.name || JSON.stringify(t))}</div>`
      ).join('');
    } else {
      tasksEl.innerHTML = '<div class="setting-task-item" style="color:rgba(255,255,255,.25)">None</div>';
    }
  }

  setVal('set-idle-spend', idle.hourly_spend !== undefined ? '$' + idle.hourly_spend.toFixed(4) + '/hr' : '--');

  // ── 4. Heartbeat ──
  setVal('set-hb-script', hb.script_interval ? hb.script_interval + 's' : '--');
  setVal('set-hb-llm', hb.llm_interval ? hb.llm_interval + 's' : '--');
  setVal('set-hb-major', hb.major_interval ? hb.major_interval + 's' : '--');

  // ── 5. Network ──
  setVal('set-net-node', net.node_id || '--');
  setVal('set-net-alive', net.alive_count || 0);

  const netStatusEl = document.getElementById('set-net-status-badge');
  if (netStatusEl) {
    const enabled = net.enabled !== undefined ? net.enabled : (net.alive_count > 0);
    netStatusEl.innerHTML = badge(enabled ? 'Connected' : 'Disconnected', enabled ? 'green' : 'muted');
  }

  // ── 6. Authentication ──
  const authModeEl = document.getElementById('set-auth-mode-badge');
  if (authModeEl) {
    const mode = auth.mode || '--';
    const variant = mode === 'OAuth' ? 'cyan' : mode === 'API Key' ? 'green' : 'muted';
    authModeEl.innerHTML = badge(mode, variant);
  }

  const tokenEl = document.getElementById('set-auth-token');
  if (tokenEl) {
    // Mask token: show source and masked status
    const source = auth.source || auth.provider || '';
    const hasToken = auth.mode && auth.mode !== '--';
    tokenEl.textContent = hasToken ? (source ? source + ' ' : '') + '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : '--';
  }
}

function setSelectValue(sel, value) {
  // If the value matches an option, select it; otherwise add it
  let found = false;
  for (const opt of sel.options) {
    if (opt.value === value) { found = true; break; }
  }
  if (!found && value) {
    const o = document.createElement('option');
    o.value = value;
    o.textContent = value;
    sel.appendChild(o);
  }
  sel.value = value;
}
