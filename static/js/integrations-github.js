/**
 * Integrations — GitHub card (#ghCard) + repository picker
 * (docs/FRONTEND_CONTRACT.md §7.4 "Integrazioni").
 *
 * Status (pill, identity, error, buttons) renders from `store.githubStatus`;
 * Client ID and include_* toggles render from `store.settings.settings.github`.
 * Repositories are loaded lazily when the Integrazioni tab is shown and the
 * account is connected.
 *
 *   initGithubCard(ctx, startDeviceLogin) → { render(state, changed), onTabShown(state) }
 */

const SEARCH_DEBOUNCE_MS = 150;
const REPO_URL_BASE = 'https://github.com/';

const MSG = Object.freeze({
  statusUnavailable: 'Impossibile leggere lo stato di GitHub.',
  connectHint: 'Inserisci prima il Client ID della OAuth App (sezione qui sotto).',
  connected: 'Account GitHub collegato.',
  disconnected: 'Account GitHub disconnesso.',
  disconnectFailed: 'Impossibile disconnettere l\'account GitHub.',
  confirmDisconnect: 'Disconnettere l\'account GitHub? I repository selezionati verranno mantenuti.',
  tokenMissing: 'Inserisci un token personale.',
  tokenInvalid: 'Token non valido.',
  clientSaved: 'Client ID salvato.',
  clientCleared: 'Client ID rimosso.',
  clientSaveFailed: 'Impossibile salvare il Client ID.',
  connectFirst: 'Collega GitHub per vedere i repository. Nessuna selezione = tutti i repository con attività.',
  loading: 'Caricamento repository…',
  noRepos: 'Nessun repository trovato.',
  noMatch: 'Nessun repository corrisponde alla ricerca.',
  reposFailed: 'Impossibile caricare i repository.',
  reposSaved: 'Repository salvati.',
  reposSaveFailed: 'Impossibile salvare i repository.',
  allRepos: 'Tutti i repository',
  viaDevice: 'via device flow',
  viaPat: 'via token personale',
  saving: 'Salvataggio…',
});

const PILLS = Object.freeze({
  unavailable: { cls: 'error', text: 'Stato non disponibile' },
  connected: { cls: 'connected', text: 'Connesso' },
  pending: { cls: 'pending', text: 'In attesa…' },
  unconfigured: { cls: 'unconfigured', text: 'Non configurato' },
  error: { cls: 'error', text: 'Errore' },
  disconnected: { cls: '', text: 'Non connesso' },
});

function pillFor(status) {
  if (!status) return PILLS.unavailable;
  if (status.connected) return PILLS.connected;
  if (status.pending) return PILLS.pending;
  if (!status.configured) return PILLS.unconfigured;
  if (status.error) return PILLS.error;
  return PILLS.disconnected;
}

function githubSettingsOf(payload) {
  return (payload && payload.settings && payload.settings.github) || {};
}

function filterRepos(repos, filter) {
  const needle = String(filter || '').trim().toLowerCase();
  if (!needle) return repos;
  return repos.filter((repo) =>
    String(repo.full_name || '').toLowerCase().includes(needle) ||
    String(repo.description || '').toLowerCase().includes(needle));
}

/**
 * @param {{store: object, api: object, utils: object, actions: object}} ctx
 * @param {(provider: string, onConnected?: Function) => void} startDeviceLogin
 */
