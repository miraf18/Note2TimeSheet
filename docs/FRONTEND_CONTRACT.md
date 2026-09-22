# Frontend contract — Note2TimeSheet v2

Binding contract between the frontend foundation (`templates/index.html`,
`static/css/app.css`, `static/js/{app,store,api,utils}.js`) and the feature
modules (`history.js`, `entries.js`, `timesheet.js`, `settings.js`,
`integrations.js`). It complements `docs/ARCHITECTURE.md` §7 (HTTP API) and §8
(UI behaviour). Where this file and §8 differ, §8 wins for *behaviour*, this
file wins for *names* (ids, classes, exports, events).

Conventions: ES modules, no build step, no framework. Never put untrusted data
into `innerHTML` — build nodes with `utils.el()` / `textContent`, or escape with
`utils.escHtml()`. `innerHTML` is acceptable only for trusted static markup
(e.g. `utils.icon()`). Immutable state updates only (`store.set` with new
arrays/objects). All user-facing strings in Italian.

---

## 1. Module initialisation

`app.js` boots in this order:

1. create store, apply theme, start clock;
2. bind global behaviour (theme toggle, Esc / backdrop / `[data-close-modal]`
   for every `.modal-backdrop`, settings modal shell + tabs);
3. call **every feature init** with the same frozen context object;
4. load initial data (`/api/config`, `/api/days`, `/api/practices`,
   `/api/github/status`, `/api/microsoft/status`), write it into the store,
   then set `selectedDate = config.today` → which triggers `actions.loadDay()`
   (entries + elaboration) through a store subscription.

So a module must **subscribe first and render on store changes** — the data is
not there yet when `init` runs.

```js
// each feature module exports exactly one function, synchronous, idempotent
export function initHistory(ctx)      // history.js
export function initEntries(ctx)      // entries.js
export function initTimesheet(ctx)    // timesheet.js
export function initSettings(ctx)     // settings.js
export function initIntegrations(ctx) // integrations.js

// ctx = Object.freeze({ store, api, utils, actions })
```

| ctx key | What it is |
|---------|------------|
| `store` | the shared store (see §2) |
| `api` | the whole `api.js` module namespace (`api.getEntries`, `api.github.status`, `api.ApiError`, …) |
| `utils` | the whole `utils.js` module namespace |
| `actions` | shell actions implemented in `app.js` (see §3) |

`init` must not throw; `app.js` wraps each init in try/catch and logs, but a
throwing module simply stays dead. Do not `import` `app.js` from a feature
module (circular). Import `./utils.js` / `./api.js` directly if you prefer, they
are the same objects as `ctx.utils` / `ctx.api`.

---

## 2. Store (`store.js`)

```js
createStore(initial) → { get(), set(patch), update(fn), subscribe(fn) }
```

- `get()` — current **shallow-frozen** snapshot (assigning a top-level key
  throws; nested objects are not frozen but must be treated as read-only).
- `set(patch)` — `Object.assign({}, state, patch)`; notifies only if at least
  one key changed (`!==`). Returns the new state.
- `update(fn)` — `fn(state)` returns a patch (partial object); merged via `set`.
- `subscribe(fn)` — `fn(state, prevState, changedKeys)`; returns `unsubscribe`.
  `changedKeys` is the array of top-level keys that changed. React with
  `if (changed.includes('entries')) render(state)`.

### Store keys and shapes

| Key | Type / shape | Written by |
|-----|--------------|------------|
| `config` | `GET /api/config` body: `{app_name, app_version, user_name, daily_hours, timezone, today, openai_configured, openai_model, integrations:{github:{configured,connected,login,repos_count}, microsoft:{configured,connected,account,provider,outlook_com_available}}}` | app.js (`actions.refreshConfig`) |
| `entries` | `Entry[]` of the selected day: `{id, type:'manual'|'meeting'|'github', text, time:'HH:MM', duration_min, source_id, meta}` (meta shapes in ARCHITECTURE §3.4) | app.js `loadDay`; entries.js after add/edit/delete/import |
| `elaboration` | `null` or `{result:{timesheet:[{pratica, ore, descrizione}], totale_ore, note}, elaborated_at, elaborated_edited_at}` | app.js `loadDay`; timesheet.js after elaborate / PUT |
| `practices` | `[{code, name, description}]` | app.js; settings.js after CRUD |
| `days` | `[{date, entry_count, elaborated}]` newest first (today always present server-side) | app.js `refreshDays` |
| `selectedDate` | `'YYYY-MM-DD'` or `null` before config is loaded | history.js via `actions.selectDay(date)` |
| `settings` | `null` until loaded, then the full `GET /api/settings` body `{settings, ai_defaults, ai_effective, placeholders, openai_key_set, openai_key_source, timezones}` | settings.js / integrations.js via `actions.refreshSettings()` |
| `githubStatus` | `null` (unavailable) or `GET /api/github/status` body `{configured, connected, pending, device, error, identity, repos}` | app.js; integrations.js |
| `microsoftStatus` | `null` or `GET /api/microsoft/status` body `{configured, connected, pending, device, error, account, provider, source, outlook_com_available}` | app.js; integrations.js |
| `theme` | `'dark'` \| `'light'` | app.js only (`actions.applyTheme`) |

