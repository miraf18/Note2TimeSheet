/**
 * Left panel "Attività": entry list, composer, inline edit / delete and the
 * GitHub / Microsoft import buttons.
 *
 * Contract: docs/FRONTEND_CONTRACT.md §7.2. Owns #entryList, #entryEmptyState,
 * #entryCount, #composerTextarea, #sendBtn, #githubImportBtn (+#githubDot),
 * #meetingsImportBtn (+#meetingsDot). Reacts to store keys entries /
 * selectedDate / githubStatus / microsoftStatus / config.
 *
 * After every mutation the store's `entries` is replaced immutably and
 * actions.refreshDays() is called so the day selector counts stay in sync.
 */
import * as api from './api.js';
import * as utils from './utils.js';
import { buildEntryCard } from './entries-card.js';

const { byId, qsa, show, setBusy, showToast, pluralize, currentDate, autoResize } = utils;

const LIST_KEYS = Object.freeze(['entries', 'selectedDate']);
const STATUS_KEYS = Object.freeze(['githubStatus', 'microsoftStatus', 'config']);
const DELETE_CONFIRM_MS = 2500;
const COMPOSER_MAX_HEIGHT = 120;
const EVENT_OPEN_SETTINGS = 'ts:open-settings';
const EVENT_DEVICE_LOGIN = 'ts:device-login';

const PROVIDERS = Object.freeze({
  github: Object.freeze({
    buttonId: 'githubImportBtn',
    dotId: 'githubDot',
    hint: 'Collega prima GitHub dalle impostazioni',
    fallback: 'Impossibile importare da GitHub.',
    isConnected: (state) => !!(state.githubStatus && state.githubStatus.connected),
  }),
  microsoft: Object.freeze({
    buttonId: 'meetingsImportBtn',
    dotId: 'meetingsDot',
    hint: 'Collega prima l\'account Microsoft dalle impostazioni',
    fallback: 'Impossibile importare le riunioni.',
    isConnected: (state) => {
      const status = state.microsoftStatus;
      return !!(status && (status.connected || status.provider === 'outlook_com'));
    },
  }),
});

let store = null;
let actions = null;
/** Pending two-click delete: {id, button, timer} or null (replaced, never mutated). */
let pendingDelete = null;

/* ── Helpers ───────────────────────────────────────────────────── */

function refreshDaysSafe() {
  return actions.refreshDays().catch((err) => console.warn('[entries] refreshDays failed', err));
}

/** Replace store.entries only if the shown day is still `date`. */
function setEntriesFor(date, entries) {
  if (currentDate(store.get()) !== date) return false;
  store.set({ entries: Array.isArray(entries) ? entries : [] });
  return true;
}

function importToast(count, skipped) {
  const created = count === 1 ? '1 nuova voce' : count + ' nuove voci';
  const dupes = skipped + ' già ' + (skipped === 1 ? 'importata' : 'importate');
  return created + ', ' + dupes;
}

/* ── Delete (two clicks within 2.5 s) ──────────────────────────── */

function clearPendingDelete() {
  if (!pendingDelete) return;
  clearTimeout(pendingDelete.timer);
  pendingDelete.button.classList.remove('danger');
  pendingDelete.button.title = 'Elimina';
  pendingDelete = null;
}

async function deleteEntry(entry) {
  const date = currentDate(store.get());
  try {
    await api.deleteEntry(entry.id, date);
    setEntriesFor(date, store.get().entries.filter((e) => e.id !== entry.id));
    refreshDaysSafe();
  } catch (err) {
    console.error('[entries] delete failed', err);
    showToast(api.errorMessage(err, 'Impossibile eliminare l\'attività.'), 'error');
  }
}

function onDeleteClick(entry, button) {
  if (pendingDelete && pendingDelete.id === entry.id) {
    clearPendingDelete();
    deleteEntry(entry);
    return;
  }
  clearPendingDelete();
  button.classList.add('danger');
  button.title = 'Clicca ancora per confermare';
  const timer = setTimeout(() => {
    if (pendingDelete && pendingDelete.id === entry.id) clearPendingDelete();
  }, DELETE_CONFIRM_MS);
  pendingDelete = Object.freeze({ id: entry.id, button, timer });
}

/* ── Edit ──────────────────────────────────────────────────────── */

async function onEditCommit(entry, text) {
  const date = currentDate(store.get());
  try {
    const data = await api.updateEntry(entry.id, text, date);
    const updated = data && data.entry ? data.entry : Object.assign({}, entry, { text });
    setEntriesFor(date, store.get().entries.map((e) => (e.id === entry.id ? updated : e)));
    refreshDaysSafe();
  } catch (err) {
    console.error('[entries] update failed', err);
    showToast(api.errorMessage(err, 'Impossibile modificare l\'attività.'), 'error');
    throw err;
  }
}

const cardHandlers = Object.freeze({ onEdit: onEditCommit, onDelete: onDeleteClick });

