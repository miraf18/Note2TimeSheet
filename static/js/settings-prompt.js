/**
 * Settings modal — "Prompt AI" pane (docs/FRONTEND_CONTRACT.md §7.4).
 *
 * Four editable prompt sections (`.prompt-textarea[data-field]`) filled from
 * `settings.ai_effective`, "modificato" badges when a section differs from
 * `settings.ai_defaults`, placeholder chips inserted at the caret of the last
 * focused textarea, a soft warning when `{{practices}}` / `{{entries}}` are
 * missing, per-field reset (server side, immediate), preview of the saved
 * prompts for the current day and Save.
 *
 *   initPromptPane(ctx) → { render(state) }
 */

const FIELDS = Object.freeze(['system_intro', 'system_rules', 'system_output', 'user_template']);
const SYSTEM_FIELDS = Object.freeze(['system_intro', 'system_rules', 'system_output']);
const USER_FIELD = 'user_template';
const TOKEN_PRACTICES = '{{practices}}';
const TOKEN_ENTRIES = '{{entries}}';

const MSG = Object.freeze({
  saved: 'Prompt salvati.',
  saveFailed: 'Impossibile salvare i prompt.',
  reset: 'Sezione ripristinata al testo predefinito.',
  resetFailed: 'Impossibile ripristinare la sezione.',
  confirmReset: 'Ripristinare il testo predefinito di questa sezione? Le modifiche salvate andranno perse.',
  previewFailed: 'Impossibile generare l\'anteprima.',
  pickField: 'Clicca prima in un campo di testo, poi scegli il segnaposto.',
  warnPractices: 'Il segnaposto {{practices}} non compare in nessuna sezione di sistema: l\'AI non conoscerà le pratiche.',
  warnEntries: 'Il segnaposto {{entries}} non compare nel messaggio utente: le attività non verranno inviate.',
  previewOpen: 'Anteprima prompt del giorno',
  previewClose: 'Nascondi anteprima',
  previewUnsaved: ' · anteprima dei prompt salvati',
  saving: 'Salvataggio…',
  loading: 'Caricamento…',
});

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 * @returns {{render: (state: object) => void}}
 */