Day helpers (always use these instead of `new Date()`):
`utils.currentDate(state)` → the day shown (`selectedDate || config.today ||
browser today`), `utils.isToday(state)`, `utils.serverToday(state)`.

---

## 3. Shell actions (`ctx.actions`, implemented in `app.js`)

| Action | Effect |
|--------|--------|
| `loadDay(date?)` → Promise | GET entries + elaboration for `date` (default current day) and `store.set({entries, elaboration})`. Stale responses (day changed meanwhile) are dropped. Rejects on entries error (elaboration errors are logged and become `null`). |
| `refreshDays()` → Promise | GET `/api/days` → `store.set({days})` |
| `refreshConfig()` → Promise | GET `/api/config` → `store.set({config})` + header user badge |
| `refreshPractices()` → Promise | GET `/api/practices` → `store.set({practices})` |
| `refreshSettings()` → Promise | GET `/api/settings` → `store.set({settings})` |
| `refreshIntegrations()` → Promise | GET both statuses → `store.set({githubStatus, microsoftStatus})` (a failing provider becomes `null`) |
| `selectDay(date)` | validates `YYYY-MM-DD`, `store.set({selectedDate})` → triggers `loadDay` |
| `openSettings(tab?)` | shows tab (`'general'|'integrations'|'practices'|'prompt'`) and opens `#settingsModal` |
| `closeSettings()` | closes `#settingsModal` |
| `showTab(tab)` | switches the settings tab without opening the modal |
| `applyTheme('dark'|'light')` | sets `data-theme`, icons, localStorage (`ts-theme`), `store.theme` |

Callers show toasts on rejection: `showToast(api.errorMessage(err, 'Fallback…'), 'error')`.

Who calls what after a mutation:

- entries.js after add/edit/delete/import: update `entries` in the store
  (immutable) **and** `actions.refreshDays()` (entry counts in the selector).
- timesheet.js after `POST /api/elaborate` or `PUT /api/elaborate`: update
  `elaboration`, then `actions.refreshDays()` (elaborated flag).
- settings.js after `PUT /api/settings` (general tab): `actions.refreshConfig()`
  (user_name / daily_hours / today may change) + `store.set({settings})`.
- integrations.js after connect / disconnect / save repos:
  `actions.refreshIntegrations()` + `actions.refreshConfig()`.

---

## 4. DOM events (cross-module)

All on `document` unless noted. Names are exported from `app.js` as `EVENTS`
but modules should just use the string literals below.

| Event | detail | Dispatched by | Handled by |
|-------|--------|---------------|------------|
| `ts:open-settings` | `{tab}` | anyone (e.g. entries.js when an import button is clicked while not connected) | app.js → `actions.openSettings(tab)` |
| `ts:settings-tab` | `{tab}` | app.js after each tab switch | settings.js / integrations.js (lazy load of a pane) |
| `ts:modal-open` (on the `.modal-backdrop` element) | `{tab}` for `#settingsModal`; whatever `openModal(el, extra)` was given | `utils.openModal` | settings.js listens on `#settingsModal` to (re)load `/api/settings` + practices; integrations.js may listen on `#deviceModal` |
| `ts:modal-close` (on the `.modal-backdrop` element) | `{}` | `utils.closeModal` (Esc, backdrop click, `[data-close-modal]`, programmatic) | integrations.js **must** listen on `#deviceModal` to stop polling |
| `ts:device-login` | `{provider:'github'|'microsoft', onConnected?: () => void}` | entries.js when an import returns `auth_required` (401) | integrations.js: starts `POST /api/<provider>/connect`, fills and opens `#deviceModal`, polls status every 3 s, on success toast + `actions.refreshIntegrations()` + `actions.refreshConfig()` + `onConnected()` |

Day changes are **not** an event: subscribe to the store and check
`changed.includes('selectedDate')`.

---

## 5. `utils.js` exports

