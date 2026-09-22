/**
 * Day selector (header): fills #daySelect from store.days, keeps its value in
 * sync with the day currently shown and toggles the "storico" badge when a
 * past day is selected.
 *
 * Contract: docs/FRONTEND_CONTRACT.md §7.1. Reacts to store keys
 * days / selectedDate / config. Day changes go through actions.selectDay().
 */
import * as utils from './utils.js';

const { byId, el, clear, show, dayLabel, pluralize, serverToday, currentDate, isToday, isISODay } = utils;

const WATCHED_KEYS = Object.freeze(['days', 'selectedDate', 'config']);

/** "Oggi · Mer 16 set · 3 voci ✓" (✓ elaborated, • entries but not elaborated). */
function optionLabel(day, today) {
  const count = Number(day.entry_count) || 0;
  const base = dayLabel(day.date, today);
  const voci = count ? ' · ' + pluralize(count, 'voce', 'voci') : '';
  const marker = day.elaborated ? ' ✓' : count ? ' •' : '';
  return base + voci + marker;
}

/** Newest-first list of days, guaranteed to contain today and the shown day. */
function buildDayList(state) {
  const days = (Array.isArray(state.days) ? state.days : []).filter((d) => d && isISODay(d.date));
  const known = new Set(days.map((d) => d.date));
  const extras = [serverToday(state), currentDate(state)]
    .filter((date, index, arr) => isISODay(date) && !known.has(date) && arr.indexOf(date) === index)
    .map((date) => ({ date, entry_count: 0, elaborated: false }));
  return days.concat(extras).slice().sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
}

function render(state) {
  const select = byId('daySelect');
  if (!select) return;
  const today = serverToday(state);
  const current = currentDate(state);
  clear(select);
  buildDayList(state).forEach((day) => {
    select.appendChild(el('option', { value: day.date, text: optionLabel(day, today) }));
  });
  select.value = current;
  show(byId('pastDayBadge'), !isToday(state));
}

function bindSelect(ctx) {
  const select = byId('daySelect');
  if (!select) return;
  select.addEventListener('change', () => {
    const value = select.value;
    if (isISODay(value)) {
      ctx.actions.selectDay(value);
      return;
    }
    console.warn('[history] invalid day value', value);
    select.value = currentDate(ctx.store.get());
  });
}

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 */
export function initHistory(ctx) {
  bindSelect(ctx);
  ctx.store.subscribe((state, prev, changed) => {
    if (changed.some((key) => WATCHED_KEYS.includes(key))) render(state);
  });
  render(ctx.store.get());
}
