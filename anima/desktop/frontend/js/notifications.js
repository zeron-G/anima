/**
 * ANIMA Desktop — Toast Notification System
 *
 * Provides slide-in toast notifications with auto-dismiss,
 * progress bars, and snapshot-based monitoring for degradation,
 * evolution events, and system alerts.
 */

// ═══ CONSTANTS ═══
const MAX_VISIBLE = 4;
const TYPES = {
  info:    { color: '#60c8d0', icon: '\u2139' },   // i
  warning: { color: '#fab005', icon: '\u26A0' },   // warning sign
  error:   { color: '#ff3b30', icon: '\u2715' },   // x
  success: { color: '#34d399', icon: '\u2713' },   // checkmark
};

let _container = null;
let _toasts = [];     // active toast elements, oldest first
let _prevSnapshot = null;

// ═══ CONTAINER ═══
function ensureContainer() {
  if (_container) return _container;
  _container = document.createElement('div');
  _container.className = 'toast-container';
  Object.assign(_container.style, {
    position: 'fixed',
    top: '16px',
    right: '16px',
    zIndex: '10000',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
    pointerEvents: 'none',
    maxWidth: '360px',
    width: '100%',
  });
  document.body.appendChild(_container);
  injectStyles();
  return _container;
}

// ═══ STYLES ═══
function injectStyles() {
  if (document.getElementById('toast-styles')) return;
  const style = document.createElement('style');
  style.id = 'toast-styles';
  style.textContent = `
    .toast {
      pointer-events: auto;
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 12px 14px;
      background: rgba(10, 10, 14, .92);
      backdrop-filter: blur(20px);
      border: 1px solid rgba(255,255,255,.08);
      border-radius: 12px;
      font-family: 'Inter','SF Pro Display','Segoe UI',system-ui,-apple-system,sans-serif;
      font-size: 13px;
      color: rgba(255,255,255,.85);
      animation: toastSlideIn .3s cubic-bezier(.2,1,.3,1) forwards;
      position: relative;
      overflow: hidden;
      box-shadow: 0 4px 24px rgba(0,0,0,.5);
    }
    .toast.removing {
      animation: toastSlideOut .25s ease forwards;
    }
    @keyframes toastSlideIn {
      from { opacity: 0; transform: translateX(80px); }
      to   { opacity: 1; transform: translateX(0); }
    }
    @keyframes toastSlideOut {
      from { opacity: 1; transform: translateX(0); }
      to   { opacity: 0; transform: translateX(80px); }
    }
    .toast-icon {
      flex-shrink: 0;
      width: 22px;
      height: 22px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 6px;
      font-size: 13px;
      font-weight: 600;
    }
    .toast-body {
      flex: 1;
      min-width: 0;
      line-height: 1.5;
      word-wrap: break-word;
    }
    .toast-close {
      flex-shrink: 0;
      background: none;
      border: none;
      color: rgba(255,255,255,.3);
      font-size: 16px;
      cursor: pointer;
      padding: 0 2px;
      line-height: 1;
      transition: color .15s;
    }
    .toast-close:hover {
      color: rgba(255,255,255,.7);
    }
    .toast-progress {
      position: absolute;
      bottom: 0;
      left: 0;
      height: 2px;
      border-radius: 0 0 12px 12px;
      transition: width linear;
    }
  `;
  document.head.appendChild(style);
}

// ═══ SHOW TOAST ═══
/**
 * Display a toast notification.
 * @param {string} message  - Text to display
 * @param {string} type     - 'info' | 'warning' | 'error' | 'success'
 * @param {number} duration - Auto-dismiss in ms (0 = manual close only)
 */