| Export | Signature / notes |
|--------|-------------------|
| `escHtml(str)` | HTML-escape (`& < > " '`) |
| `fmtTime(val)` | `'HH:MM'` passthrough (zero-padded) or ISO datetime → local `HH:MM` |
| `fmtDateTime(iso)` | → `'16 set 14:32'` |
| `fmtOre(val)` | `8 → '8'`, `7.5 → '7.5'`, `0.25 → '0.25'`, NaN → `'—'` |
| `fmtDuration(min)` | `30 → '30 min'`, `90 → '1 h 30 min'`, `120 → '2 h'` |
| `pluralize(n, sing, plur)` | `'1 voce'` / `'3 voci'` |
| `todayISO(date?)` | browser-local `YYYY-MM-DD` (prefer `serverToday(state)`) |
| `isISODay(val)` | regex check |
| `dayLabel(iso, todayIso?)` | `'Oggi · Mer 16 set'` / `'Lun 15 set'` |
| `serverToday(state)`, `currentDate(state)`, `isToday(state)` | see §2 |
| `debounce(fn, wait)` | trailing debounce; result has `.cancel()` / `.flush()` |
| `qs(sel, root?)`, `qsa(sel, root?)` (→ Array), `byId(id)` | query helpers |
| `el(tag, props?, ...children)` | DOM builder. Props: `class`, `text`, `html` (trusted only), `dataset:{}`, `style:{}`, `on<Event>` handlers, any attribute (`'aria-label'`, `href`, `disabled:true`). Children: strings → text nodes **always** (never markup — pass `iconEl()` elements for icons), Nodes, arrays, null/false skipped |
| `clear(node)` | remove all children |
| `show(node, visible)`, `hide(node)` | toggle `.hidden` |
| `autoResize(textarea, max=120)` | composer/edit textareas |
| `setBusy(btn, busy, label?)` | swaps content for spinner (+label), disables, `aria-busy`; restores original markup on `false` |
| `copyToClipboard(text)` → Promise<boolean> | clipboard with `execCommand` fallback |
| `showToast(msg, type='info', duration?)` | type ∈ `info|success|error|warning`; renders into `#toastContainer` |
| `openModal(elOrId, extra?)`, `closeModal(elOrId)`, `topModal()`, `isModalOpen(elOrId)` | see §4 events; `openModal` focuses the first focusable element |
| `icon(name, size=14, extraClass?)` → SVG string | names: `send github calendar settings sun moon edit trash copy check refresh external-link plus x spinner link unlink` (+ extras `search eye eye-off alert zap clock microsoft`). `'spinner'` returns `<span class="spinner">`. Unknown → `alert` + console warning |
| `iconEl(name, size?, extraClass?)` | same as `icon()` but returns an SVG element |
| `ICON_NAMES`, `DAYS_SHORT`, `MONTHS_SHORT`, `TOAST_TYPES`, `TOAST_DURATION_MS` | constants |

---

## 6. `api.js` exports

```js
apiFetch(url, opts)          // fetch wrapper: JSON headers, opts.body object → JSON,
                             // resolves parsed body, rejects ApiError on !ok / network
class ApiError extends Error { status, data, authRequired (getter), errors (getter) }
isAuthRequired(err)          // ApiError && (status 401 || data.auth_required)
errorMessage(err, fallback)  // server message → validation errors joined → fallback (Italian)
withDate(url, date)          // '/api/x' → '/api/x?date=YYYY-MM-DD' when date truthy
query(params)                // '?a=1&b=2' skipping null/undefined/''
```

Typed helpers (ARCHITECTURE §7). `date` is `'YYYY-MM-DD'`; pass
`utils.currentDate(store.get())`.