/* ── List ──────────────────────────────────────────────────────── */

function renderList(state) {
  const entries = Array.isArray(state.entries) ? state.entries : [];
  const count = byId('entryCount');
  if (count) count.textContent = pluralize(entries.length, 'voce', 'voci');
  const list = byId('entryList');
  if (!list) return;
  clearPendingDelete();
  qsa('.entry-card', list).forEach((node) => node.remove());
  show(byId('entryEmptyState'), entries.length === 0);
  const fragment = document.createDocumentFragment();
  entries.forEach((entry) => fragment.appendChild(buildEntryCard(entry, cardHandlers)));
  list.appendChild(fragment);
}

/* ── Composer ──────────────────────────────────────────────────── */

async function sendEntry() {
  const textarea = byId('composerTextarea');
  const button = byId('sendBtn');
  const text = textarea ? textarea.value.trim() : '';
  if (!text || (button && button.disabled)) return;
  const date = currentDate(store.get());
  setBusy(button, true);
  try {
    const data = await api.addEntry(text, date);
    if (data && data.entry) setEntriesFor(date, store.get().entries.concat([data.entry]));
    textarea.value = '';
    autoResize(textarea, COMPOSER_MAX_HEIGHT);
    const list = byId('entryList');
    if (list) list.scrollTop = list.scrollHeight;
    refreshDaysSafe();
  } catch (err) {
    console.error('[entries] add failed', err);
    showToast(api.errorMessage(err, 'Impossibile aggiungere l\'attività.'), 'error');
  } finally {
    setBusy(button, false);
    if (textarea) textarea.focus();
  }
}

function bindComposer() {
  const textarea = byId('composerTextarea');
  const button = byId('sendBtn');
  if (textarea) {
    textarea.addEventListener('input', () => autoResize(textarea, COMPOSER_MAX_HEIGHT));
    textarea.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendEntry(); }
    });
  }
  if (button) button.addEventListener('click', sendEntry);
}

/* ── Imports (GitHub / Microsoft) ──────────────────────────────── */

function renderImportButtons(state) {
  Object.values(PROVIDERS).forEach((provider) => {
    const connected = provider.isConnected(state);
    const button = byId(provider.buttonId);
    const dot = byId(provider.dotId);
    if (button) button.classList.toggle('is-off', !connected);
    if (dot) {
      dot.classList.toggle('on', connected);
      dot.classList.toggle('off', !connected);
    }
  });
}

function openIntegrations(provider) {
  document.dispatchEvent(new CustomEvent(EVENT_OPEN_SETTINGS, { detail: { tab: 'integrations' } }));
  showToast(provider.hint, 'warning');
}

function requestDeviceLogin(name) {
  document.dispatchEvent(new CustomEvent(EVENT_DEVICE_LOGIN, {
    detail: { provider: name, onConnected: () => runImport(name, { skipCheck: true }) },
  }));
}

function applyImportResult(date, data) {
  const created = Number(data && data.new) || 0;
  const skipped = Number(data && data.skipped) || 0;
  showToast(importToast(created, skipped), created > 0 ? 'success' : 'info');
  if (Array.isArray(data && data.entries)) setEntriesFor(date, data.entries);
  else actions.loadDay(date).catch((err) => console.warn('[entries] reload after import failed', err));
  refreshDaysSafe();
}

/**
 * Import the shown day from `name` ('github' | 'microsoft').
 * Not connected → open the Integrazioni tab with a hint. 401 → device login.
 */
async function runImport(name, options) {
  const provider = PROVIDERS[name];
  const state = store.get();
  if (!provider) return;
  if (!(options && options.skipCheck) && !provider.isConnected(state)) { openIntegrations(provider); return; }
  const button = byId(provider.buttonId);
  if (button && button.disabled) return;
  const date = currentDate(state);
  setBusy(button, true);
  try {
    applyImportResult(date, await api.providers[name].importDay(date));
  } catch (err) {
    if (api.isAuthRequired(err)) { requestDeviceLogin(name); return; }
    console.error('[entries] import ' + name + ' failed', err);
    showToast(api.errorMessage(err, provider.fallback), 'error');
  } finally {
    setBusy(button, false);
    renderImportButtons(store.get());
  }
}

function bindImportButtons() {
  Object.keys(PROVIDERS).forEach((name) => {
    const button = byId(PROVIDERS[name].buttonId);
    if (button) button.addEventListener('click', () => runImport(name));
  });
}

/* ── Init ──────────────────────────────────────────────────────── */

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 */
export function initEntries(ctx) {
  store = ctx.store;
  actions = ctx.actions;
  bindComposer();
  bindImportButtons();
  store.subscribe((state, prev, changed) => {
    if (changed.some((key) => LIST_KEYS.includes(key))) renderList(state);
    if (changed.some((key) => STATUS_KEYS.includes(key))) renderImportButtons(state);
  });
  const state = store.get();
  renderList(state);
  renderImportButtons(state);
}
