/**
 * Shared frontend utilities: escaping, formatters, dates, DOM helpers,
 * toasts, modal helpers and the SVG icon set.
 *
 * Everything here is side-effect free at import time (safe to import from
 * node for tests); functions touching the DOM only do so when called.
 */

/* ── Constants ─────────────────────────────────────────────────── */

export const DAYS_SHORT = Object.freeze(['Dom', 'Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab']);
export const MONTHS_SHORT = Object.freeze([
  'gen', 'feb', 'mar', 'apr', 'mag', 'giu', 'lug', 'ago', 'set', 'ott', 'nov', 'dic',
]);
export const TOAST_DURATION_MS = 3500;
export const TOAST_TYPES = Object.freeze(['info', 'success', 'error', 'warning']);
const ISO_DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
const HHMM_RE = /^\d{1,2}:\d{2}$/;

/* ── Escaping ──────────────────────────────────────────────────── */

/** Escape a value for safe insertion into HTML. */
export function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ── Formatters ────────────────────────────────────────────────── */

/** "HH:MM" passthrough, or an ISO datetime → local "HH:MM". */
export function fmtTime(val) {
  if (!val) return '';
  const s = String(val);
  if (HHMM_RE.test(s)) return s.length === 4 ? '0' + s : s;
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
}

/** ISO datetime → "16 set 14:32" (local). */
export function fmtDateTime(val) {
  if (!val) return '';
  const d = new Date(String(val));
  if (isNaN(d.getTime())) return String(val);
  return d.getDate() + ' ' + MONTHS_SHORT[d.getMonth()] + ' ' +
    d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
}

/** Hours → compact string: 8 → "8", 7.5 → "7.5", 0.25 → "0.25", NaN → "—". */
export function fmtOre(val) {
  const n = parseFloat(val);
  if (isNaN(n)) return '—';
  const s = n.toFixed(2);
  return s.endsWith('.00') ? String(Math.round(n)) : s.replace(/0$/, '');
}

/** Minutes → "30 min" / "1 h 30 min" / "2 h". */
export function fmtDuration(minutes) {
  const m = parseInt(minutes, 10);
  if (isNaN(m) || m <= 0) return '';
  const h = Math.floor(m / 60);
  const rest = m % 60;
  if (h === 0) return rest + ' min';
  return rest === 0 ? h + ' h' : h + ' h ' + rest + ' min';
}

/** "3 voci" / "1 voce". */
export function pluralize(n, singular, plural) {
  const count = Number(n) || 0;
  return count + ' ' + (count === 1 ? singular : plural);
}

/* ── Dates ─────────────────────────────────────────────────────── */

/** Browser-local date as YYYY-MM-DD (prefer state.config.today when available). */
export function todayISO(date) {
  const d = date instanceof Date ? date : new Date();
  const pad = (v) => String(v).padStart(2, '0');
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

/** True when the string is a YYYY-MM-DD day. */
export function isISODay(val) {
  return typeof val === 'string' && ISO_DAY_RE.test(val);
}

/**
 * "Oggi · Mar 16 set" / "Lun 15 set" (Italian short names).
 * @param {string} iso        YYYY-MM-DD
 * @param {string} [todayIso] reference "today" (defaults to browser today)
 */
export function dayLabel(iso, todayIso) {
  if (!isISODay(iso)) return String(iso || '');
  const [y, m, d] = iso.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  const base = DAYS_SHORT[date.getDay()] + ' ' + date.getDate() + ' ' + MONTHS_SHORT[date.getMonth()];
  return iso === (todayIso || todayISO()) ? 'Oggi · ' + base : base;
}

/** The server's notion of today (timezone-aware) with browser fallback. */
export function serverToday(state) {
  const cfg = (state && state.config) || {};
  return isISODay(cfg.today) ? cfg.today : todayISO();
}

/** The day currently shown: state.selectedDate || server today. */
export function currentDate(state) {
  return isISODay(state && state.selectedDate) ? state.selectedDate : serverToday(state);
}

/** True when the shown day is today. */
export function isToday(state) {
  return currentDate(state) === serverToday(state);
}

/* ── Functions ─────────────────────────────────────────────────── */

/** Classic trailing debounce. Returned fn has .cancel() and .flush(). */
export function debounce(fn, wait) {
  let timer = null;
  let lastArgs = null;
  const debounced = function (...args) {
    lastArgs = args;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => { timer = null; fn.apply(this, lastArgs); }, wait);
  };
  debounced.cancel = () => { if (timer) clearTimeout(timer); timer = null; };
  debounced.flush = function () {
    if (!timer) return;
    clearTimeout(timer);
    timer = null;
    fn.apply(this, lastArgs);
  };
  return debounced;
}