| Helper | Request | Response |
|--------|---------|----------|
| `getConfig()` | GET `/api/config` | config object |
| `getDays()` | GET `/api/days` | `{days}` |
| `getEntries(date)` | GET `/api/entries?date=` | `{date, entries}` |
| `addEntry(text, date)` | POST `/api/entry` `{text, date}` | `{success, entry}` |
| `updateEntry(id, text, date)` | PUT `/api/entry/<id>` `{text, date}` | `{success, entry}` |
| `deleteEntry(id, date)` | DELETE `/api/entry/<id>?date=` | `{success}` |
| `elaborate(date)` | POST `/api/elaborate` `{date}` | `{success, result, elaborated_at}` (400 no entries / no key, 401 bad key, 502 upstream) |
| `getElaboration(date)` | GET `/api/elaborate?date=` | `{result, elaborated_at, elaborated_edited_at}` |
| `saveElaboration(date, result)` | PUT `/api/elaborate` `{date, result:{timesheet, note}}` | `{success, result, elaborated_edited_at}` |
| `getPractices()` | GET `/api/practices` | `{practices}` |
| `addPractice({code, name, description})` | POST `/api/practices` | `{success, practices}` |
| `updatePractice(code, {name, description})` | PUT `/api/practices/<code>` | `{success, practices}` |
| `deletePractice(code)` | DELETE `/api/practices/<code>` | `{success, practices}` |
| `getSettings()` | GET `/api/settings` | `{settings, ai_defaults, ai_effective, placeholders, openai_key_set, openai_key_source, timezones}` |
| `updateSettings(patch)` | PUT `/api/settings` partial patch (may include `openai_api_key`; `""` clears) | `{success, settings}`; 400 → `ApiError.errors` |
| `resetAiPrompts(fields?)` | POST `/api/settings/ai/reset` `{fields}` or `{}` = all | `{success, settings, ai_effective}` |
| `previewPrompt(date)` | GET `/api/settings/ai/preview?date=` | `{system_prompt, user_prompt, entry_count}` |
| `github.status()` | GET `/api/github/status` | `{configured, connected, pending, device, error, identity, repos}` |
| `github.connect()` | POST `/api/github/connect` | `{success, device:{user_code, verification_uri, expires_in, interval}}`; 400 not configured; 502 |
| `github.token(token)` | POST `/api/github/token` `{token}` | `{success, identity}` |
| `github.disconnect()` | POST `/api/github/disconnect` | `{success}` |
| `github.repos(refresh?)` | GET `/api/github/repos[?refresh=1]` | `{repos:[{full_name, private, pushed_at, default_branch, description}], selected}`; 401 |
| `github.saveRepos(repos)` | PUT `/api/github/repos` `{repos}` | `{success, repos}` |
| `github.importDay(date)` | POST `/api/github/import` `{date}` | `{success, new, skipped, summary:{commits, pull_requests, issues}, entries}`; 401 `auth_required`; 502 |
| `microsoft.status()` | GET `/api/microsoft/status` | `{configured, connected, pending, device, error, account, provider, source, outlook_com_available}` |
| `microsoft.connect()` | POST `/api/microsoft/connect` | `{success, device:{user_code, verification_uri, message, expires_in}}` |
| `microsoft.disconnect()` | POST `/api/microsoft/disconnect` | `{success}` |
| `microsoft.importDay(date)` | POST `/api/microsoft/import` `{date}` | `{success, new, skipped, entries, provider}`; 401 `auth_required`; 400 no provider |
| `providers` | `{github, microsoft}` — for generic code keyed by provider name |

Error handling pattern:

```js
try { … } catch (err) {
  if (api.isAuthRequired(err)) { document.dispatchEvent(new CustomEvent('ts:device-login', { detail: { provider: 'github', onConnected: runImport } })); return; }
  utils.showToast(api.errorMessage(err, 'Impossibile importare.'), 'error');
}
```

---

## 7. DOM map (`templates/index.html`)

Legend: **owner** = module that reads/writes it. Everything not listed is
static. Static `<svg>` icons inside buttons may be replaced with
`utils.setBusy()` while a request is in flight.

### 7.1 Header (`app.js` unless noted)

| id | Element | Purpose / owner |
|----|---------|-----------------|
| `daySelect` | `<select class="header-day-select">` | **history.js** fills `<option value="YYYY-MM-DD">` from `days` (`dayLabel(d) + ' · N voci' + (' ✓' | ' •')`), sets `value = currentDate`, on `change` → `actions.selectDay(value)` |
| `pastDayBadge` | `<span class="header-day-badge hidden">storico</span>` | **history.js** shows when `!isToday(state)` |
| `headerTime` | clock `HH:MM:SS` | app.js |
| `userBadge` | initial letter of `config.user_name` | app.js |
| `themeToggle` (+ `iconMoon`, `iconSun`) | theme toggle | app.js |
| `settingsBtn` | opens settings (tab *Generale*) | app.js |

### 7.2 Left panel — entries (`entries.js`)

| id / class | Purpose |
|------------|---------|
| `#entryCount` | `pluralize(n, 'voce', 'voci')` |
| `#entryList` | container; render `.entry-card` nodes **after** `#entryEmptyState` (remove previous `.entry-card`s first, keep the empty state node) |
| `#entryEmptyState` | toggle `.hidden` when `entries.length > 0` |
| `#composerTextarea` | `maxlength=4000`; Enter = send, Shift+Enter = newline; call `utils.autoResize` on input |
| `#sendBtn` | send; use `setBusy` while posting |
| `#githubImportBtn` + `#githubDot` | GitHub import. Dot: `.status-dot.on` when `githubStatus.connected` else `.off`; add `.is-off` to the button when not connected. Not connected → `ts:open-settings {tab:'integrations'}` + toast hint. 401 → `ts:device-login`. Disable when `!isToday`? No — imports are per selected day, keep enabled |
| `#meetingsImportBtn` + `#meetingsDot` | same for Microsoft; connected = `microsoftStatus.connected || microsoftStatus.provider === 'outlook_com'` |

