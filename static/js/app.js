/**
 * Entry point (ES module). Owns the app shell:
 *  - shared store + initial data load
 *  - theme, clock, user badge
 *  - generic modal behaviour (Esc, backdrop click, [data-close-modal])
 *  - settings modal shell (open/close + tab switching); pane CONTENT is
 *    rendered by settings.js / integrations.js
 *  - `actions` shared with the feature modules
 *
 * Feature modules are initialised with a frozen context object:
 *    initX({ store, api, utils, actions })
 * See docs/FRONTEND_CONTRACT.md for the full contract.
 */
import { createStore, initialState } from './store.js';
import * as api from './api.js';
import * as utils from './utils.js';
import { initHistory } from './history.js';
import { initEntries } from './entries.js';
import { initTimesheet } from './timesheet.js';
import { initSettings } from './settings.js';
import { initIntegrations } from './integrations.js';

const { byId, qsa, show, showToast, openModal, closeModal, topModal } = utils;

const THEME_STORAGE_KEY = 'ts-theme';
const CLOCK_INTERVAL_MS = 1000;
const SETTINGS_TABS = Object.freeze(['general', 'integrations', 'practices', 'prompt']);
const DEFAULT_TAB = 'general';

export const EVENTS = Object.freeze({
  OPEN_SETTINGS: 'ts:open-settings',   // document; detail {tab}
  SETTINGS_TAB: 'ts:settings-tab',     // document; detail {tab}  (after a tab is shown)
  DEVICE_LOGIN: 'ts:device-login',     // document; detail {provider, onConnected}  (handled by integrations.js)
  MODAL_OPEN: 'ts:modal-open',         // on the .modal-backdrop element
  MODAL_CLOSE: 'ts:modal-close',       // on the .modal-backdrop element
});

const store = createStore(initialState());

/* ── Theme ─────────────────────────────────────────────────────── */

function readStoredTheme() {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY);
  } catch (err) {
    console.warn('[app] localStorage unavailable', err);
    return null;
  }
}

function applyTheme(theme) {
  const next = theme === 'light' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  show(byId('iconMoon'), next === 'dark');
  show(byId('iconSun'), next === 'light');
  const select = byId('setTheme');
  if (select && select.value !== next) select.value = next;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch (err) {
    console.warn('[app] cannot persist theme', err);
  }
  store.set({ theme: next });
}

function toggleTheme() {
  applyTheme(store.get().theme === 'dark' ? 'light' : 'dark');
}

/* ── Clock & user badge ────────────────────────────────────────── */

function tickClock() {
  const node = byId('headerTime');
  if (!node) return;
  node.textContent = new Date().toLocaleTimeString('it-IT', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function renderUserBadge(config) {
  const badge = byId('userBadge');
  if (!badge) return;
  const name = (config && config.user_name) || '';
  badge.textContent = name ? name.charAt(0).toUpperCase() : '?';
  badge.title = name || 'Utente';
  badge.setAttribute('aria-label', name ? 'Utente: ' + name : 'Utente');
}

/* ── Data actions (shared with feature modules) ────────────────── */

function normalizeElaboration(data) {
  if (!data || !data.result) return null;
  return {
    result: data.result,
    elaborated_at: data.elaborated_at || null,
    elaborated_edited_at: data.elaborated_edited_at || null,
  };
}

/** Load entries + elaboration for `date` (default: the selected day). */
async function loadDay(date) {
  const day = date || utils.currentDate(store.get());
  const [entriesData, elabData] = await Promise.all([
    api.getEntries(day),
    api.getElaboration(day).catch((err) => {
      console.warn('[app] elaboration unavailable for', day, err);
      return null;
    }),
  ]);
  // Drop stale responses if the user switched day meanwhile.
  if (utils.currentDate(store.get()) !== day) return store.get();
  return store.set({
    entries: Array.isArray(entriesData.entries) ? entriesData.entries : [],
    elaboration: normalizeElaboration(elabData),
  });
}

async function refreshDays() {
  const data = await api.getDays();
  return store.set({ days: Array.isArray(data.days) ? data.days : [] });
}

async function refreshConfig() {
  const config = await api.getConfig();
  renderUserBadge(config);
  return store.set({ config });
}

async function refreshPractices() {
  const data = await api.getPractices();
  return store.set({ practices: Array.isArray(data.practices) ? data.practices : [] });
}

async function refreshSettings() {
  const settings = await api.getSettings();
  return store.set({ settings });
}

/** Refresh both provider statuses; a failing provider becomes null (logged). */
async function refreshIntegrations() {
  const [githubStatus, microsoftStatus] = await Promise.all([
    api.github.status().catch((err) => { console.warn('[app] github status failed', err); return null; }),
    api.microsoft.status().catch((err) => { console.warn('[app] microsoft status failed', err); return null; }),
  ]);
  return store.set({ githubStatus, microsoftStatus });
}

function selectDay(date) {
  if (!utils.isISODay(date)) {
    console.warn('[app] selectDay: invalid date', date);
    return store.get();
  }
  if (store.get().selectedDate === date) return store.get();
  // Clear the previous day's data in the same update so nothing stale is shown
  // (or edited and persisted under the new date) while loadDay is in flight.
  return store.set({ selectedDate: date, entries: [], elaboration: null });
}

/* ── Settings modal shell ──────────────────────────────────────── */

function showTab(tab) {
  const name = SETTINGS_TABS.includes(tab) ? tab : DEFAULT_TAB;
  qsa('#settingsTabs .tab-btn').forEach((btn) => {
    const active = btn.dataset.tab === name;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', String(active));
    btn.tabIndex = active ? 0 : -1;
  });
  qsa('#settingsModal .tab-pane').forEach((pane) => show(pane, pane.dataset.pane === name));
  document.dispatchEvent(new CustomEvent(EVENTS.SETTINGS_TAB, { detail: { tab: name } }));
  return name;
}

function openSettings(tab) {
  const name = showTab(tab || DEFAULT_TAB);
  return openModal('settingsModal', { tab: name });
}

function closeSettings() {
  return closeModal('settingsModal');
}

function onTabKeydown(event) {
  const keys = { ArrowRight: 1, ArrowLeft: -1, Home: 0, End: 0 };
  if (!(event.key in keys)) return;
  event.preventDefault();
  const current = SETTINGS_TABS.indexOf(showTabFromButton(event.target));
  const next = event.key === 'Home' ? 0
    : event.key === 'End' ? SETTINGS_TABS.length - 1
      : (current + keys[event.key] + SETTINGS_TABS.length) % SETTINGS_TABS.length;
  showTab(SETTINGS_TABS[next]);
  const btn = qsa('#settingsTabs .tab-btn')[next];
  if (btn) btn.focus();
}

function showTabFromButton(target) {
  const btn = target && target.closest ? target.closest('.tab-btn') : null;
  return btn ? btn.dataset.tab : DEFAULT_TAB;
}

/* ── Global bindings ───────────────────────────────────────────── */

function bindModals() {
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const modal = topModal();
    if (modal) { event.preventDefault(); closeModal(modal); }
  });
  qsa('.modal-backdrop').forEach((backdrop) => {
    backdrop.addEventListener('click', (event) => {
      if (event.target === event.currentTarget) closeModal(backdrop);
    });
  });
  document.addEventListener('click', (event) => {
    const closer = event.target.closest ? event.target.closest('[data-close-modal]') : null;
    if (!closer) return;
    const modal = closer.closest('.modal-backdrop');
    if (modal) closeModal(modal);
  });
}

