/**
 * Settings modal — "Generale" pane (docs/FRONTEND_CONTRACT.md §7.4).
 *
 * Fields: user name, daily hours, timezone, history retention, OpenAI model
 * and the write-only OpenAI key. Renders from `store.settings` (the full
 * GET /api/settings payload). Inputs the user is currently editing are
 * preserved across re-renders unless the server value itself changed.
 *
 *   initGeneralPane(ctx) → { render(state) }
 */

const INPUT_IDS = Object.freeze({
  user_name: 'setUserName',
  daily_hours: 'setDailyHours',
  timezone: 'setTimezone',
  history_keep_days: 'setHistoryDays',
  openai_model: 'setModel',
});

const LIMITS = Object.freeze({ hoursMin: 0.25, hoursMax: 24, daysMin: 1, daysMax: 365 });

const MSG = Object.freeze({
  saved: 'Impostazioni salvate.',
  saveFailed: 'Impossibile salvare le impostazioni.',
  refreshFailed: 'Impostazioni salvate, ma il ricaricamento è fallito.',
  keyCleared: 'Chiave OpenAI rimossa.',
  keyClearFailed: 'Impossibile rimuovere la chiave OpenAI.',
  confirmClearKey: 'Rimuovere la chiave OpenAI salvata nelle impostazioni?',
  keyFromSettings: 'configurata ✓ (da impostazioni)',
  keyFromEnv: 'configurata ✓ (da .env)',
  keyMissing: 'mancante',
  clearKeyTitle: 'Rimuovi la chiave salvata nelle impostazioni',
  clearKeyDisabled: 'Nessuna chiave salvata nelle impostazioni da rimuovere',
  invalidHours: 'Le ore giornaliere devono essere un numero tra 0.25 e 24.',
  invalidDays: 'I giorni di storico devono essere un numero intero tra 1 e 365.',
  invalidTimezone: 'Seleziona un fuso orario.',
  saving: 'Salvataggio…',
});

function generalOf(payload) {
  return (payload && payload.settings && payload.settings.general) || {};
}

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 * @returns {{render: (state: object) => void}}
 */