Entry card markup to render (classes are styled in `app.css`):

```html
<div class="entry-card entry-manual|entry-meeting|entry-github" data-id="…">
  <div class="entry-card-top">
    <span class="badge badge-activity">Attività</span>          <!-- manual -->
    <span class="badge badge-meeting">Riunione</span>
    <span class="entry-meeting-time">09:00–09:30 · 30 min</span> <!-- meeting: meta.start/end + duration_min -->
    <span class="badge badge-github">GitHub</span>
    <a class="entry-repo" href="meta.url" target="_blank" rel="noopener noreferrer">owner/name ↗</a>  <!-- github -->
    <span class="entry-duration">30 min</span>                   <!-- optional, fmtDuration -->
    <span class="entry-time">09:15</span>                        <!-- fmtTime(entry.time), always last -->
  </div>
  <div class="entry-text">…</div>            <!-- textContent; manual/meeting text. For github commits: first line only -->
  <ul class="entry-commits"><li><a href="commit.url" target="_blank" rel="noopener noreferrer">message</a></li>…
      <li class="entry-commits-more">… e altri N</li></ul>       <!-- github kind=commits (meta.commits) -->
  <div class="entry-actions">
    <button class="entry-btn" aria-label="Modifica">icon('edit',13)</button>
    <button class="entry-btn" aria-label="Elimina">icon('trash',13)</button>  <!-- add .danger on first click; second click within 2.5 s deletes -->
  </div>
</div>
```

Inline edit: replace `.entry-text` with `<textarea class="entry-edit-input">`
(Enter commits, Esc cancels, blur commits) → `api.updateEntry`.

### 7.3 Right panel — timesheet (`timesheet.js`)

| id | Purpose |
|----|---------|
| `#elabMeta` | `'N righe'` or `''` |
| `#elabEmpty` / `#elaborateBtn` | empty state + CTA (`setBusy(btn, true, 'Elaborazione in corso...')`) |
| `#elabResult` | result wrapper (toggle with `#elabEmpty`) |
| `#elabUpdatedAt` | `'Ultimo aggiornamento HH:MM'`; append `<span class="edited"> · modificato HH:MM</span>` when `elaborated_edited_at` |
| `#copyJsonBtn` | copies `{data: currentDate, utente: config.user_name, timesheet:[{pratica, ore, descrizione}], totale_ore}`; swap label to `icon('check') Copiato!` + `.btn-success` for 2.2 s |
| `#reElaborateBtn` | re-run elaboration |
| `#tsTable` / `#tsBody` / `#tsTotal` | rows: `<tr><td><div class="practice-badge"><span class="practice-code [unknown]">code</span><span class="practice-name-label">name</span></div></td><td class="editable">ore</td><td class="desc-td"><div class="desc-cell"><span class="desc-text">…</span><button class="desc-edit-btn">icon('edit',12)</button></div></td></tr>`; inline inputs use `class="inline-input"`; add `.saving` to the `<tr>` while persisting. `#tsTotal` gets `.total-ok` when `|total − config.daily_hours| < 0.001` else `.total-ko` |
| `#elabNote` | `result.note` (hide when empty) |

Persist edits with `api.saveElaboration(date, {timesheet, note})` debounced
(`utils.debounce(fn, 600)`), toast on error, store updated immutably.

### 7.4 Settings modal (shell in `app.js`; content in `settings.js` / `integrations.js`)

Shell: `#settingsModal` (`.modal-backdrop`, `role=dialog`), `#settingsTitle`,
`#settingsCloseBtn` (`data-close-modal`), tab bar `#settingsTabs` with
`#tabGeneral #tabIntegrations #tabPractices #tabPrompt` (`.tab-btn`,
`data-tab`, `.active`, arrow-key navigation handled by app.js) and panes
`#paneGeneral #paneIntegrations #panePractices #panePrompt` (`.tab-pane`,
`data-pane`; hidden ones have `.hidden`). Modules never toggle tabs/panes
themselves — use `actions.showTab()` if really needed.

**Load strategy (settings.js):** on `ts:modal-open` of `#settingsModal` call
`actions.refreshSettings()` (and `actions.refreshPractices()`), then render all
panes from `store.settings` on `changed.includes('settings')`.

#### Generale (`settings.js`)