export function showToast(message, type = 'info', duration = 5000) {
  const container = ensureContainer();
  const cfg = TYPES[type] || TYPES.info;

  // Build toast element
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.style.borderColor = `${cfg.color}22`;

  // Icon
  const icon = document.createElement('span');
  icon.className = 'toast-icon';
  icon.textContent = cfg.icon;
  icon.style.background = `${cfg.color}18`;
  icon.style.color = cfg.color;
  toast.appendChild(icon);

  // Body
  const body = document.createElement('span');
  body.className = 'toast-body';
  body.textContent = message;
  toast.appendChild(body);

  // Close button
  const close = document.createElement('button');
  close.className = 'toast-close';
  close.textContent = '\u00D7';
  close.addEventListener('click', () => removeToast(toast));
  toast.appendChild(close);

  // Progress bar
  if (duration > 0) {
    const progress = document.createElement('div');
    progress.className = 'toast-progress';
    progress.style.background = cfg.color;
    progress.style.width = '100%';
    progress.style.transitionDuration = duration + 'ms';
    toast.appendChild(progress);

    // Trigger reflow then animate to 0
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        progress.style.width = '0%';
      });
    });

    toast._timer = setTimeout(() => removeToast(toast), duration);
  }

  // Enforce max visible — remove oldest
  while (_toasts.length >= MAX_VISIBLE) {
    removeToast(_toasts[0], true);
  }

  _toasts.push(toast);
  container.appendChild(toast);

  return toast;
}

function removeToast(toast, immediate = false) {
  if (toast._removed) return;
  toast._removed = true;
  if (toast._timer) clearTimeout(toast._timer);

  const idx = _toasts.indexOf(toast);
  if (idx !== -1) _toasts.splice(idx, 1);

  if (immediate) {
    toast.remove();
  } else {
    toast.classList.add('removing');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
    // Safety fallback
    setTimeout(() => { if (toast.parentNode) toast.remove(); }, 350);
  }
}

// ═══ MONITOR DEGRADATION ═══
/**
 * Compare current snapshot vs previous and fire toasts on notable changes.
 * Call this on every WebSocket snapshot.
 * @param {object} data - Full WebSocket snapshot
 */
export function monitorDegradation(data) {
  const prev = _prevSnapshot;
  _prevSnapshot = data;
  if (!prev) return;   // First snapshot, nothing to compare

  const llm = data.llm_status || {};
  const prevLlm = prev.llm_status || {};
  const sys = data.system || {};
  const evo = data.evolution || {};
  const prevEvo = prev.evolution || {};

  // ── LLM Degradation ──
  if (llm.degraded && !prevLlm.degraded) {
    showToast(
      `LLM degraded — switched to ${llm.active_model || 'fallback'} (${llm.consecutive_failures} failures)`,
      'warning', 8000
    );
  }
  if (!llm.degraded && prevLlm.degraded) {
    showToast(
      `LLM recovered — active model: ${llm.active_model || 'primary'}`,
      'success', 5000
    );
  }

  // ── Circuit breaker ──
  if (llm.circuit_open && !prevLlm.circuit_open) {
    showToast('LLM circuit breaker OPEN — all calls blocked', 'error', 10000);
  }
  if (!llm.circuit_open && prevLlm.circuit_open) {
    showToast('LLM circuit breaker closed — calls resumed', 'success', 5000);
  }

  // ── Evolution events ──
  const evoSucc = evo.successes || 0;
  const prevSucc = prevEvo.successes || 0;
  const evoFail = evo.failures || 0;
  const prevFail = prevEvo.failures || 0;

  if (evoSucc > prevSucc) {
    showToast(`Evolution succeeded (${evoSucc} total)`, 'success', 6000);
  }
  if (evoFail > prevFail) {
    showToast(`Evolution failed (${evoFail} total)`, 'error', 6000);
  }

  // ── System alerts ──
  if (sys.cpu_percent > 90 && (prev.system?.cpu_percent || 0) <= 90) {
    showToast(`High CPU usage: ${sys.cpu_percent.toFixed(0)}%`, 'warning', 7000);
  }
  if (sys.disk_percent > 95 && (prev.system?.disk_percent || 0) <= 95) {
    showToast(`Disk nearly full: ${sys.disk_percent.toFixed(0)}%`, 'error', 10000);
  }
  if (sys.memory_percent > 90 && (prev.system?.memory_percent || 0) <= 90) {
    showToast(`High memory usage: ${sys.memory_percent.toFixed(0)}%`, 'warning', 7000);
  }

  // ── Budget ──
  const usage = data.usage || {};
  const prevUsage = prev.usage || {};
  if (!usage.budget_ok && prevUsage.budget_ok) {
    showToast('Budget limit exceeded', 'error', 10000);
  }
}
