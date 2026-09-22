/**
 * Settings modal — "Pratiche" pane (docs/FRONTEND_CONTRACT.md §7.4, v1
 * behaviour): list of practice rows, inline edit form, double-click-to-confirm
 * delete and the add form (Enter in any input adds).
 *
 * After each CRUD call the practices list returned by the server is written
 * to the store (`store.set({practices})`) and the list re-renders from there.
 *
 *   initPracticesPane(ctx) → { render(state) }
 */

const CONFIRM_DELETE_MS = 2500;
const NEW_INPUT_IDS = Object.freeze(['newCode', 'newName', 'newDesc']);

const MSG = Object.freeze({
  empty: 'Nessuna pratica configurata.',
  nameRequired: 'Il nome è obbligatorio.',
  codeNameRequired: 'Codice e nome sono obbligatori.',
  added: 'Pratica aggiunta.',
  addFailed: 'Impossibile aggiungere la pratica.',
  updated: 'Pratica aggiornata.',
  updateFailed: 'Impossibile aggiornare la pratica.',
  deleted: 'Pratica eliminata.',
  deleteFailed: 'Impossibile eliminare la pratica.',
  editTitle: 'Modifica',
  deleteTitle: 'Elimina',
  confirmDeleteTitle: 'Clicca ancora per confermare',
  save: 'Salva',
});

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 * @returns {{render: (state: object) => void}}
 */
