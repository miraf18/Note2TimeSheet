/**
 * Timesheet table rows + inline cell editing (docs/FRONTEND_CONTRACT.md §7.3).
 *
 * Pure DOM: no store or API access. timesheet.js supplies the handlers:
 *   handlers.onEdit(index, field, value)  field ∈ 'ore' | 'descrizione'
 *   handlers.onEditEnd()                  called when an inline input closes
 *
 * Row markup:
 * <tr data-index="i"><td><div class="practice-badge"><span class="practice-code [unknown]">…</span>
 * <span class="practice-name-label">…</span></div></td><td class="editable">ore</td>
 * <td class="desc-td"><div class="desc-cell"><span class="desc-text">…</span>
 * <button class="desc-edit-btn">✎</button></div></td></tr>
 */
import * as utils from './utils.js';

const { el, iconEl, clear, fmtOre, copyToClipboard, showToast } = utils;

const COPIED_FEEDBACK_MS = 1200;
const HOURS_STEP = '0.25';
const HOURS_MIN = '0.25';

/** Practice lookup by code (string comparison). */
export function findPractice(practices, code) {
  const wanted = String(code == null ? '' : code);
  return (Array.isArray(practices) ? practices : []).find((p) => p && String(p.code) === wanted) || null;
}

/** Hours parser for the inline input: positive finite number rounded to 2 decimals, else null. */
export function parseHours(raw) {
  const value = parseFloat(String(raw == null ? '' : raw).replace(',', '.'));
  if (!Number.isFinite(value) || value <= 0) return null;
  return Math.round(value * 100) / 100;
}

/* ── Generic inline editor ─────────────────────────────────────── */

/**
 * Swap `host` content for `input`; Enter/blur commit, Esc cancels.
 * `parse(raw)` returns the new value or null (invalid / unchanged → restore).
 */
function runInlineEdit(host, input, parse, restore, onCommit, onEnd) {
  clear(host);
  host.appendChild(input);
  input.focus();
  input.select();
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    const value = commit && input.isConnected ? parse(input.value) : null;
    if (value === null) restore();
    else onCommit(value);
    if (input.isConnected) restore(value === null ? undefined : value);
    onEnd();
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); finish(true); }
    else if (event.key === 'Escape') { event.preventDefault(); finish(false); }
  });
  input.addEventListener('blur', () => finish(true));
}

/** Inline edit of the "Ore" cell. */
export function startHoursEdit(td, row, index, handlers) {
  if (td.querySelector('input')) return;
  const current = parseHours(row.ore);
  const input = el('input', {
    class: 'inline-input', type: 'number', step: HOURS_STEP, min: HOURS_MIN, 'aria-label': 'Ore',
  });
  input.value = current === null ? '' : String(current);
  const parse = (raw) => {
    const value = parseHours(raw);
    return value === null || value === current ? null : value;
  };
  const restore = (value) => { td.textContent = fmtOre(value === undefined ? row.ore : value); };
  runInlineEdit(td, input, parse, restore, (value) => handlers.onEdit(index, 'ore', value), handlers.onEditEnd);
}

/** Inline edit of the description (inside .desc-text, keeping the .desc-cell layout). */
export function startDescEdit(descText, row, index, handlers) {
  if (descText.querySelector('input')) return;
  const current = String(row.descrizione || '');
  const input = el('input', { class: 'inline-input', type: 'text', maxlength: 2000, 'aria-label': 'Descrizione' });
  input.value = current;
  const parse = (raw) => {
    const value = String(raw).trim();
    return value === current ? null : value;
  };
  const restore = (value) => { descText.textContent = value === undefined ? current : value; };
  runInlineEdit(descText, input, parse, restore,
    (value) => handlers.onEdit(index, 'descrizione', value), handlers.onEditEnd);
}

/* ── Cells ─────────────────────────────────────────────────────── */

function practiceCell(row, practices) {
  const practice = findPractice(practices, row.pratica);
  const code = el('span', {
    class: 'practice-code' + (practice ? '' : ' unknown'),
    text: String(row.pratica == null ? '' : row.pratica),
    title: practice ? (practice.description || practice.name || '') : 'Codice non presente nell\'elenco pratiche',
  });
  const name = el('span', { class: 'practice-name-label', text: practice ? practice.name || '' : '' });
  return el('td', null, el('div', { class: 'practice-badge' }, code, name));
}

function hoursCell(row, index, handlers) {
  const td = el('td', { class: 'editable', title: 'Clicca per modificare le ore', text: fmtOre(row.ore) });
  td.addEventListener('click', () => startHoursEdit(td, row, index, handlers));
  return td;
}

function copyDescription(descText, text) {
  if (descText.querySelector('input')) return;
  copyToClipboard(text).then((ok) => {
    if (!ok) { showToast('Impossibile copiare.', 'error'); return; }
    descText.classList.add('copied');
    setTimeout(() => descText.classList.remove('copied'), COPIED_FEEDBACK_MS);
  });
}

function descCell(row, index, handlers) {
  const text = String(row.descrizione || '');
  const descText = el('span', { class: 'desc-text', text, title: 'Clicca per copiare' });
  descText.addEventListener('click', () => copyDescription(descText, text));
  const editBtn = el('button', {
    class: 'desc-edit-btn', type: 'button', title: 'Modifica descrizione', 'aria-label': 'Modifica descrizione',
    onClick: (event) => { event.stopPropagation(); startDescEdit(descText, row, index, handlers); },
  }, iconEl('edit', 12));
  return el('td', { class: 'desc-td' }, el('div', { class: 'desc-cell' }, descText, editBtn));
}

/* ── Rows ──────────────────────────────────────────────────────── */

/**
 * Rebuild the table body.
 * @param {HTMLElement} tbody
 * @param {Array} timesheet     result.timesheet rows
 * @param {Array} practices     store.practices
 * @param {{onEdit: Function, onEditEnd: Function}} handlers
 * @param {number[]} [savingRows]  indexes that get the .saving class
 */
export function renderRows(tbody, timesheet, practices, handlers, savingRows) {
  if (!tbody) return;
  const saving = Array.isArray(savingRows) ? savingRows : [];
  clear(tbody);
  const fragment = document.createDocumentFragment();
  (Array.isArray(timesheet) ? timesheet : []).forEach((row, index) => {
    const tr = el('tr', { class: saving.includes(index) ? 'saving' : null, dataset: { index: String(index) } },
      practiceCell(row || {}, practices),
      hoursCell(row || {}, index, handlers),
      descCell(row || {}, index, handlers));
    fragment.appendChild(tr);
  });
  tbody.appendChild(fragment);
}

/** Toggle .saving on the rows whose index is listed (no rebuild). */
export function markSavingRows(tbody, savingRows) {
  if (!tbody) return;
  const saving = Array.isArray(savingRows) ? savingRows : [];
  Array.from(tbody.querySelectorAll('tr[data-index]')).forEach((tr) => {
    tr.classList.toggle('saving', saving.includes(Number(tr.dataset.index)));
  });
}
