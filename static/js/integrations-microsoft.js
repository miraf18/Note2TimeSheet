/**
 * Integrations — Microsoft card (#msCard)
 * (docs/FRONTEND_CONTRACT.md §7.4 "Integrazioni").
 *
 * Status (pill, account, error, buttons, Outlook availability) renders from
 * `store.microsoftStatus`; Client ID / Tenant / source select render from
 * `store.settings.settings.microsoft`.
 *
 *   initMicrosoftCard(ctx, startDeviceLogin) → { render(state, changed) }
 */

const SOURCE_IDS = Object.freeze({ clientId: 'msClientId', tenantId: 'msTenantId', source: 'msSource' });
const DEFAULT_TENANT = 'common';

const MSG = Object.freeze({
  statusUnavailable: 'Impossibile leggere lo stato Microsoft.',
  connectHint: 'Inserisci prima Client ID e Tenant della App registration (sezione qui sotto).',
  disconnected: 'Account Microsoft disconnesso.',
  disconnectFailed: 'Impossibile disconnettere l\'account Microsoft.',
  confirmDisconnect: 'Disconnettere l\'account Microsoft?',
  clientSaved: 'Configurazione Microsoft salvata.',
  clientSaveFailed: 'Impossibile salvare la configurazione Microsoft.',
  sourceSaved: 'Origine riunioni aggiornata.',
  sourceSaveFailed: 'Impossibile aggiornare l\'origine riunioni.',
  outlookUnavailable: 'Outlook desktop non è disponibile su questo sistema (richiede Windows con Outlook installato).',
  noProvider: 'Nessuna origine attiva: collega un account Microsoft.',
  activeProvider: (label) => 'Origine attiva: ' + label + '.',
  defaultAccount: 'Account Microsoft',
});

const SOURCE_HINTS = Object.freeze({
  auto: 'Automatico: usa l\'account Microsoft se collegato, altrimenti Outlook desktop.',
  graph: 'Le riunioni vengono lette dal calendario dell\'account Microsoft collegato.',
  outlook_com: 'Le riunioni vengono lette dall\'Outlook desktop installato su questo PC.',
});

const PROVIDER_LABELS = Object.freeze({ graph: 'account Microsoft (Graph)', outlook_com: 'Outlook desktop' });

const PILLS = Object.freeze({
  unavailable: { cls: 'error', text: 'Stato non disponibile' },
  connected: { cls: 'connected', text: 'Connesso' },
  pending: { cls: 'pending', text: 'In attesa…' },
  outlook: { cls: 'connected', text: 'Outlook desktop' },
  unconfigured: { cls: 'unconfigured', text: 'Non configurato' },
  error: { cls: 'error', text: 'Errore' },
  disconnected: { cls: '', text: 'Non connesso' },
});

function pillFor(status) {
  if (!status) return PILLS.unavailable;
  if (status.connected) return PILLS.connected;
  if (status.pending) return PILLS.pending;
  if (status.provider === 'outlook_com') return PILLS.outlook;
  if (!status.configured) return PILLS.unconfigured;
  if (status.error) return PILLS.error;
  return PILLS.disconnected;
}

function microsoftSettingsOf(payload) {
  return (payload && payload.settings && payload.settings.microsoft) || {};
}

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 * @param {(provider: string, onConnected?: Function) => void} startDeviceLogin
 */