| id | Field | Maps to |
|----|-------|---------|
| `setUserName` | text | `general.user_name` |
| `setDailyHours` | number step 0.25 | `general.daily_hours` |
| `setTimezone` | `<select>` — fill options from `settings.timezones` (+ current value if missing) | `general.timezone` |
| `setHistoryDays` | number 1..365 | `general.history_keep_days` |
| `setModel` | text (+ `datalist#modelSuggestions`) | `general.openai_model` |
| `setTheme` | select dark/light — **owned by app.js** (change → `applyTheme`), just leave it alone |
| `setOpenaiKey` | password, write-only; send as top-level `openai_api_key` only when non-empty | secrets |
| `clearOpenaiKeyBtn` | sends `{openai_api_key: ''}` | secrets |
| `openaiKeyStatus` | `<span class="key-status ok|missing">`: `'configurata ✓ (da impostazioni)'` / `'configurata ✓ (da .env)'` / `'mancante'` from `openai_key_set` + `openai_key_source` | |
| `generalErrors` | `.form-errors` — list `ApiError.errors` from a 400 (hidden otherwise) | |
| `saveGeneralBtn` | `api.updateSettings({general:{…}, openai_api_key?})` → `store.set({settings})`, `actions.refreshConfig()`, toast | |

#### Integrazioni (`integrations.js`)

GitHub card `#ghCard`:

| id | Purpose |
|----|---------|
| `ghStatus` | `.status-pill` + one of `.connected` / `.pending` / `.error` / `.unconfigured`; text e.g. `Connesso`, `Non connesso`, `Non configurato`, `In attesa…` |
| `ghIdentity` (+ `ghAvatar` img, `ghLogin` link → `identity.html_url`, `ghMethod` `'via device flow'` / `'via token personale'`) | shown when connected |
| `ghError` | `.card-error` shows `status.error` |
| `ghConnectBtn` | device flow → dispatch/handle `ts:device-login {provider:'github'}`; hide when connected; disable + hint when `!configured` |
| `ghDisconnectBtn` | shown when connected → `api.github.disconnect()` |
| `ghTogglePat` (`aria-expanded`) → `ghPatBox` (`.pat-box`), `ghPatInput`, `ghPatBtn` | PAT flow → `api.github.token(value)`; clear input after |
| `ghClientDetails` (`<details>`) | set `.open = !configured`; contains `ghClientId` + `ghSaveClientBtn` → `api.updateSettings({github:{client_id}})` |
| `ghRepoPicker`: `ghRepoSearch` (filter, debounce 150 ms), `ghRepoRefreshBtn` (`api.github.repos(true)`), `ghRepoList` (render `.repo-item`s, keep/replace `#ghRepoEmpty`), `ghRepoCount` (`'N selezionati'` / `'Tutti i repository'` when 0), `ghIncCommits` / `ghIncPRs` / `ghIncIssues` (checkboxes ↔ `github.include_*`), `ghSaveReposBtn` (`api.github.saveRepos(selected)` + `api.updateSettings({github:{include_commits, include_pull_requests, include_issues}})`) | |

Repo item markup:

```html
<label class="repo-item [selected]">
  <input type="checkbox" value="owner/name">
  <span class="repo-info"><span class="repo-name">owner/name</span><span class="repo-desc">description</span></span>
  <span class="repo-private">privato</span>          <!-- when private -->
  <span class="repo-pushed">16 set</span>            <!-- fmtDateTime(pushed_at) or just day -->
</label>
```

Microsoft card `#msCard`:

| id | Purpose |
|----|---------|
| `msStatus` | `.status-pill` as above |
| `msIdentity` (+ `msAvatar` letter, `msAccountName`, `msAccount`) | from `status.account {username, name}` |
| `msError` | `.card-error` |
| `msConnectBtn` / `msDisconnectBtn` | device flow (`ts:device-login {provider:'microsoft'}`) / `api.microsoft.disconnect()` |
| `msClientDetails` (`<details>`) → `msClientId`, `msTenantId`, `msSaveClientBtn` | `api.updateSettings({microsoft:{client_id, tenant_id}})`; open when `!configured` |
| `msSource` (`<select>` auto/graph/outlook_com), option `msSourceOutlook` | disable the Outlook option when `!outlook_com_available` and write the hint in `msSourceHint`; on `change` → `api.updateSettings({microsoft:{source}})` + `actions.refreshIntegrations()` |

#### Pratiche (`settings.js`, v1 behaviour)

`#practicesList` — render `.practice-row[data-code]` (`.practice-row-top` >
`.practice-row-code`, `.practice-row-info` > `.practice-row-name` +
`.practice-row-desc`, `.practice-row-actions` > two `.entry-btn`); inline edit
appends `.practice-edit-form` (two inputs + `.btn.btn-primary.btn-sm`).
Add form: `#newCode`, `#newName`, `#newDesc`, `#addPracticeBtn` (Enter in any
input adds). After each CRUD `store.set({practices})` from the response.

#### Prompt AI (`settings.js`)