export function initPromptPane(ctx) {
  const { store, api, utils, actions } = ctx;
  const { byId, qs, qsa, el, clear, show, icon, showToast, setBusy, pluralize, currentDate } = utils;

  let rendered = {};   // field → last effective text written into the textarea
  let defaults = {};   // field → built-in default text (from settings.ai_defaults)
  let lastFocused = null;

  const textareaFor = (field) => qs('.prompt-textarea[data-field="' + field + '"]');
  const badgeFor = (field) => qs('.prompt-badge[data-modified-for="' + field + '"]');
  const valueOf = (field) => { const ta = textareaFor(field); return ta ? ta.value : ''; };
  const hasDefaults = () => Object.keys(defaults).length > 0;

  /* ── Rendering ─────────────────────────────────────────────── */

  function syncTextarea(field, next) {
    const ta = textareaFor(field);
    if (!ta) return;
    const value = next == null ? '' : String(next);
    const last = rendered[field];
    const dirty = last !== undefined && ta.value !== last;
    if (!dirty || value !== last) ta.value = value;
    rendered = Object.assign({}, rendered, { [field]: value });
  }

  function renderChips(placeholders) {
    const box = byId('promptPlaceholders');
    if (!box) return;
    clear(box);
    (Array.isArray(placeholders) ? placeholders : []).forEach((ph) => {
      const token = ph.token || ('{{' + ph.name + '}}');
      box.appendChild(el('button', {
        class: 'chip', type: 'button', title: ph.description || '', dataset: { token },
        onClick: () => insertToken(token),
      }, token));
    });
  }

  function render(state) {
    const payload = state && state.settings;
    if (!payload) return;
    defaults = Object.assign({}, payload.ai_defaults || {});
    const effective = payload.ai_effective || {};
    FIELDS.forEach((field) => syncTextarea(field, effective[field]));
    renderChips(payload.placeholders);
    refreshIndicators();
  }

  /* ── Modified badges & warning ─────────────────────────────── */

  function refreshIndicators() {
    if (!hasDefaults()) return;
    FIELDS.forEach((field) => {
      const ta = textareaFor(field);
      if (!ta) return;
      const modified = ta.value !== defaults[field];
      ta.classList.toggle('modified', modified);
      show(badgeFor(field), modified);
    });
    refreshWarning();
  }

  function missingTokens() {
    const systemText = SYSTEM_FIELDS.map(valueOf).join('\n');
    return {
      practices: !systemText.includes(TOKEN_PRACTICES),
      entries: !valueOf(USER_FIELD).includes(TOKEN_ENTRIES),
    };
  }

  function refreshWarning() {
    const missing = missingTokens();
    qsa('#promptPlaceholders .chip').forEach((chip) => {
      const token = chip.dataset.token;
      const isMissing = (token === TOKEN_PRACTICES && missing.practices) ||
        (token === TOKEN_ENTRIES && missing.entries);
      chip.classList.toggle('missing', isMissing);
    });
    const box = byId('promptWarning');
    if (!box) return;
    const lines = [missing.practices && MSG.warnPractices, missing.entries && MSG.warnEntries].filter(Boolean);
    clear(box);
    if (!lines.length) { show(box, false); return; }
    box.appendChild(el('span', { html: icon('alert', 14), 'aria-hidden': 'true' }));
    box.appendChild(el('div', {}, lines.map((line) => el('div', { text: line }))));
    show(box, true);
  }

  /* ── Placeholder insertion ─────────────────────────────────── */

  function insertToken(token) {
    const ta = lastFocused && lastFocused.isConnected ? lastFocused : null;
    if (!ta) { showToast(MSG.pickField, 'info'); return; }
    const start = typeof ta.selectionStart === 'number' ? ta.selectionStart : ta.value.length;
    const end = typeof ta.selectionEnd === 'number' ? ta.selectionEnd : start;
    ta.value = ta.value.slice(0, start) + token + ta.value.slice(end);
    const caret = start + token.length;
    ta.focus();
    ta.setSelectionRange(caret, caret);
    refreshIndicators();
  }

  /* ── Reset / save ──────────────────────────────────────────── */

  async function resetField(field, btn) {
    if (!FIELDS.includes(field) || !hasDefaults()) return;
    const ta = textareaFor(field);
    const alreadyDefault = ta && ta.value === defaults[field];
    if (!alreadyDefault && !window.confirm(MSG.confirmReset)) return;
    setBusy(btn, true);
    try {
      await api.resetAiPrompts([field]);
      if (ta) ta.value = defaults[field];
      rendered = Object.assign({}, rendered, { [field]: defaults[field] });
      refreshIndicators();
      showToast(MSG.reset, 'success');
      await actions.refreshSettings().catch((err) => console.warn('[prompt] refresh failed', err));
    } catch (err) {
      console.error('[prompt] reset failed', err);
      showToast(api.errorMessage(err, MSG.resetFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  /** Fields equal to their default (or blank) are sent as null = "use default". */
  function buildAiPatch() {
    return FIELDS.reduce((patch, field) => {
      const value = valueOf(field);
      const useDefault = value === defaults[field] || !value.trim();
      return Object.assign({}, patch, { [field]: useDefault ? null : value });
    }, {});
  }

  async function savePrompts() {
    const btn = byId('savePromptBtn');
    setBusy(btn, true, MSG.saving);
    try {
      await api.updateSettings({ ai: buildAiPatch() });
      rendered = {};
      showToast(MSG.saved, 'success');
      await actions.refreshSettings().catch((err) => console.warn('[prompt] refresh failed', err));
    } catch (err) {
      console.error('[prompt] save failed', err);
      showToast(api.errorMessage(err, MSG.saveFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  /* ── Preview ───────────────────────────────────────────────── */

  function hasUnsavedChanges() {
    return FIELDS.some((field) => rendered[field] !== undefined && valueOf(field) !== rendered[field]);
  }

  async function loadPreview(btn) {
    setBusy(btn, true, MSG.loading);
    try {
      const data = await api.previewPrompt(currentDate(store.get()));
      const systemNode = byId('previewSystem');
      const userNode = byId('previewUser');
      const meta = byId('previewMeta');
      if (systemNode) systemNode.textContent = data.system_prompt || '';
      if (userNode) userNode.textContent = data.user_prompt || '';
      if (meta) {
        meta.textContent = pluralize(data.entry_count, 'attività', 'attività') +
          (hasUnsavedChanges() ? MSG.previewUnsaved : '');
      }
    } catch (err) {
      console.error('[prompt] preview failed', err);
      showToast(api.errorMessage(err, MSG.previewFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  async function togglePreview() {
    const btn = byId('promptPreviewBtn');
    const box = byId('promptPreview');
    if (!btn || !box) return;
    const opening = box.classList.contains('hidden');
    show(box, opening);
    btn.setAttribute('aria-expanded', String(opening));
    if (opening) await loadPreview(btn);
    btn.textContent = opening ? MSG.previewClose : MSG.previewOpen;
  }

  /* ── Wiring ────────────────────────────────────────────────── */

  function bind() {
    const pane = byId('panePrompt');
    if (!pane) return;
    pane.addEventListener('focusin', (event) => {
      if (event.target.classList.contains('prompt-textarea')) lastFocused = event.target;
    });
    // pointerdown fires before the chip steals focus: remember the textarea
    // even when focusin was not delivered (e.g. window without system focus).
    pane.addEventListener('pointerdown', (event) => {
      if (!event.target.closest || !event.target.closest('.chip')) return;
      const active = document.activeElement;
      if (active && active.classList.contains('prompt-textarea')) lastFocused = active;
    });
    pane.addEventListener('input', (event) => {
      if (event.target.classList.contains('prompt-textarea')) refreshIndicators();
    });
    qsa('.prompt-reset-btn[data-field]', pane).forEach((btn) => {
      btn.addEventListener('click', () => resetField(btn.dataset.field, btn));
    });
    const previewBtn = byId('promptPreviewBtn');
    if (previewBtn) previewBtn.addEventListener('click', togglePreview);
    const saveBtn = byId('savePromptBtn');
    if (saveBtn) saveBtn.addEventListener('click', savePrompts);
  }

  bind();
  return Object.freeze({ render });
}
