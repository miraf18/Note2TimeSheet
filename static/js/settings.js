/**
 * Settings modal — content of the Generale / Pratiche / Prompt AI panes
 * (docs/FRONTEND_CONTRACT.md §7.4). The modal shell (open/close, tabs) lives
 * in app.js; the Integrazioni pane is owned by integrations.js.
 *
 * Load strategy: on 'ts:modal-open' of #settingsModal reload /api/settings
 * and /api/practices; every pane renders from the store when its keys change
 * (and once at init, since data may already be there or arrive later).
 */
import { initGeneralPane } from './settings-general.js';
import { initPracticesPane } from './settings-practices.js';
import { initPromptPane } from './settings-prompt.js';

const MODAL_ID = 'settingsModal';
const MODAL_OPEN_EVENT = 'ts:modal-open';
const ALL_KEYS = Object.freeze(['settings', 'practices', 'config']);

const MSG = Object.freeze({
  settingsLoadFailed: 'Impossibile caricare le impostazioni.',
  practicesLoadFailed: 'Impossibile caricare le pratiche.',
});

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 */
export function initSettings(ctx) {
  const { store, api, utils, actions } = ctx;
  const panes = Object.freeze({
    general: initGeneralPane(ctx),
    practices: initPracticesPane(ctx),
    prompt: initPromptPane(ctx),
  });

  function renderPanes(state, changed) {
    if (changed.includes('settings') || changed.includes('config')) panes.general.render(state);
    if (changed.includes('practices')) panes.practices.render(state);
    if (changed.includes('settings')) panes.prompt.render(state);
  }

  function reportLoadError(err, fallback) {
    console.error('[settings] load failed', err);
    utils.showToast(api.errorMessage(err, fallback), 'error');
  }

  function reload() {
    actions.refreshSettings().catch((err) => reportLoadError(err, MSG.settingsLoadFailed));
    actions.refreshPractices().catch((err) => reportLoadError(err, MSG.practicesLoadFailed));
  }

  // Subscribe first: init runs before the initial data is loaded.
  store.subscribe((state, prev, changed) => renderPanes(state, changed));
  renderPanes(store.get(), ALL_KEYS);

  const modal = utils.byId(MODAL_ID);
  if (modal) modal.addEventListener(MODAL_OPEN_EVENT, reload);
  else console.warn('[settings] #' + MODAL_ID + ' not found');
}