export function initGeneralPane(ctx) {
  const { api, utils, actions } = ctx;
  const { byId, el, clear, show, showToast, setBusy } = utils;

  // id → last value written into the input (detects user edits in progress)
  let rendered = {};

  /* ── Rendering ─────────────────────────────────────────────── */

  function syncInput(id, next) {
    const input = byId(id);
    if (!input) return;
    const value = next == null ? '' : String(next);
    const last = rendered[id];
    const dirty = last !== undefined && input.value !== last;
    if (!dirty || value !== last) input.value = value;
    rendered = Object.assign({}, rendered, { [id]: value });
  }

  function renderTimezoneOptions(zones, current) {
    const select = byId(INPUT_IDS.timezone);
    if (!select) return;
    const list = Array.isArray(zones) ? zones.slice() : [];
    if (current && !list.includes(current)) list.push(current);
    const previous = select.value;
    clear(select);
    list.forEach((zone) => select.appendChild(el('option', { value: zone, text: zone })));
    // Keep what the user picked; syncInput decides whether the server wins.
    if (previous && list.includes(previous)) select.value = previous;
  }

  function renderKeyStatus(payload) {
    const node = byId('openaiKeyStatus');
    const isSet = payload.openai_key_set === true;
    const fromSettings = payload.openai_key_source === 'settings';
    if (node) {
      node.textContent = !isSet ? MSG.keyMissing : fromSettings ? MSG.keyFromSettings : MSG.keyFromEnv;
      node.classList.toggle('ok', isSet);
      node.classList.toggle('missing', !isSet);
    }
    const clearBtn = byId('clearOpenaiKeyBtn');
    if (clearBtn) {
      clearBtn.disabled = !fromSettings;
      clearBtn.title = fromSettings ? MSG.clearKeyTitle : MSG.clearKeyDisabled;
    }
  }

  function render(state) {
    const payload = state && state.settings;
    if (!payload) return;
    const general = generalOf(payload);
    renderTimezoneOptions(payload.timezones, general.timezone);
    Object.entries(INPUT_IDS).forEach(([key, id]) => syncInput(id, general[key]));
    renderKeyStatus(payload);
  }

  /* ── Form reading & validation ─────────────────────────────── */

  function readValue(id) {
    const input = byId(id);
    return input ? String(input.value).trim() : '';
  }

  function readGeneral() {
    return {
      user_name: readValue(INPUT_IDS.user_name),
      daily_hours: parseFloat(readValue(INPUT_IDS.daily_hours)),
      timezone: readValue(INPUT_IDS.timezone),
      history_keep_days: parseInt(readValue(INPUT_IDS.history_keep_days), 10),
      openai_model: readValue(INPUT_IDS.openai_model),
    };
  }

  function validate(general) {
    const errors = [];
    const hoursOk = general.daily_hours >= LIMITS.hoursMin && general.daily_hours <= LIMITS.hoursMax;
    if (!hoursOk) errors.push(MSG.invalidHours);
    const daysOk = Number.isInteger(general.history_keep_days) &&
      general.history_keep_days >= LIMITS.daysMin && general.history_keep_days <= LIMITS.daysMax;
    if (!daysOk) errors.push(MSG.invalidDays);
    if (!general.timezone) errors.push(MSG.invalidTimezone);
    return errors;
  }

  function showErrors(errors) {
    const box = byId('generalErrors');
    if (!box) return;
    clear(box);
    if (!errors.length) { show(box, false); return; }
    box.appendChild(el('ul', {}, errors.map((msg) => el('li', { text: msg }))));
    show(box, true);
  }

  function buildPatch(general) {
    const key = readValue('setOpenaiKey');
    return key ? { general, openai_api_key: key } : { general };
  }

  /* ── Actions ───────────────────────────────────────────────── */

  function refreshAfterSave() {
    return Promise.all([actions.refreshSettings(), actions.refreshConfig()]).catch((err) => {
      console.warn('[settings] refresh after save failed', err);
      showToast(api.errorMessage(err, MSG.refreshFailed), 'warning');
    });
  }

  function reportSaveError(err, fallback) {
    console.error('[settings] save failed', err);
    if (err instanceof api.ApiError && err.errors.length) { showErrors(err.errors); return; }
    showToast(api.errorMessage(err, fallback), 'error');
  }

  async function save() {
    const btn = byId('saveGeneralBtn');
    const general = readGeneral();
    const errors = validate(general);
    showErrors(errors);
    if (errors.length) return;
    setBusy(btn, true, MSG.saving);
    try {
      await api.updateSettings(buildPatch(general));
    } catch (err) {
      reportSaveError(err, MSG.saveFailed);
      setBusy(btn, false);
      return;
    }
    const keyInput = byId('setOpenaiKey');
    if (keyInput) keyInput.value = '';
    rendered = {}; // accept every server value on the next render
    showToast(MSG.saved, 'success');
    await refreshAfterSave();
    setBusy(btn, false);
  }

  async function clearKey() {
    if (!window.confirm(MSG.confirmClearKey)) return;
    const btn = byId('clearOpenaiKeyBtn');
    setBusy(btn, true);
    try {
      await api.updateSettings({ openai_api_key: '' });
    } catch (err) {
      reportSaveError(err, MSG.keyClearFailed);
      setBusy(btn, false);
      return;
    }
    showToast(MSG.keyCleared, 'success');
    await refreshAfterSave();
    setBusy(btn, false);
  }

  /* ── Wiring ────────────────────────────────────────────────── */

  function onEnter(event) {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    save();
  }

  function bind() {
    const saveBtn = byId('saveGeneralBtn');
    if (saveBtn) saveBtn.addEventListener('click', save);
    const clearBtn = byId('clearOpenaiKeyBtn');
    if (clearBtn) clearBtn.addEventListener('click', clearKey);
    const textInputs = Object.values(INPUT_IDS).concat(['setOpenaiKey']);
    textInputs.forEach((id) => {
      const input = byId(id);
      if (input && input.tagName === 'INPUT') input.addEventListener('keydown', onEnter);
    });
  }

  bind();
  return Object.freeze({ render });
}