| id / selector | Purpose |
|---------------|---------|
| `#promptPlaceholders` (`.chips`) | render `<button class="chip" type="button">{{name}}</button>` for each of `settings.placeholders`; click inserts at the caret of the last focused `.prompt-textarea` (track `focusin`); add `.missing` to `{{practices}}` / `{{entries}}` chips when absent |
| `#promptWarning` (`.warning-box`) | soft warning when `{{practices}}` missing from the system sections or `{{entries}}` missing from the user template |
| `.prompt-section[data-field]` ×4 | fields `system_intro`, `system_rules`, `system_output`, `user_template` |
| textareas `#promptIntro`, `#promptRules`, `#promptOutput`, `#promptUser` (`.prompt-textarea[data-field]`) | value = `ai_effective[field]`; add `.modified` + show the sibling badge when `!== ai_defaults[field]` |
| `.prompt-badge[data-modified-for=<field>]` | "modificato" badge (toggle `.hidden`) |
| `.prompt-reset-btn[data-field]` | sets textarea to `ai_defaults[field]` locally (persist on Salva) — or call `api.resetAiPrompts([field])` immediately; pick one and be consistent (recommended: immediate reset + toast) |
| `#promptPreviewBtn` (`aria-expanded`) → `#promptPreview`, `#previewMeta` (`'N attività'`), `#previewSystem`, `#previewUser` (`<pre>`, use `textContent`) | `api.previewPrompt(currentDate)` — note: preview reflects **saved** prompts |
| `#savePromptBtn` | `api.updateSettings({ai:{system_intro, system_rules, system_output, user_template}})`; send `null` for a field equal to its default |

### 7.5 Device-code modal `#deviceModal` (`integrations.js`)

| id | Purpose |
|----|---------|
| `deviceTitle` | `'Collega GitHub'` / `'Collega account Microsoft'` |
| `deviceHint` | `device.message` (Microsoft) or the default sentence |
| `deviceCode` (`<button class="device-code">`) | `user_code`; click → `copyToClipboard` + `.copied` 1.2 s + toast |
| `deviceOpenBtn` (`<a class="btn btn-primary" target="_blank">`) | `href = device.verification_uri`; also `window.open(uri, '_blank', 'noopener')` once when the modal opens |
| `deviceStatus` (`.device-status`) | `<span class="spinner"></span> In attesa del login…`; add `.success` / `.error` with the message |
| `deviceCancelBtn` (`data-close-modal`) | closes → `ts:modal-close` → **stop polling** |

Polling: every 3 s `api.providers[provider].status()` until `connected`
(success), `error` (show, stop) or `expires_in` elapsed (`'Tempo scaduto.
Riprova.'`). Open with `utils.openModal('deviceModal')`, close with
`utils.closeModal('deviceModal')`.

### 7.6 Toasts

`#toastContainer` — only via `utils.showToast()`.

---

## 8. CSS class reference (`static/css/app.css`)