/* ── DOM helpers ───────────────────────────────────────────────── */

/** querySelector shortcut (root defaults to document). */
export function qs(selector, root) {
  return (root || document).querySelector(selector);
}

/** querySelectorAll → real Array. */
export function qsa(selector, root) {
  return Array.from((root || document).querySelectorAll(selector));
}

/** document.getElementById shortcut. */
export function byId(id) {
  return document.getElementById(id);
}

function applyProp(node, key, value) {
  if (value == null || value === false) return;
  if (key === 'class' || key === 'className') node.className = value;
  else if (key === 'text') node.textContent = value;
  else if (key === 'html') node.innerHTML = value; // trusted markup only (icons)
  else if (key === 'dataset') Object.entries(value).forEach(([k, v]) => { node.dataset[k] = v; });
  else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
  else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
  else if (key in node && typeof value !== 'string') node[key] = value;
  else node.setAttribute(key, value === true ? '' : value);
}

/**
 * Build a DOM element without innerHTML.
 *   el('button', { class: 'btn', onClick: fn, 'aria-label': 'x' }, iconEl('x'), 'Chiudi')
 * Children: strings → ALWAYS text nodes (never markup, so untrusted data is safe);
 * Nodes appended; arrays flattened; null/false skipped. Use iconEl() for icons.
 * Props: class/className, text, html (trusted static markup only), dataset, style,
 * on<Event>, attributes.
 */
export function el(tag, props, ...children) {
  const node = document.createElement(tag);
  Object.entries(props || {}).forEach(([key, value]) => applyProp(node, key, value));
  children.flat(Infinity).forEach((child) => {
    if (child == null || child === false) return;
    if (child instanceof Node) node.appendChild(child);
    else node.appendChild(document.createTextNode(String(child)));
  });
  return node;
}

/**
 * Return `url` as an absolute http(s) URL, or null when it is missing, malformed
 * or uses any other scheme (javascript:, data:, …). Use before assigning href /
 * window.open targets taken from API data.
 */
export function safeHttpUrl(url) {
  if (typeof url !== 'string' || !url.trim()) return null;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null;
  } catch (err) {
    return null;
  }
}

/** Remove all children of a node. */
export function clear(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
}

/** Toggle the utility class .hidden. */
export function show(node, visible) {
  if (node) node.classList.toggle('hidden', visible === false);
}

export function hide(node) {
  show(node, false);
}

/** Grow a textarea with its content up to max px. */
export function autoResize(textarea, max) {
  if (!textarea) return;
  textarea.style.height = 'auto';
  textarea.style.height = Math.min(textarea.scrollHeight, max || 120) + 'px';
}

/**
 * Put a button in "busy" state (spinner, disabled) or restore it.
 * The original markup is kept in data-orig-html so nested calls are safe.
 */
export function setBusy(btn, busy, label) {
  if (!btn) return;
  if (busy) {
    if (btn.dataset.origHtml == null) btn.dataset.origHtml = btn.innerHTML;
    btn.innerHTML = icon('spinner') + (label ? '<span>' + escHtml(label) + '</span>' : '');
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    return;
  }
  if (btn.dataset.origHtml != null) {
    btn.innerHTML = btn.dataset.origHtml;
    delete btn.dataset.origHtml;
  }
  btn.disabled = false;
  btn.removeAttribute('aria-busy');
}

/** Clipboard write with a graceful fallback; resolves true on success. */
export async function copyToClipboard(text) {
  const value = String(text == null ? '' : text);
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (err) {
    console.warn('[utils] clipboard API failed, falling back', err);
  }
  try {
    const ta = el('textarea', { style: { position: 'fixed', opacity: '0' } });
    ta.value = value;
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    ta.remove();
    return ok;
  } catch (err) {
    console.error('[utils] copy fallback failed', err);
    return false;
  }
}

/* ── Toasts ────────────────────────────────────────────────────── */

/**
 * Show a toast in #toastContainer.
 * @param {string} msg
 * @param {'info'|'success'|'error'|'warning'} [type='info']
 * @param {number} [duration=TOAST_DURATION_MS]
 */