export function initPracticesPane(ctx) {
  const { store, api, utils, actions } = ctx;
  const { byId, el, clear, iconEl, showToast, setBusy } = utils;

  let pendingDeleteCode = null;

  /* ── Rendering ─────────────────────────────────────────────── */

  function render(state) {
    const list = byId('practicesList');
    if (!list) return;
    clear(list);
    pendingDeleteCode = null;
    const practices = Array.isArray(state && state.practices) ? state.practices : [];
    if (!practices.length) {
      list.appendChild(el('div', { class: 'empty-state', style: { minHeight: '80px' } },
        el('p', { text: MSG.empty })));
      return;
    }
    practices.forEach((practice) => list.appendChild(buildRow(practice)));
  }

  function buildRow(practice) {
    const code = String(practice.code);
    const row = el('div', { class: 'practice-row', dataset: { code } });
    const editBtn = el('button', {
      class: 'entry-btn', type: 'button', title: MSG.editTitle,
      'aria-label': 'Modifica pratica ' + code,
      onClick: () => startEdit(row, practice),
    }, iconEl('edit', 13));
    const deleteBtn = el('button', {
      class: 'entry-btn', type: 'button', title: MSG.deleteTitle,
      'aria-label': 'Elimina pratica ' + code,
      onClick: () => handleDelete(code, deleteBtn),
    }, iconEl('trash', 13));
    row.appendChild(el('div', { class: 'practice-row-top' },
      el('span', { class: 'practice-row-code', text: code }),
      el('div', { class: 'practice-row-info' },
        el('div', { class: 'practice-row-name', text: practice.name || '' }),
        el('div', { class: 'practice-row-desc', text: practice.description || '' })),
      el('div', { class: 'practice-row-actions' }, editBtn, deleteBtn)));
    return row;
  }

  /* ── Store update ──────────────────────────────────────────── */

  function applyPractices(data) {
    if (data && Array.isArray(data.practices)) {
      store.set({ practices: data.practices });
      return Promise.resolve();
    }
    return actions.refreshPractices().catch((err) => console.warn('[practices] refresh failed', err));
  }

  /* ── Inline edit ───────────────────────────────────────────── */

  function startEdit(row, practice) {
    if (row.querySelector('.practice-edit-form')) return;
    const nameInput = el('input', { type: 'text', value: practice.name || '', placeholder: 'Nome', maxlength: 120 });
    const descInput = el('input', { type: 'text', value: practice.description || '', placeholder: 'Descrizione', maxlength: 300 });
    const saveBtn = el('button', { class: 'btn btn-primary btn-sm', type: 'button', text: MSG.save });
    const form = el('div', { class: 'practice-edit-form' }, nameInput, descInput, saveBtn);
    const submit = () => saveEdit(practice.code, { nameInput, descInput, saveBtn, form });
    saveBtn.addEventListener('click', submit);
    form.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); submit(); }
      if (event.key === 'Escape') { event.stopPropagation(); form.remove(); }
    });
    row.appendChild(form);
    nameInput.focus();
  }

  async function saveEdit(code, refs) {
    const name = refs.nameInput.value.trim();
    const description = refs.descInput.value.trim();
    if (!name) { showToast(MSG.nameRequired, 'error'); refs.nameInput.focus(); return; }
    setBusy(refs.saveBtn, true);
    try {
      const data = await api.updatePractice(code, { name, description });
      refs.form.remove();
      await applyPractices(data);
      showToast(MSG.updated, 'success');
    } catch (err) {
      console.error('[practices] update failed', err);
      showToast(api.errorMessage(err, MSG.updateFailed), 'error');
      setBusy(refs.saveBtn, false);
    }
  }

  /* ── Delete (double click to confirm) ──────────────────────── */

  function resetPendingDelete() {
    if (pendingDeleteCode === null) return;
    const selector = '.practice-row[data-code="' + CSS.escape(pendingDeleteCode) + '"] .entry-btn.danger';
    const previous = document.querySelector(selector);
    if (previous) { previous.classList.remove('danger'); previous.title = MSG.deleteTitle; }
    pendingDeleteCode = null;
  }

  function handleDelete(code, btn) {
    if (pendingDeleteCode === code) {
      pendingDeleteCode = null;
      deletePractice(code, btn);
      return;
    }
    resetPendingDelete();
    pendingDeleteCode = code;
    btn.classList.add('danger');
    btn.title = MSG.confirmDeleteTitle;
    setTimeout(() => {
      if (pendingDeleteCode !== code) return;
      pendingDeleteCode = null;
      btn.classList.remove('danger');
      btn.title = MSG.deleteTitle;
    }, CONFIRM_DELETE_MS);
  }

  async function deletePractice(code, btn) {
    setBusy(btn, true);
    try {
      const data = await api.deletePractice(code);
      await applyPractices(data);
      showToast(MSG.deleted, 'success');
    } catch (err) {
      console.error('[practices] delete failed', err);
      showToast(api.errorMessage(err, MSG.deleteFailed), 'error');
      setBusy(btn, false);
    }
  }

  /* ── Add form ──────────────────────────────────────────────── */

  function readNewPractice() {
    const read = (id) => { const node = byId(id); return node ? node.value.trim() : ''; };
    return { code: read('newCode'), name: read('newName'), description: read('newDesc') };
  }

  function clearNewForm() {
    NEW_INPUT_IDS.forEach((id) => { const node = byId(id); if (node) node.value = ''; });
    const first = byId('newCode');
    if (first) first.focus();
  }

  async function addPractice() {
    const practice = readNewPractice();
    if (!practice.code || !practice.name) { showToast(MSG.codeNameRequired, 'error'); return; }
    const btn = byId('addPracticeBtn');
    setBusy(btn, true);
    try {
      const data = await api.addPractice(practice);
      clearNewForm();
      await applyPractices(data);
      showToast(MSG.added, 'success');
    } catch (err) {
      console.error('[practices] add failed', err);
      showToast(api.errorMessage(err, MSG.addFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  /* ── Wiring ────────────────────────────────────────────────── */

  function bind() {
    const addBtn = byId('addPracticeBtn');
    if (addBtn) addBtn.addEventListener('click', addPractice);
    NEW_INPUT_IDS.forEach((id) => {
      const input = byId(id);
      if (!input) return;
      input.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        addPractice();
      });
    });
  }

  bind();
  return Object.freeze({ render });
}