function bindShell() {
  const themeToggle = byId('themeToggle');
  if (themeToggle) themeToggle.addEventListener('click', toggleTheme);
  const themeSelect = byId('setTheme');
  if (themeSelect) themeSelect.addEventListener('change', () => applyTheme(themeSelect.value));

  const settingsBtn = byId('settingsBtn');
  if (settingsBtn) settingsBtn.addEventListener('click', () => openSettings(DEFAULT_TAB));

  const tabs = byId('settingsTabs');
  if (tabs) {
    tabs.addEventListener('click', (event) => {
      const btn = event.target.closest('.tab-btn');
      if (btn) showTab(btn.dataset.tab);
    });
    tabs.addEventListener('keydown', onTabKeydown);
  }

  document.addEventListener(EVENTS.OPEN_SETTINGS, (event) => {
    openSettings(event.detail && event.detail.tab);
  });
}

/* ── Store reactions owned by the shell ────────────────────────── */

function bindStoreReactions() {
  store.subscribe((state, prev, changed) => {
    if (changed.includes('config')) renderUserBadge(state.config);
    if (changed.includes('selectedDate') && state.selectedDate) {
      loadDay(state.selectedDate).catch((err) => {
        console.error('[app] loadDay failed', err);
        showToast(api.errorMessage(err, 'Errore nel caricamento del giorno selezionato.'), 'error');
      });
    }
  });
}

/* ── Boot ──────────────────────────────────────────────────────── */

const actions = Object.freeze({
  loadDay,
  refreshDays,
  refreshConfig,
  refreshPractices,
  refreshSettings,
  refreshIntegrations,
  selectDay,
  openSettings,
  closeSettings,
  showTab,
  applyTheme,
});

function initModules(ctx) {
  const inits = [
    ['history', initHistory], ['entries', initEntries], ['timesheet', initTimesheet],
    ['settings', initSettings], ['integrations', initIntegrations],
  ];
  inits.forEach(([name, init]) => {
    try {
      if (typeof init === 'function') init(ctx);
      else console.warn('[app] module "' + name + '" has no init function');
    } catch (err) {
      console.error('[app] init of module "' + name + '" failed', err);
    }
  });
}

async function loadInitialData() {
  const safe = (promise, label, fallback) => promise.catch((err) => {
    console.warn('[app] initial load: ' + label + ' failed', err);
    return fallback;
  });
  const [config, daysData, practicesData, githubStatus, microsoftStatus] = await Promise.all([
    api.getConfig(),
    safe(api.getDays(), 'days', { days: [] }),
    safe(api.getPractices(), 'practices', { practices: [] }),
    safe(api.github.status(), 'github status', null),
    safe(api.microsoft.status(), 'microsoft status', null),
  ]);
  renderUserBadge(config);
  store.set({
    config,
    days: Array.isArray(daysData.days) ? daysData.days : [],
    practices: Array.isArray(practicesData.practices) ? practicesData.practices : [],
    githubStatus,
    microsoftStatus,
  });
  // Setting the day triggers loadDay() through the store subscription.
  selectDay(utils.serverToday(store.get()));
}

function boot() {
  applyTheme(readStoredTheme() || 'dark');
  tickClock();
  setInterval(tickClock, CLOCK_INTERVAL_MS);

  bindModals();
  bindShell();
  bindStoreReactions();

  const ctx = Object.freeze({ store, api, utils, actions });
  initModules(ctx);

  loadInitialData().catch((err) => {
    console.error('[app] initial load failed', err);
    showToast(api.errorMessage(err, 'Errore durante il caricamento iniziale.'), 'error');
    // Still pick a day so the UI is usable offline-ish.
    selectDay(utils.todayISO());
  });

  // Debug handle (local single-user tool).
  window.__ts = { store, actions, api, utils };
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}

export { store, actions };