export function showToast(msg, type, duration) {
  const container = byId('toastContainer');
  if (!container) { console.warn('[toast]', type || 'info', msg); return null; }
  const kind = TOAST_TYPES.includes(type) ? type : 'info';
  const toast = el('div', { class: 'toast ' + kind, role: kind === 'error' ? 'alert' : 'status' },
    el('span', { class: 'toast-dot' }),
    el('span', { class: 'toast-text', text: String(msg) }));
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('fade-out');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }, duration || TOAST_DURATION_MS);
  return toast;
}

/* ── Modals ────────────────────────────────────────────────────── */

const MODAL_OPEN_EVENT = 'ts:modal-open';
const MODAL_CLOSE_EVENT = 'ts:modal-close';

function resolveNode(target) {
  return typeof target === 'string' ? byId(target) : target;
}

/**
 * Show a .modal-backdrop element (or id). Dispatches 'ts:modal-open' on it
 * with detail = extra. Returns the element.
 */
export function openModal(target, extra) {
  const node = resolveNode(target);
  if (!node) return null;
  node.classList.remove('hidden');
  node.dispatchEvent(new CustomEvent(MODAL_OPEN_EVENT, { detail: extra || {} }));
  const focusable = node.querySelector('[autofocus], input, textarea, select, button');
  if (focusable) setTimeout(() => focusable.focus(), 30);
  return node;
}

/** Hide a modal (element or id) and dispatch 'ts:modal-close' on it. */
export function closeModal(target, extra) {
  const node = resolveNode(target);
  if (!node || node.classList.contains('hidden')) return null;
  node.classList.add('hidden');
  node.dispatchEvent(new CustomEvent(MODAL_CLOSE_EVENT, { detail: extra || {} }));
  return node;
}

/** The last visible .modal-backdrop in DOM order (topmost), or null. */
export function topModal() {
  const open = qsa('.modal-backdrop:not(.hidden)');
  return open.length ? open[open.length - 1] : null;
}

export function isModalOpen(target) {
  const node = resolveNode(target);
  return !!node && !node.classList.contains('hidden');
}

/* ── Icons ─────────────────────────────────────────────────────── */

const ICON_PATHS = Object.freeze({
  send: '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
  github: '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  sun: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  refresh: '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
  'external-link': '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  unlink: '<path d="M18.84 12.25l1.72-1.71h-.02a5.004 5.004 0 0 0-.12-7.07 5.006 5.006 0 0 0-6.95 0l-1.72 1.71"/><path d="M5.17 11.75l-1.71 1.71a5.004 5.004 0 0 0 .12 7.07 5.006 5.006 0 0 0 6.95 0l1.71-1.71"/><line x1="8" y1="2" x2="8" y2="5"/><line x1="2" y1="8" x2="5" y2="8"/><line x1="16" y1="19" x2="16" y2="22"/><line x1="19" y1="16" x2="22" y2="16"/>',
  // extras (not required by the contract, handy for the feature wave)
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  'eye-off': '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>',
  alert: '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
  zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  microsoft: '<rect x="3" y="3" width="8" height="8"/><rect x="13" y="3" width="8" height="8"/><rect x="3" y="13" width="8" height="8"/><rect x="13" y="13" width="8" height="8"/>',
});

export const ICON_NAMES = Object.freeze(Object.keys(ICON_PATHS));

/**
 * SVG markup for a named icon (currentColor stroke, feather style).
 * 'spinner' returns the CSS spinner span instead of an SVG.
 * Unknown names fall back to 'alert' and log a warning.
 * @param {string} name
 * @param {number} [size=14]
 * @param {string} [extraClass]
 */
export function icon(name, size, extraClass) {
  if (name === 'spinner') {
    return '<span class="spinner' + (extraClass ? ' ' + escHtml(extraClass) : '') + '" aria-hidden="true"></span>';
  }
  let paths = ICON_PATHS[name];
  if (!paths) {
    console.warn('[utils] unknown icon "' + name + '"');
    paths = ICON_PATHS.alert;
  }
  const px = Number(size) > 0 ? Number(size) : 14;
  const cls = 'icon icon-' + escHtml(name) + (extraClass ? ' ' + escHtml(extraClass) : '');
  return '<svg class="' + cls + '" width="' + px + '" height="' + px + '" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
    'aria-hidden="true" focusable="false">' + paths + '</svg>';
}

/** icon() as a real SVG element (for el() children or replaceChildren). */
export function iconEl(name, size, extraClass) {
  const tpl = document.createElement('template');
  tpl.innerHTML = icon(name, size, extraClass);
  return tpl.content.firstChild;
}
