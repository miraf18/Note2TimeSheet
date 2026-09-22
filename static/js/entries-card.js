/**
 * Entry card builder for the left panel (docs/FRONTEND_CONTRACT.md §7.2).
 *
 * Pure DOM construction: no store access, no API calls. entries.js passes the
 * handlers that perform the mutations:
 *   handlers.onEdit(entry, newText)  → Promise (resolves when persisted)
 *   handlers.onDelete(entry, button) → void   (two-click confirmation)
 *
 * Everything coming from the server (text, repo names, commit messages, URLs)
 * is inserted with textContent / attributes — never innerHTML.
 */
import * as utils from './utils.js';

const { el, iconEl, fmtTime, fmtDuration, autoResize, safeHttpUrl } = utils;

export const MAX_COMMITS_SHOWN = 30;
const EDIT_TEXTAREA_MAX_HEIGHT = 160;
const TYPE_CLASS = Object.freeze({ manual: 'entry-manual', meeting: 'entry-meeting', github: 'entry-github' });

/** Normalise the entry type (legacy "outlook" → meeting, unknown → manual). */
export function entryKind(entry) {
  const type = entry && entry.type;
  if (type === 'meeting' || type === 'outlook') return 'meeting';
  if (type === 'github') return 'github';
  return 'manual';
}

/** Only http(s) URLs are rendered as links (never javascript:/data:). */
export function safeUrl(url) {
  return safeHttpUrl(url);
}

function firstLine(text) {
  return String(text == null ? '' : text).split('\n')[0].trim();
}

/* ── Top row pieces ────────────────────────────────────────────── */

function meetingTime(entry) {
  const meta = entry.meta || {};
  const duration = fmtDuration(entry.duration_min);
  if (meta.start && meta.end) {
    const range = fmtTime(meta.start) + '–' + fmtTime(meta.end);
    return el('span', { class: 'entry-meeting-time', text: duration ? range + ' · ' + duration : range });
  }
  return duration ? el('span', { class: 'entry-duration', text: duration }) : null;
}

function repoLink(entry) {
  const meta = entry.meta || {};
  if (!meta.repo) return null;
  const href = safeUrl(meta.url);
  if (!href) return el('span', { class: 'entry-repo', text: String(meta.repo) });
  return el('a', {
    class: 'entry-repo', href, target: '_blank', rel: 'noopener noreferrer',
    title: 'Apri ' + meta.repo + ' su GitHub',
  }, String(meta.repo) + ' ↗');
}

function buildTopRow(entry, kind) {
  const badge = kind === 'meeting'
    ? el('span', { class: 'badge badge-meeting', text: 'Riunione' })
    : kind === 'github'
      ? el('span', { class: 'badge badge-github', text: 'GitHub' })
      : el('span', { class: 'badge badge-activity', text: 'Attività' });
  const extra = kind === 'meeting' ? meetingTime(entry)
    : kind === 'github' ? repoLink(entry)
      : (fmtDuration(entry.duration_min) ? el('span', { class: 'entry-duration', text: fmtDuration(entry.duration_min) }) : null);
  return el('div', { class: 'entry-card-top' },
    badge,
    extra,
    el('span', { class: 'entry-time', text: fmtTime(entry.time) }));
}

/* ── Body pieces ───────────────────────────────────────────────── */

function commitItem(commit) {
  const message = firstLine(commit && commit.message) || (commit && commit.sha ? String(commit.sha).slice(0, 7) : '');
  const href = safeUrl(commit && commit.url);
  return el('li', null, href
    ? el('a', { href, target: '_blank', rel: 'noopener noreferrer' }, message)
    : message);
}

function commitList(entry) {
  const meta = entry.meta || {};
  if (meta.kind !== 'commits' || !Array.isArray(meta.commits) || meta.commits.length === 0) return null;
  const shown = meta.commits.slice(0, MAX_COMMITS_SHOWN);
  const rest = meta.commits.length - shown.length;
  return el('ul', { class: 'entry-commits' },
    shown.map(commitItem),
    rest > 0 ? el('li', { class: 'entry-commits-more', text: '… e altri ' + rest }) : null);
}

function buildText(entry, kind) {
  const isCommits = kind === 'github' && entry.meta && entry.meta.kind === 'commits';
  return el('div', { class: 'entry-text', text: isCommits ? firstLine(entry.text) : String(entry.text || '') });
}

/* ── Inline edit ───────────────────────────────────────────────── */

/**
 * Replace the .entry-text node with a textarea. Enter commits, Esc cancels,
 * blur commits. `onCommit(entry, newText)` must return a promise; the text
 * node is restored when the textarea is still attached afterwards (i.e. the
 * list was not re-rendered by a successful update).
 */
export function beginInlineEdit(card, entry, textEl, onCommit) {
  if (!card || card.querySelector('.entry-edit-input')) return;
  const original = String(entry.text || '');
  const textarea = el('textarea', { class: 'entry-edit-input', rows: 2, maxlength: 4000, 'aria-label': 'Modifica attività' });
  textarea.value = original;
  textEl.replaceWith(textarea);
  autoResize(textarea, EDIT_TEXTAREA_MAX_HEIGHT);
  textarea.focus();
  textarea.setSelectionRange(original.length, original.length);

  let done = false;
  const restore = () => { if (textarea.isConnected) textarea.replaceWith(textEl); };
  const finish = (commit) => {
    if (done) return;
    done = true;
    const next = textarea.value.trim();
    if (!commit || !next || next === original || !textarea.isConnected) { restore(); return; }
    textarea.disabled = true;
    Promise.resolve(onCommit(entry, next)).catch(() => undefined).then(restore);
  };

  textarea.addEventListener('input', () => autoResize(textarea, EDIT_TEXTAREA_MAX_HEIGHT));
  textarea.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); finish(true); }
    else if (event.key === 'Escape') { event.preventDefault(); finish(false); }
  });
  textarea.addEventListener('blur', () => finish(true));
}

/* ── Card ──────────────────────────────────────────────────────── */

/**
 * Build one `.entry-card` element.
 * @param {object} entry     server entry {id, type, text, time, duration_min, meta}
 * @param {{onEdit: Function, onDelete: Function}} handlers
 */
export function buildEntryCard(entry, handlers) {
  const kind = entryKind(entry);
  const textEl = buildText(entry, kind);
  const card = el('div', { class: 'entry-card ' + TYPE_CLASS[kind], dataset: { id: String(entry.id) } });

  const editBtn = el('button', {
    class: 'entry-btn', type: 'button', title: 'Modifica', 'aria-label': 'Modifica',
    onClick: () => beginInlineEdit(card, entry, card.querySelector('.entry-text') || textEl, handlers.onEdit),
  }, iconEl('edit', 13));
  const deleteBtn = el('button', {
    class: 'entry-btn', type: 'button', title: 'Elimina', 'aria-label': 'Elimina',
  }, iconEl('trash', 13));
  deleteBtn.addEventListener('click', () => handlers.onDelete(entry, deleteBtn));

  card.appendChild(buildTopRow(entry, kind));
  card.appendChild(textEl);
  const commits = commitList(entry);
  if (commits) card.appendChild(commits);
  card.appendChild(el('div', { class: 'entry-actions' }, editBtn, deleteBtn));
  return card;
}
