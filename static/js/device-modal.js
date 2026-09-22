/**
 * Shared device-code modal (#deviceModal) for the GitHub / Microsoft device
 * flows (docs/FRONTEND_CONTRACT.md §7.5).
 *
 *   const device = initDeviceModal(ctx);
 *   device.startDeviceLogin('github', onConnected?)
 *
 * startDeviceLogin: POST /api/<provider>/connect → fill + open the modal, open
 * the verification page once, then poll GET /api/<provider>/status every 3 s
 * until `connected` (toast, refresh integrations + config, close after 800 ms,
 * call onConnected), `error` (show, stop) or the code expires.
 * Polling stops on 'ts:modal-close' / Annulla.
 */

const MODAL_ID = 'deviceModal';
const POLL_INTERVAL_MS = 3000;
const CLOSE_DELAY_MS = 800;
const COPIED_MS = 1200;
const DEFAULT_EXPIRES_S = 900;
const MODAL_CLOSE_EVENT = 'ts:modal-close';

const PROVIDERS = Object.freeze({
  github: Object.freeze({ title: 'Collega GitHub', name: 'GitHub' }),
  microsoft: Object.freeze({ title: 'Collega account Microsoft', name: 'Microsoft' }),
});

const MSG = Object.freeze({
  defaultHint: 'Apri la pagina di login e inserisci questo codice:',
  waiting: 'In attesa del login…',
  connected: 'Collegato!',
  expired: 'Codice scaduto, riprova.',
  notConfigured: 'Inserisci prima il Client ID nella sezione Integrazioni delle impostazioni.',
  startFailed: 'Impossibile avviare il collegamento.',
  invalidDevice: 'Risposta del server non valida: codice mancante.',
  unknownProvider: 'Provider non supportato.',
  copied: 'Codice copiato negli appunti.',
  copyFailed: 'Impossibile copiare il codice.',
  connectedToast: (name) => 'Account ' + name + ' collegato.',
});

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 * @returns {{startDeviceLogin: Function, stopPolling: Function, isPolling: Function}}
 */