export function initMicrosoftCard(ctx, startDeviceLogin) {
  const { store, api, utils, actions } = ctx;
  const { byId, show, showToast, setBusy } = utils;

  let rendered = {};          // input id → last value written (dirty detection)
  let lastConfigured = null;  // <details>.open is only forced when `configured` changes

  const on = (id, event, handler) => { const node = byId(id); if (node) node.addEventListener(event, handler); };
  const onEnter = (handler) => (event) => { if (event.key === 'Enter') { event.preventDefault(); handler(); } };

  function setText(id, text) {
    const node = byId(id);
    if (node) node.textContent = text;
  }

  function sync(id, next) {
    const node = byId(id);
    if (!node) return;
    const last = rendered[id];
    const dirty = last !== undefined && node.value !== last;
    if (!dirty || next !== last) node.value = next;
    rendered = Object.assign({}, rendered, { [id]: next });
  }

  function refreshQuietly(...promises) {
    return Promise.all(promises).catch((err) => console.warn('[microsoft] refresh failed', err));
  }

  /* ── Status rendering ──────────────────────────────────────── */

  function renderPill(status) {
    const pill = byId('msStatus');
    if (!pill) return;
    const { cls, text } = pillFor(status);
    pill.className = 'status-pill' + (cls ? ' ' + cls : '');
    pill.textContent = text;
  }

  function renderIdentity(status) {
    const account = status && status.connected && status.account ? status.account : null;
    show(byId('msIdentity'), !!account);
    if (!account) return;
    const name = String(account.name || account.username || MSG.defaultAccount);
    const username = String(account.username || '');
    setText('msAvatar', name.trim().charAt(0).toUpperCase() || 'M');
    setText('msAccountName', name);
    setText('msAccount', username && username !== name ? username : '');
  }

  function renderError(status) {
    const box = byId('msError');
    if (!box) return;
    const error = !status ? MSG.statusUnavailable : (typeof status.error === 'string' ? status.error : '');
    box.textContent = error;
    show(box, !!error);
  }

  function renderButtons(status) {
    const connected = !!(status && status.connected);
    const configured = !!(status && status.configured);
    const connectBtn = byId('msConnectBtn');
    if (connectBtn) {
      show(connectBtn, !connected);
      connectBtn.disabled = !configured;
      connectBtn.title = configured ? '' : MSG.connectHint;
    }
    show(byId('msDisconnectBtn'), connected);
  }

  function renderClientDetails(status) {
    const configured = !!(status && status.configured);
    const details = byId('msClientDetails');
    if (details && configured !== lastConfigured) details.open = !configured;
    lastConfigured = configured;
  }

  function renderSourceAvailability(status) {
    const available = !!(status && status.outlook_com_available);
    const outlookOption = byId('msSourceOutlook');
    if (outlookOption) outlookOption.disabled = !available;
    const hint = byId('msSourceHint');
    if (!hint) return;
    const select = byId(SOURCE_IDS.source);
    const source = select ? select.value : 'auto';
    const parts = [SOURCE_HINTS[source] || ''];
    if (!available) parts.push(MSG.outlookUnavailable);
    if (status) parts.push(status.provider ? MSG.activeProvider(PROVIDER_LABELS[status.provider] || status.provider) : MSG.noProvider);
    hint.textContent = parts.filter(Boolean).join(' ');
  }

  function renderStatus(state) {
    const status = state.microsoftStatus;
    renderPill(status);
    renderIdentity(status);
    renderError(status);
    renderButtons(status);
    renderClientDetails(status);
    renderSourceAvailability(status);
  }

  /* ── Settings rendering (client id, tenant, source) ────────── */

  function renderSettings(state) {
    if (!state.settings) return;
    const ms = microsoftSettingsOf(state.settings);
    sync(SOURCE_IDS.clientId, ms.client_id || '');
    sync(SOURCE_IDS.tenantId, ms.tenant_id || DEFAULT_TENANT);
    const fallbackSource = (state.microsoftStatus && state.microsoftStatus.source) || 'auto';
    sync(SOURCE_IDS.source, ms.source || fallbackSource);
    renderSourceAvailability(state.microsoftStatus);
  }

  /* ── Actions ───────────────────────────────────────────────── */

  async function disconnect() {
    if (!window.confirm(MSG.confirmDisconnect)) return;
    const btn = byId('msDisconnectBtn');
    setBusy(btn, true);
    try {
      await api.microsoft.disconnect();
      showToast(MSG.disconnected, 'success');
      await refreshQuietly(actions.refreshIntegrations(), actions.refreshConfig());
    } catch (err) {
      console.error('[microsoft] disconnect failed', err);
      showToast(api.errorMessage(err, MSG.disconnectFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  async function saveClient() {
    const btn = byId('msSaveClientBtn');
    const read = (id) => { const node = byId(id); return node ? node.value.trim() : ''; };
    const patch = { client_id: read(SOURCE_IDS.clientId), tenant_id: read(SOURCE_IDS.tenantId) || DEFAULT_TENANT };
    setBusy(btn, true);
    try {
      await api.updateSettings({ microsoft: patch });
      rendered = {};
      showToast(MSG.clientSaved, 'success');
      await refreshQuietly(actions.refreshSettings(), actions.refreshIntegrations());
    } catch (err) {
      console.error('[microsoft] save client failed', err);
      showToast(api.errorMessage(err, MSG.clientSaveFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  async function changeSource() {
    const select = byId(SOURCE_IDS.source);
    if (!select) return;
    const source = select.value;
    const previous = rendered[SOURCE_IDS.source];
    select.disabled = true;
    try {
      await api.updateSettings({ microsoft: { source } });
      rendered = {};
      showToast(MSG.sourceSaved, 'success');
      await refreshQuietly(actions.refreshSettings(), actions.refreshIntegrations(), actions.refreshConfig());
    } catch (err) {
      console.error('[microsoft] change source failed', err);
      showToast(api.errorMessage(err, MSG.sourceSaveFailed), 'error');
      if (previous !== undefined) select.value = previous;
    } finally {
      select.disabled = false;
      renderSourceAvailability(store.get().microsoftStatus);
    }
  }

  /* ── Public API ────────────────────────────────────────────── */

  function render(state, changed) {
    if (changed.includes('microsoftStatus')) renderStatus(state);
    if (changed.includes('settings')) renderSettings(state);
  }

  function bind() {
    on('msConnectBtn', 'click', () => startDeviceLogin('microsoft'));
    on('msDisconnectBtn', 'click', disconnect);
    on('msSaveClientBtn', 'click', saveClient);
    on(SOURCE_IDS.clientId, 'keydown', onEnter(saveClient));
    on(SOURCE_IDS.tenantId, 'keydown', onEnter(saveClient));
    on(SOURCE_IDS.source, 'change', changeSource);
  }

  bind();
  return Object.freeze({ render });
}