| Area | Classes |
|------|---------|
| Utilities | `.hidden`, `.sr-only`, `.icon`, `.spinner` (`.dark` variant for light buttons) |
| Buttons | `.btn` + `.btn-primary` / `.btn-secondary` / `.btn-danger` / `.btn-ghost` / `.btn-icon` / `.btn-sm` / `.btn-success` / `.btn-elaborate`; `.icon-btn` (header/modal close); `.link-btn`; `.btn-row` |
| Entry cards | `.entry-card` (+ `.entry-manual` / `.entry-meeting` / `.entry-github`), `.entry-card-top`, `.badge` + `.badge-activity` / `.badge-meeting` / `.badge-github` (`.badge-outlook` alias), `.entry-time`, `.entry-duration`, `.entry-meeting-time`, `.entry-repo`, `.entry-text`, `.entry-commits` (+ `li`, `.entry-commits-more`), `.entry-actions`, `.entry-btn` (+ `.danger`), `.entry-edit-input`, `.empty-state` |
| Composer | `.composer`, `.composer-inner`, `.composer-textarea`, `.composer-footer`, `.composer-hint`, `.composer-btns`, `.import-btn` (+ `.github-btn` / `.meetings-btn` / `.is-off`), `.status-dot` (+ `.on` / `.off` / `.warn` / `.error`) |
| Timesheet | `.elab-empty`, `.elab-empty-icon`, `.elab-result`, `.elab-header-row`, `.elab-updated` (+ `.edited`), `.elab-header-actions`, `.elab-table-wrap`, `.ts-table`, `td.editable`, `td.desc-td`, `.desc-cell`, `.desc-text` (+ `.copied`), `.desc-edit-btn`, `.inline-input`, `.practice-badge`, `.practice-code` (+ `.unknown`), `.practice-name-label`, `tr.saving`, `.ts-total-label`, `.total-ok` / `.total-ko`, `.elab-note` |
| Modals | `.modal-backdrop`, `.modal` (+ `.modal-lg` / `.modal-sm`), `.modal-head`, `.modal-title`, `.modal-body`, `.modal-footer`, `.modal-footer-label` |
| Settings | `.tab-bar`, `.tab-btn` (+ `.active`), `.tab-pane`, `.settings-body`, `.settings-grid`, `.pane-footer`, `.section-head`, `.section-title`, `.section-meta`, `.settings-details` |
| Forms | `.form-field` (label + input/select/textarea), `.input-row`, `.form-help`, `.form-errors`, `.key-status` (+ `.ok` / `.missing`), `.toggle-row`, `.toggle`, `[aria-invalid="true"]` |
| Integration cards | `.card`, `.integration-card`, `.card-head`, `.card-title-row`, `.card-icon` (+ `.card-icon-github` / `.card-icon-microsoft`), `.card-title`, `.card-body`, `.card-error`, `.status-pill` (+ `.connected` / `.pending` / `.error` / `.unconfigured`), `.identity`, `.identity-avatar` (+ `.identity-avatar-letter`), `.identity-text`, `.identity-name`, `.identity-sub`, `.pat-box` |
| Repo picker | `.repo-picker`, `.repo-search`, `.repo-list`, `.repo-empty`, `.repo-item` (+ `.selected`), `.repo-info`, `.repo-name`, `.repo-desc`, `.repo-private`, `.repo-pushed` |
| Practices | `.practices-list`, `.add-practice`, `.practice-form`, `.practice-row`, `.practice-row-top`, `.practice-row-code`, `.practice-row-info`, `.practice-row-name`, `.practice-row-desc`, `.practice-row-actions`, `.practice-edit-form` |
| Prompt editor | `.chips`, `.chip` (+ `.missing`), `.prompt-section`, `.prompt-head`, `.prompt-label`, `.prompt-badge`, `.prompt-reset-btn`, `.prompt-textarea` (+ `.modified`), `.warning-box`, `.prompt-footer`, `.prompt-preview`, `.preview-label`, `.preview-pre` |
| Device modal | `.device-modal`, `.device-body`, `.device-hint`, `.device-code` (+ `.copied`), `.device-status` (+ `.success` / `.error`), `.device-footer` |
| Toasts | `.toast-container`, `.toast` (+ `.info` / `.success` / `.error` / `.warning`, `.fade-out`), `.toast-dot`, `.toast-text` |

Design tokens (CSS variables): `--accent` (indigo), `--meeting` (violet),
`--github` (teal), `--success`, `--error`, `--warning` and their `-soft`
variants; `--bg`, `--bg-panel`, `--bg-card`, `--bg-card-hover`, `--border`,
`--border-hover`, `--text`, `--text-muted`, `--text-secondary`, `--radius`,
`--radius-sm`, `--shadow`, `--transition`. Themes via
`html[data-theme="dark"|"light"]`. Breakpoints: 900 px (single column, always
visible card actions), 500 px (stacked forms).

---

## 9. Decisions made where the spec was silent

- **Store subscriptions instead of a day-change event**: `selectedDate` is a
  store key; `app.js` reacts to it by loading the day. Modules react to
  `changed.includes(...)`.
- **`actions`** is a fourth key on the init context (additive to
  `{store, api, utils}`) so modules never import `app.js`.
- **Settings modal shell (open/close/tabs) lives in app.js** so the gear button
  works even before `settings.js` is implemented; panes are filled by
  `settings.js` / `integrations.js`.
- **Generic modal plumbing**: Esc closes the topmost visible `.modal-backdrop`,
  backdrop click and `[data-close-modal]` close their modal; both paths emit
  `ts:modal-close` so modules can clean up (device-code polling).
- **Theme select `#setTheme`** in the *Generale* tab is wired by `app.js`
  (localStorage only; not persisted server-side).
- **Extra static ids** beyond the spec (all optional to use): `githubDot`,
  `meetingsDot`, `settingsCloseBtn`, `clearOpenaiKeyBtn`, `generalErrors`,
  `modelSuggestions`, `ghIdentity`, `ghAvatar`, `ghLogin`, `ghMethod`,
  `ghError`, `ghClientDetails`, `ghRepoPicker`, `ghRepoEmpty`, `msIdentity`,
  `msAvatar`, `msAccountName`, `msAccount`, `msError`, `msClientDetails`,
  `msSourceOutlook`, `msSourceHint`, `deviceHint`, `previewMeta`.
- `#deviceOpenBtn` is an `<a target="_blank">` styled as a button so the
  browser handles the popup natively (module sets `href`).
- `window.__ts = {store, actions, api, utils}` is exposed for debugging.
