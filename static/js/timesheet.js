/**
 * Right panel "Timesheet": empty state / result toggling, AI elaboration,
 * table with inline edits persisted through PUT /api/elaborate (debounced),
 * total vs daily hours, note and "Copia JSON".
 *
 * Contract: docs/FRONTEND_CONTRACT.md §7.3. Owns #elabMeta, #elabEmpty,
 * #elaborateBtn, #elabResult, #elabUpdatedAt, #copyJsonBtn, #reElaborateBtn,
 * #tsTable/#tsBody/#tsTotal, #elabNote. Reacts to store keys elaboration /
 * practices / config / selectedDate.
 */
import * as api from './api.js';
import * as utils from './utils.js';
import { renderRows, markSavingRows } from './timesheet-table.js';

const {
  byId, el, clear, show, setBusy, showToast, fmtTime, fmtOre, pluralize,
  currentDate, debounce, copyToClipboard, icon,
} = utils;

const WATCHED_KEYS = Object.freeze(['elaboration', 'practices', 'config', 'selectedDate']);
const SAVE_DEBOUNCE_MS = 600;
const COPY_FEEDBACK_MS = 2200;
const DEFAULT_DAILY_HOURS = 8;
const TOTAL_EPSILON = 0.001;

let store = null;
let actions = null;
/** Edits waiting to be persisted: {date, result, rows, seq} or null (replaced, never mutated). */
let pending = null;
/** Incremented on every edit / new elaboration; stale save responses are ignored. */
let saveSeq = 0;
let renderDeferred = false;
let copyTimer = null;
let copyBtnMarkup = null;

/* ── Helpers ───────────────────────────────────────────────────── */

function hasResult(elaboration) {
  return !!(elaboration && elaboration.result && Array.isArray(elaboration.result.timesheet));
}

function sumHours(timesheet) {
  const total = timesheet.reduce((acc, row) => acc + (parseFloat(row && row.ore) || 0), 0);
  return Math.round(total * 100) / 100;
}

function refreshDaysSafe() {
  return actions.refreshDays().catch((err) => console.warn('[timesheet] refreshDays failed', err));
}

function isEditingCell() {
  const tbody = byId('tsBody');
  const active = document.activeElement;
  return !!(tbody && active && active.classList && active.classList.contains('inline-input') && tbody.contains(active));
}

/* ── Rendering ─────────────────────────────────────────────────── */

function renderUpdatedAt(elaboration) {
  const node = byId('elabUpdatedAt');
  if (!node) return;
  clear(node);
  if (!elaboration.elaborated_at) return;
  node.appendChild(document.createTextNode('Ultimo aggiornamento ' + fmtTime(elaboration.elaborated_at)));
  if (elaboration.elaborated_edited_at) {
    node.appendChild(el('span', { class: 'edited', text: ' · modificato ' + fmtTime(elaboration.elaborated_edited_at) }));
  }
}

function renderTotal(timesheet, config) {
  const node = byId('tsTotal');
  if (!node) return;
  const total = sumHours(timesheet);
  const target = Number(config && config.daily_hours) || DEFAULT_DAILY_HOURS;
  const ok = Math.abs(total - target) < TOTAL_EPSILON;
  node.textContent = fmtOre(total);
  node.classList.toggle('total-ok', ok);
  node.classList.toggle('total-ko', !ok);
  node.title = 'Obiettivo giornaliero: ' + fmtOre(target) + ' h';
}

function renderNote(result) {
  const node = byId('elabNote');
  if (!node) return;
  const note = typeof result.note === 'string' ? result.note.trim() : '';
  node.textContent = note;
  show(node, note.length > 0);
}

function render(state) {
  const elaboration = state.elaboration;
  const has = hasResult(elaboration);
  const meta = byId('elabMeta');
  show(byId('elabEmpty'), !has);
  show(byId('elabResult'), has);
  if (!has) {
    if (meta) meta.textContent = '';
    if (!isEditingCell()) clear(byId('tsBody'));
    return;
  }
  const timesheet = elaboration.result.timesheet;
  if (meta) meta.textContent = pluralize(timesheet.length, 'riga', 'righe');
  renderUpdatedAt(elaboration);
  renderTotal(timesheet, state.config);
  renderNote(elaboration.result);
  // Never destroy a cell the user is typing in; re-render when the edit closes.
  if (isEditingCell()) { renderDeferred = true; return; }
  renderDeferred = false;
  renderRows(byId('tsBody'), timesheet, state.practices, tableHandlers, pending ? pending.rows : []);
}

/* ── Inline edits → store + debounced PUT ──────────────────────── */

function toPayload(result) {
  return {
    timesheet: result.timesheet.map((row) => ({
      pratica: String(row.pratica == null ? '' : row.pratica),
      ore: Number(row.ore),
      descrizione: String(row.descrizione || ''),
    })),
    note: typeof result.note === 'string' ? result.note : '',
  };
}

function applySaveResponse(job, data) {
  if (job.seq !== saveSeq) return; // newer edits (or a new elaboration) supersede this response
  const state = store.get();
  if (currentDate(state) !== job.date || !hasResult(state.elaboration)) return;
  const result = data && data.result && Array.isArray(data.result.timesheet) ? data.result : state.elaboration.result;
  const editedAt = (data && data.elaborated_edited_at) || state.elaboration.elaborated_edited_at || null;
  store.set({ elaboration: Object.assign({}, state.elaboration, { result, elaborated_edited_at: editedAt }) });
  refreshDaysSafe();
}

