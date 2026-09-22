/**
 * Integrations — content of the Integrazioni pane (#ghCard, #msCard) and the
 * shared device-code modal #deviceModal (docs/FRONTEND_CONTRACT.md §7.4/§7.5).
 *
 * Listens:
 *   - document 'ts:settings-tab' {tab:'integrations'} → refresh statuses and
 *     lazily load the GitHub repo list
 *   - document 'ts:device-login' {provider, onConnected} → device flow
 *   - #deviceModal 'ts:modal-close' → stop polling (inside device-modal.js)
 * Reacts to store keys: githubStatus, microsoftStatus, settings.
 */
import { initDeviceModal } from './device-modal.js';
import { initGithubCard } from './integrations-github.js';
import { initMicrosoftCard } from './integrations-microsoft.js';

const INTEGRATIONS_TAB = 'integrations';
const SETTINGS_TAB_EVENT = 'ts:settings-tab';
const DEVICE_LOGIN_EVENT = 'ts:device-login';
const ALL_KEYS = Object.freeze(['githubStatus', 'microsoftStatus', 'settings']);

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 */
export function initIntegrations(ctx) {
  const { store, actions } = ctx;
  const device = initDeviceModal(ctx);
  const github = initGithubCard(ctx, device.startDeviceLogin);
  const microsoft = initMicrosoftCard(ctx, device.startDeviceLogin);

  function renderCards(state, changed) {
    github.render(state, changed);
    microsoft.render(state, changed);
  }

  function onSettingsTab(event) {
    const tab = event.detail && event.detail.tab;
    if (tab !== INTEGRATIONS_TAB) return;
    actions.refreshIntegrations().catch((err) => console.warn('[integrations] status refresh failed', err));
    github.onTabShown(store.get());
  }

  function onDeviceLogin(event) {
    const detail = event.detail || {};
    device.startDeviceLogin(detail.provider, detail.onConnected);
  }

  // Subscribe first: init runs before the initial data is loaded.
  store.subscribe((state, prev, changed) => renderCards(state, changed));
  renderCards(store.get(), ALL_KEYS);

  document.addEventListener(SETTINGS_TAB_EVENT, onSettingsTab);
  document.addEventListener(DEVICE_LOGIN_EVENT, onDeviceLogin);
}