export function initGithubCard(ctx, startDeviceLogin) {
  const { store, api, utils, actions } = ctx;
  const { byId, el, clear, show, showToast, setBusy, debounce, pluralize, fmtDateTime, MONTHS_SHORT, safeHttpUrl } = utils;

  let picker = Object.freeze({ repos: [], selected: [], filter: '', loaded: false, loading: false, message: MSG.connectFirst });
  let rendered = {};          // input id → last value/checked written (dirty detection)
  let lastConfigured = null;  // <details>.open is only forced when `configured` changes

  const on = (id, event, handler) => { const node = byId(id); if (node) node.addEventListener(event, handler); };
  const onEnter = (handler) => (event) => { if (event.key === 'Enter') { event.preventDefault(); handler(); } };

  function sync(id, next, prop) {
    const node = byId(id);
    if (!node) return;
    const last = rendered[id];
    const dirty = last !== undefined && node[prop] !== last;
    if (!dirty || next !== last) node[prop] = next;
    rendered = Object.assign({}, rendered, { [id]: next });
  }

  function refreshQuietly(...promises) {
    return Promise.all(promises).catch((err) => console.warn('[github] refresh failed', err));
  }

  /* ── Status rendering ──────────────────────────────────────── */

  function renderPill(status) {
    const pill = byId('ghStatus');
    if (!pill) return;
    const { cls, text } = pillFor(status);
    pill.className = 'status-pill' + (cls ? ' ' + cls : '');
    pill.textContent = text;
  }

  function renderIdentity(status) {
    const identity = status && status.connected ? status.identity : null;
    show(byId('ghIdentity'), !!identity);
    if (!identity) return;
    const avatar = byId('ghAvatar');
    const avatarUrl = safeHttpUrl(identity.avatar_url);
    if (avatar) {
      avatar.src = avatarUrl;
      avatar.alt = identity.login ? 'Avatar di ' + identity.login : '';
      show(avatar, !!avatarUrl);
    }
    const login = byId('ghLogin');
    if (login) {
      login.textContent = identity.login || identity.name || 'account GitHub';
      login.href = safeHttpUrl(identity.html_url) ||
        (identity.login ? REPO_URL_BASE + encodeURIComponent(identity.login) : '#');
    }
    const method = byId('ghMethod');
    if (method) method.textContent = identity.auth_method === 'pat' ? MSG.viaPat : MSG.viaDevice;
  }

  function renderError(status) {
    const box = byId('ghError');
    if (!box) return;
    const error = !status ? MSG.statusUnavailable : (typeof status.error === 'string' ? status.error : '');
    box.textContent = error;
    show(box, !!error);
  }

  function renderButtons(status) {
    const connected = !!(status && status.connected);
    const configured = !!(status && status.configured);
    const connectBtn = byId('ghConnectBtn');
    if (connectBtn) {
      show(connectBtn, !connected);
      connectBtn.disabled = !configured;
      connectBtn.title = configured ? '' : MSG.connectHint;
    }
    show(byId('ghDisconnectBtn'), connected);
    const toggle = byId('ghTogglePat');
    show(toggle, !connected);
    if (connected) {
      show(byId('ghPatBox'), false);
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
    }
  }

  function renderClientDetails(status) {
    const configured = !!(status && status.configured);
    const details = byId('ghClientDetails');
    if (details && configured !== lastConfigured) details.open = !configured;
    lastConfigured = configured;
  }

  function renderStatus(state) {
    const status = state.githubStatus;
    renderPill(status);
    renderIdentity(status);
    renderError(status);
    renderButtons(status);
    renderClientDetails(status);
    const savedRepos = status && Array.isArray(status.repos) ? status.repos : [];
    if (!status || !status.connected) {
      setPicker({ repos: [], selected: savedRepos, loaded: false, loading: false, message: MSG.connectFirst });
    } else if (!picker.loaded && !picker.loading) {
      setPicker({ selected: savedRepos });
    }
  }

  /* ── Settings rendering (client id, include toggles) ───────── */

  function renderSettings(state) {
    if (!state.settings) return;
    const gh = githubSettingsOf(state.settings);
    sync('ghClientId', gh.client_id || '', 'value');
    sync('ghIncCommits', gh.include_commits !== false, 'checked');
    sync('ghIncPRs', gh.include_pull_requests !== false, 'checked');
    sync('ghIncIssues', gh.include_issues === true, 'checked');
  }

  /* ── Repo picker ───────────────────────────────────────────── */

  function setPicker(patch) {
    picker = Object.freeze(Object.assign({}, picker, patch));
    renderRepoList();
  }

  function fmtPushed(iso) {
    const d = new Date(String(iso));
    if (isNaN(d.getTime())) return '';
    const base = d.getDate() + ' ' + MONTHS_SHORT[d.getMonth()];
    return d.getFullYear() === new Date().getFullYear() ? base : base + ' ' + d.getFullYear();
  }

  function buildRepoItem(repo) {
    const name = String(repo.full_name || '');
    const checked = picker.selected.includes(name);
    const checkbox = el('input', { type: 'checkbox', value: name, checked });
    const item = el('label', { class: 'repo-item' + (checked ? ' selected' : '') },
      checkbox,
      el('span', { class: 'repo-info' },
        el('span', { class: 'repo-name', text: name }),
        repo.description ? el('span', { class: 'repo-desc', text: repo.description }) : null),
      repo.private ? el('span', { class: 'repo-private', text: 'privato' }) : null,
      repo.pushed_at ? el('span', { class: 'repo-pushed', text: fmtPushed(repo.pushed_at), title: fmtDateTime(repo.pushed_at) }) : null);
    checkbox.addEventListener('change', () => toggleRepo(name, checkbox.checked, item));
    return item;
  }

  function renderCount() {
    const node = byId('ghRepoCount');
    if (!node) return;
    const n = picker.selected.length;
    node.textContent = n ? pluralize(n, 'selezionato', 'selezionati') : MSG.allRepos;
  }

  function renderRepoList() {
    const list = byId('ghRepoList');
    if (!list) return;
    utils.qsa('.repo-item', list).forEach((node) => node.remove());
    const visible = filterRepos(picker.repos, picker.filter);
    const message = picker.message || (!visible.length && picker.repos.length ? MSG.noMatch : null);
    const empty = byId('ghRepoEmpty');
    if (empty) { empty.textContent = message || ''; show(empty, !!message); }
    if (!message) visible.forEach((repo) => list.appendChild(buildRepoItem(repo)));
    renderCount();
  }

  function toggleRepo(name, on, item) {
    const without = picker.selected.filter((repo) => repo !== name);
    picker = Object.freeze(Object.assign({}, picker, { selected: on ? without.concat(name) : without }));
    item.classList.toggle('selected', on);
    renderCount();
  }

  function handleReposError(err) {
    if (api.isAuthRequired(err)) {
      setPicker({ repos: [], loaded: false, loading: false, message: MSG.connectFirst });
      return;
    }
    console.error('[github] repos failed', err);
    const message = api.errorMessage(err, MSG.reposFailed);
    setPicker({ loading: false, message });
    showToast(message, 'error');
  }

  async function loadRepos(refresh) {
    const btn = byId('ghRepoRefreshBtn');
    const keepSelection = picker.loaded; // a manual refresh must not drop unsaved toggles
    setBusy(btn, true);
    setPicker({ loading: true, message: MSG.loading });
    try {
      const data = await api.github.repos(refresh);
      const repos = Array.isArray(data.repos) ? data.repos : [];
      const selected = keepSelection || !Array.isArray(data.selected) ? picker.selected : data.selected;
      setPicker({ repos, selected, loaded: true, loading: false, message: repos.length ? null : MSG.noRepos });
    } catch (err) {
      handleReposError(err);
    } finally {
      setBusy(btn, false);
    }
  }

  /** Load the repo list once per connection; `force` skips the visibility check. */
  function ensureReposLoaded(state, force) {
    const status = state.githubStatus;
    if (!status || !status.connected || picker.loaded || picker.loading) return;
    if (!force && !isPaneVisible()) return;
    loadRepos(false);
  }

  function isPaneVisible() {
    const pane = byId('paneIntegrations');
    return !!pane && !pane.classList.contains('hidden') && utils.isModalOpen('settingsModal');
  }

  function includeFlags() {
    const checked = (id) => { const node = byId(id); return !!(node && node.checked); };
    return {
      include_commits: checked('ghIncCommits'),
      include_pull_requests: checked('ghIncPRs'),
      include_issues: checked('ghIncIssues'),
    };
  }

  async function saveRepos() {
    const btn = byId('ghSaveReposBtn');
    setBusy(btn, true, MSG.saving);
    try {
      // Sequential: both endpoints write settings.json.
      await api.github.saveRepos(picker.selected);
      await api.updateSettings({ github: includeFlags() });
      rendered = {};
      showToast(MSG.reposSaved, 'success');
      await refreshQuietly(actions.refreshIntegrations(), actions.refreshConfig(), actions.refreshSettings());
    } catch (err) {
      console.error('[github] save repos failed', err);
      showToast(api.errorMessage(err, MSG.reposSaveFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  /* ── Account actions ───────────────────────────────────────── */

  function togglePat() {
    const toggle = byId('ghTogglePat');
    const box = byId('ghPatBox');
    if (!toggle || !box) return;
    const open = box.classList.contains('hidden');
    show(box, open);
    toggle.setAttribute('aria-expanded', String(open));
    const input = byId('ghPatInput');
    if (open && input) input.focus();
  }

  async function connectWithToken() {
    const input = byId('ghPatInput');
    const btn = byId('ghPatBtn');
    const token = input ? input.value.trim() : '';
    if (!token) { showToast(MSG.tokenMissing, 'warning'); if (input) input.focus(); return; }
    setBusy(btn, true);
    try {
      await api.github.token(token);
      if (input) input.value = '';
      showToast(MSG.connected, 'success');
      await refreshQuietly(actions.refreshIntegrations(), actions.refreshConfig());
      ensureReposLoaded(store.get(), true);
    } catch (err) {
      console.error('[github] token connect failed', err);
      showToast(api.errorMessage(err, MSG.tokenInvalid), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  async function disconnect() {
    if (!window.confirm(MSG.confirmDisconnect)) return;
    const btn = byId('ghDisconnectBtn');
    setBusy(btn, true);
    try {
      await api.github.disconnect();
      showToast(MSG.disconnected, 'success');
      await refreshQuietly(actions.refreshIntegrations(), actions.refreshConfig());
    } catch (err) {
      console.error('[github] disconnect failed', err);
      showToast(api.errorMessage(err, MSG.disconnectFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  async function saveClientId() {
    const input = byId('ghClientId');
    const btn = byId('ghSaveClientBtn');
    const clientId = input ? input.value.trim() : '';
    setBusy(btn, true);
    try {
      await api.updateSettings({ github: { client_id: clientId } });
      rendered = {};
      showToast(clientId ? MSG.clientSaved : MSG.clientCleared, 'success');
      await refreshQuietly(actions.refreshSettings(), actions.refreshIntegrations());
    } catch (err) {
      console.error('[github] save client id failed', err);
      showToast(api.errorMessage(err, MSG.clientSaveFailed), 'error');
    } finally {
      setBusy(btn, false);
    }
  }

  /* ── Public API ────────────────────────────────────────────── */

  function render(state, changed) {
    if (changed.includes('githubStatus')) {
      renderStatus(state);
      ensureReposLoaded(state, false);
    }
    if (changed.includes('settings')) renderSettings(state);
  }

  function onTabShown(state) {
    ensureReposLoaded(state, true);
  }

  function bind() {
    on('ghConnectBtn', 'click', () => startDeviceLogin('github', () => ensureReposLoaded(store.get(), true)));
    on('ghDisconnectBtn', 'click', disconnect);
    on('ghTogglePat', 'click', togglePat);
    on('ghPatBtn', 'click', connectWithToken);
    on('ghPatInput', 'keydown', onEnter(connectWithToken));
    on('ghSaveClientBtn', 'click', saveClientId);
    on('ghClientId', 'keydown', onEnter(saveClientId));
    on('ghRepoRefreshBtn', 'click', () => loadRepos(true));
    on('ghSaveReposBtn', 'click', saveRepos);
    const search = byId('ghRepoSearch');
    if (search) search.addEventListener('input', debounce(() => setPicker({ filter: search.value }), SEARCH_DEBOUNCE_MS));
  }

  bind();
  renderRepoList();
  return Object.freeze({ render, onTabShown });
}