async function persistPending() {
  const job = pending;
  if (!job) return;
  pending = null;
  try {
    applySaveResponse(job, await api.saveElaboration(job.date, toPayload(job.result)));
  } catch (err) {
    console.error('[timesheet] save failed', err);
    showToast(api.errorMessage(err, 'Impossibile salvare le modifiche al timesheet.'), 'error');
  } finally {
    markSavingRows(byId('tsBody'), pending ? pending.rows : []);
  }
}

const saveDebounced = debounce(persistPending, SAVE_DEBOUNCE_MS);

function queueSave(date, result, index) {
  saveSeq += 1;
  const rows = pending && pending.date === date ? pending.rows : [];
  pending = Object.freeze({
    date, result, seq: saveSeq, rows: rows.includes(index) ? rows : rows.concat([index]),
  });
  markSavingRows(byId('tsBody'), pending.rows);
  saveDebounced();
}

function onCellEdit(index, field, value) {
  const state = store.get();
  const elaboration = state.elaboration;
  if (!hasResult(elaboration) || !elaboration.result.timesheet[index]) return;
  const timesheet = elaboration.result.timesheet.map((row, i) => (
    i === index ? Object.assign({}, row, { [field]: value }) : row));
  const result = Object.assign({}, elaboration.result, { timesheet, totale_ore: sumHours(timesheet) });
  store.set({ elaboration: Object.assign({}, elaboration, { result }) });
  queueSave(currentDate(state), result, index);
}

const tableHandlers = Object.freeze({
  onEdit: onCellEdit,
  onEditEnd: () => { if (renderDeferred) render(store.get()); },
});

/* ── Elaborate ─────────────────────────────────────────────────── */

async function elaborate(button) {
  if (button && button.disabled) return;
  const date = currentDate(store.get());
  const others = [byId('elaborateBtn'), byId('reElaborateBtn')].filter((b) => b && b !== button);
  setBusy(button, true, 'Elaborazione in corso...');
  others.forEach((b) => { b.disabled = true; });
  try {
    saveDebounced.cancel();
    await persistPending(); // never throws; flush manual edits before regenerating
    const data = await api.elaborate(date);
    saveSeq += 1;
    pending = null;
    if (currentDate(store.get()) === date) {
      store.set({ elaboration: { result: data.result, elaborated_at: data.elaborated_at || null, elaborated_edited_at: null } });
    }
    refreshDaysSafe();
    showToast('Timesheet generato.', 'success');
  } catch (err) {
    console.error('[timesheet] elaborate failed', err);
    showToast(api.errorMessage(err, 'Errore durante l\'elaborazione.'), 'error');
  } finally {
    setBusy(button, false);
    others.forEach((b) => { b.disabled = false; });
  }
}

/* ── Copia JSON ────────────────────────────────────────────────── */

function buildJsonPayload(state) {
  const timesheet = state.elaboration.result.timesheet;
  return {
    data: currentDate(state),
    utente: (state.config && state.config.user_name) || '',
    timesheet: timesheet.map((row) => ({
      pratica: row.pratica, ore: Number(row.ore) || 0, descrizione: row.descrizione || '',
    })),
    totale_ore: sumHours(timesheet),
  };
}

function showCopiedFeedback(button) {
  if (!button) return;
  if (copyBtnMarkup === null) copyBtnMarkup = button.innerHTML;
  button.innerHTML = icon('check', 13) + ' Copiato!'; // trusted static markup
  button.classList.add('btn-success');
  if (copyTimer) clearTimeout(copyTimer);
  copyTimer = setTimeout(() => {
    button.innerHTML = copyBtnMarkup;
    button.classList.remove('btn-success');
    copyTimer = null;
  }, COPY_FEEDBACK_MS);
}

async function copyJson() {
  const state = store.get();
  if (!hasResult(state.elaboration)) return;
  const ok = await copyToClipboard(JSON.stringify(buildJsonPayload(state), null, 2));
  if (!ok) { showToast('Impossibile copiare negli appunti.', 'error'); return; }
  showCopiedFeedback(byId('copyJsonBtn'));
}

/* ── Init ──────────────────────────────────────────────────────── */

function bindButtons() {
  const elaborateBtn = byId('elaborateBtn');
  const reElaborateBtn = byId('reElaborateBtn');
  const copyBtn = byId('copyJsonBtn');
  if (elaborateBtn) elaborateBtn.addEventListener('click', () => elaborate(elaborateBtn));
  if (reElaborateBtn) reElaborateBtn.addEventListener('click', () => elaborate(reElaborateBtn));
  if (copyBtn) {
    copyBtnMarkup = copyBtn.innerHTML;
    copyBtn.addEventListener('click', copyJson);
  }
}

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 */
export function initTimesheet(ctx) {
  store = ctx.store;
  actions = ctx.actions;
  bindButtons();
  store.subscribe((state, prev, changed) => {
    // Persist pending edits of the previous day before its elaboration is replaced.
    if (changed.includes('selectedDate')) saveDebounced.flush();
    if (changed.some((key) => WATCHED_KEYS.includes(key))) render(state);
  });
  render(store.get());
}