export function initDeviceModal(ctx) {
  const { api, utils, actions } = ctx;
  const { byId, icon, clear, openModal, closeModal, copyToClipboard, showToast, safeHttpUrl } = utils;

  // Active polling session: {provider, onConnected, expiresAt, timer} or null.
  let session = null;
  // True while POST /connect is in flight (ignore double clicks).
  let starting = false;

  /* ── DOM helpers ───────────────────────────────────────────── */

  function setText(id, text) {
    const node = byId(id);
    if (node) node.textContent = text;
  }

  function setStatus(kind, message) {
    const node = byId('deviceStatus');
    if (!node) return;
    clear(node);
    node.classList.toggle('success', kind === 'success');
    node.classList.toggle('error', kind === 'error');
    const lead = kind === 'success' ? icon('check', 14) : kind === 'error' ? icon('alert', 14) : icon('spinner');
    node.insertAdjacentHTML('beforeend', lead); // trusted icon markup
    node.appendChild(document.createTextNode(' ' + message));
  }

  function fillModal(provider, device) {
    setText('deviceTitle', PROVIDERS[provider].title);
    setText('deviceHint', device.message || MSG.defaultHint);
    const code = byId('deviceCode');
    if (code) { code.textContent = device.user_code; code.classList.remove('copied'); }
    const open = byId('deviceOpenBtn');
    if (open) open.href = safeHttpUrl(device.verification_uri) || '#';
    setStatus('pending', MSG.waiting);
  }

  /* ── Polling ───────────────────────────────────────────────── */

  function stopPolling() {
    if (session && session.timer) clearInterval(session.timer);
    session = null;
  }

  function finish(current, kind, message) {
    if (session === current) stopPolling();
    setStatus(kind, message);
  }

  async function handleConnected(current) {
    finish(current, 'success', MSG.connected);
    showToast(MSG.connectedToast(PROVIDERS[current.provider].name), 'success');
    setTimeout(() => closeModal(MODAL_ID), CLOSE_DELAY_MS);
    await Promise.all([actions.refreshIntegrations(), actions.refreshConfig()])
      .catch((err) => console.warn('[device] refresh after connect failed', err));
    if (!current.onConnected) return;
    try {
      current.onConnected();
    } catch (err) {
      console.error('[device] onConnected callback failed', err);
    }
  }

  async function poll(current) {
    if (session !== current) return;
    if (Date.now() >= current.expiresAt) { finish(current, 'error', MSG.expired); return; }
    let status;
    try {
      status = await api.providers[current.provider].status();
    } catch (err) {
      console.warn('[device] status poll failed (will retry)', err);
      return;
    }
    if (session !== current) return; // cancelled while the request was in flight
    if (status && status.connected) { await handleConnected(current); return; }
    if (status && status.error) finish(current, 'error', String(status.error));
  }

  function beginPolling(provider, device, onConnected) {
    stopPolling(); // never leave a previous interval running
    const expiresIn = Number(device.expires_in) > 0 ? Number(device.expires_in) : DEFAULT_EXPIRES_S;
    const current = Object.freeze({
      provider,
      onConnected: typeof onConnected === 'function' ? onConnected : null,
      expiresAt: Date.now() + expiresIn * 1000,
      timer: setInterval(() => poll(current), POLL_INTERVAL_MS),
    });
    session = current;
  }

  /* ── Start ─────────────────────────────────────────────────── */

  function reportStartError(err) {
    console.error('[device] connect failed', err);
    if (err instanceof api.ApiError && err.status === 400) {
      actions.openSettings('integrations');
      showToast(MSG.notConfigured, 'warning');
      return;
    }
    showToast(api.errorMessage(err, MSG.startFailed), 'error');
  }

  /**
   * Start the device flow for a provider and drive the modal until the
   * account is connected, an error occurs or the code expires.
   * @param {'github'|'microsoft'} provider
   * @param {() => void} [onConnected]
   */
  async function startDeviceLogin(provider, onConnected) {
    if (!PROVIDERS[provider] || !api.providers[provider]) {
      console.error('[device] unknown provider', provider);
      showToast(MSG.unknownProvider, 'error');
      return;
    }
    if (starting) return; // a connect request is already in flight
    starting = true;
    stopPolling();
    let device;
    try {
      const res = await api.providers[provider].connect();
      device = res && res.device;
      if (!device || !device.user_code) throw new api.ApiError(MSG.invalidDevice, 502, res || {});
    } catch (err) {
      reportStartError(err);
      return;
    } finally {
      starting = false;
    }
    fillModal(provider, device);
    openModal(MODAL_ID);
    const loginUrl = safeHttpUrl(device.verification_uri);
    if (loginUrl) window.open(loginUrl, '_blank', 'noopener');
    beginPolling(provider, device, onConnected);
  }

  /* ── Copy code ─────────────────────────────────────────────── */

  async function copyCode() {
    const code = byId('deviceCode');
    const text = code ? code.textContent.trim() : '';
    if (!text || text.startsWith('—')) return;
    const ok = await copyToClipboard(text);
    if (!ok) { showToast(MSG.copyFailed, 'error'); return; }
    code.classList.add('copied');
    setTimeout(() => code.classList.remove('copied'), COPIED_MS);
    showToast(MSG.copied, 'success');
  }

  /* ── Wiring ────────────────────────────────────────────────── */

  function bind() {
    const modal = byId(MODAL_ID);
    if (modal) modal.addEventListener(MODAL_CLOSE_EVENT, stopPolling);
    else console.warn('[device] #' + MODAL_ID + ' not found');
    const cancel = byId('deviceCancelBtn');
    if (cancel) cancel.addEventListener('click', stopPolling);
    const code = byId('deviceCode');
    if (code) code.addEventListener('click', copyCode);
  }

  bind();
  return Object.freeze({ startDeviceLogin, stopPolling, isPolling: () => session !== null });
}
