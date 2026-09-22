# Note2TimeSheet v2 — Architecture & Contracts

This document is the single source of truth for the v2 rewrite. Every module,
route and frontend component MUST follow the contracts below so that pieces
built independently fit together. UI strings are in **Italian**; code,
identifiers and comments are in **English**.

---

## 1. Goals

Core mechanism stays the same: during the day the user types free-text notes
("entries"); at the end the AI (OpenAI) turns them into a structured timesheet
classified by *pratica* code, balanced to a configurable daily total (default
8.00h). New in v2:

| Area | v2 behaviour |
|------|--------------|
| **GitHub** | User connects a GitHub account (OAuth *device flow*, or pastes a Personal Access Token). Picks repositories to monitor. One click imports the day's commits / pull requests as entries. Settings panel to change repos, disconnect, reconnect. |
| **Microsoft** | User connects a Microsoft account (MSAL *device code flow*, delegated `Calendars.Read`). One click imports the day's meetings as entries. Disconnect / reconnect from settings. Local Outlook COM remains an optional fallback when running natively on Windows. |
| **Settings panel** | Tabs: *Generale*, *Integrazioni*, *Pratiche*, *Prompt AI*. Everything editable from the UI and persisted in `settings.json` (no restart needed). |
| **Prompt AI** | The system/user prompts are split in editable sections with `{{placeholders}}`, with "Ripristina default" and a live preview. |
| **History** | Daily history already exists (day selector). Keep it. Persist manual edits to the elaborated table (new `PUT /api/elaborate`). |
| **Launch** | `start_timesheet.bat` (creates venv, installs deps, runs) **or** Docker (`docker compose up -d --build` → container `timesheet-app-v2` on port **5600**, coexisting with the old `timesheet-app` on 5599). |
| **Removed** | Everything n8n / webhook / callback related (`n8n/`, `n8n_meetings_service.py`, `webhook_service.py`, `/api/callback*`, `ENABLE_WEBHOOK`, `WEBHOOK_URL`, `N8N_*`). |

Non-goals: multi-user, database, authentication of the web UI itself (local
single-user tool).

---

## 2. Repository layout (target)

```
Note2TimeSheet/
├── app.py                      # create_app() factory + __main__ (banner, browser auto-open)
├── config.py                   # paths, env defaults, constants, timezone helper
├── requirements.txt            # runtime deps
├── requirements-dev.txt        # pytest, etc.
├── start_timesheet.bat         # Windows launcher (venv + deps + run)
├── Dockerfile
├── docker-compose.yml          # service timesheet-v2 → container timesheet-app-v2, port 5600
├── .env.example
├── .dockerignore / .gitignore
├── README.md
├── practices.json              # default practices, copied to DATA_DIR on first run
├── docs/ARCHITECTURE.md        # this file
├── routes/
│   ├── __init__.py             # register_blueprints(app)
│   ├── ui.py                   # GET /
│   ├── entries.py              # /api/days, /api/entries, /api/entry*
│   ├── elaborate.py            # /api/elaborate (GET/POST/PUT), /api/config
│   ├── practices.py            # /api/practices*
│   ├── settings.py             # /api/settings*, /api/settings/ai/preview
│   ├── github.py               # /api/github/*
│   └── microsoft.py            # /api/microsoft/*
├── services/
│   ├── __init__.py             # empty
│   ├── settings_service.py     # settings.json (non-secret) + secrets.json (secret) load/save
│   ├── practices_service.py    # practices.json CRUD
│   ├── state_service.py        # daily history, entries, imported source ids, elaboration
│   ├── ai_service.py           # prompt templates, OpenAI call, balancing
│   ├── github_service.py       # device flow, PAT, repos, activity fetch, entry drafts
│   ├── graph_service.py        # MSAL device flow, calendar fetch, disconnect
│   └── outlook_service.py      # Windows COM fallback
├── static/
│   ├── css/app.css
│   └── js/
│       ├── app.js              # entry point (ES module): init, wiring
│       ├── api.js              # fetch wrapper + typed API helpers
│       ├── utils.js            # escHtml, formatters, dates, toasts, icons
│       ├── entries.js          # left panel: entry list, composer, edit/delete
│       ├── timesheet.js        # right panel: elaborate, table, inline edit, copy JSON
│       ├── settings.js         # settings modal (tabs), general/practices/prompt tabs
│       ├── integrations.js     # GitHub & Microsoft cards, device-code modal, repo picker
│       └── history.js          # day selector
├── templates/index.html        # markup only (no inline CSS/JS except tiny bootstrap)
└── tests/
    ├── conftest.py             # tmp DATA_DIR fixture, app fixture
    ├── test_state_service.py
    ├── test_settings_service.py
    ├── test_practices_service.py
    ├── test_ai_service.py
    ├── test_github_service.py
    ├── test_graph_service.py
    ├── test_routes_entries.py
    ├── test_routes_settings.py
    ├── test_routes_github.py
    └── test_routes_microsoft.py
```

Rules: files ≤ 400 lines preferred (800 hard max); functions < 50 lines;
immutable-style updates (build new dicts/lists, don't mutate inputs); validate
all input at route boundaries; never swallow errors silently (log with
`app.logger` / `logging`).

---

## 3. Configuration & data directory

### 3.1 `config.py`

**Paths are functions, not import-time constants** (so tests can point
`TIMESHEET_DATA_DIR` at a temp dir with `monkeypatch.setenv` without reloading
modules). Services must call these functions at use time and never cache the
result at module level.

```python
BASE_DIR        = <repo root>                      # constant is fine (never changes)
APP_NAME        = "Note2TimeSheet"
APP_VERSION     = "2.0.0"
DEFAULT_PORT    = 5600
DEFAULT_TIMEZONE = "Europe/Rome"
DEFAULT_MODEL   = "gpt-4.1-mini"

def app_port() -> int                  # env APP_PORT, default DEFAULT_PORT
def app_host() -> str                  # env APP_HOST, default 127.0.0.1 (Docker sets 0.0.0.0)
def auto_open_browser() -> bool        # env AUTO_OPEN_BROWSER, default True
def data_dir() -> str                  # env TIMESHEET_DATA_DIR, default BASE_DIR/data; os.makedirs(exist_ok=True)
def settings_file() -> str             # data_dir()/settings.json
def secrets_file() -> str              # data_dir()/secrets.json
def practices_file() -> str            # data_dir()/practices.json
def default_practices_file() -> str    # BASE_DIR/practices.json
def history_dir() -> str               # data_dir()/.timesheet_history  (created)
def graph_cache_file() -> str          # data_dir()/.graph_token_cache.bin
def legacy_state_file() -> str         # data_dir()/.timesheet_state.json

def local_tz(tz_name=None) -> tzinfo   # ZoneInfo(tz_name or settings timezone) → fallback DEFAULT_TIMEZONE → UTC
def today_iso() -> str                 # date in local_tz
def now_local() -> datetime            # aware datetime in local_tz
def day_bounds_utc(date_iso) -> tuple[str, str]   # ("YYYY-MM-DDTHH:MM:SS", ...) UTC covering the local day
def atomic_write_json(path, data) -> None          # write tmp file in same dir + os.replace
def read_json(path, default) -> Any                # returns default on missing/corrupt (logs a warning on corrupt)
```

`config.py` must NOT import services at module level (avoid cycles).
`local_tz()` reads the timezone name with a tiny local `read_json(settings_file())`
lookup (`general.timezone`), not via `settings_service`.

Test fixture contract (`tests/conftest.py`):

```python
@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMESHEET_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GRAPH_CLIENT_ID", raising=False)
    # reset in-memory caches of services (each service exposes reset_cache() for tests)
    yield tmp_path

@pytest.fixture
def client():
    from app import create_app
    app = create_app(testing=True)
    return app.test_client()
```

Every service that keeps in-memory state (device-flow pending state, repo
cache, settings cache) exposes `reset_cache()` (no-op if nothing cached).

Environment variables (all optional except OpenAI key):

| Var | Purpose |
|-----|---------|
| `APP_PORT` | default 5600 |
| `APP_HOST` | bind address, default `127.0.0.1`; Docker sets `0.0.0.0` |
| `OUTLOOK_ACCOUNT` | Outlook desktop (COM) store to read, default profile when empty |
| `USER_NAME` | seed for `general.user_name` on first run |
| `OPENAI_API_KEY` | OpenAI key (can also be set from UI → stored in `secrets.json`; UI value wins) |
| `OPENAI_MODEL` | seed for `general.openai_model` (default `gpt-4.1-mini`) |
| `AUTO_OPEN_BROWSER` | `true`/`false` |
| `TIMESHEET_DATA_DIR` | data dir (Docker: `/app/data`) |
| `TIMESHEET_TIMEZONE` | seed for `general.timezone` |
| `GITHUB_CLIENT_ID` | seed for `github.client_id` (OAuth App with Device Flow enabled) |
| `GRAPH_CLIENT_ID`, `GRAPH_TENANT_ID` | seeds for `microsoft.client_id` / `microsoft.tenant_id` |
| `HISTORY_KEEP_DAYS` | seed for `general.history_keep_days` (default 30) |

### 3.2 `settings.json` (non-secret) — `settings_service`

```json
{
  "version": 2,
  "general": {
    "user_name": "Raffaele",
    "daily_hours": 8.0,
    "timezone": "Europe/Rome",
    "history_keep_days": 30,
    "openai_model": "gpt-4.1-mini"
  },
  "ai": {
    "system_intro": null,
    "system_rules": null,
    "system_output": null,
    "user_template": null,
    "temperature": 0.2
  },
  "github": {
    "client_id": "",
    "repos": [],
    "include_commits": true,
    "include_pull_requests": true,
    "include_issues": false
  },
  "microsoft": {
    "client_id": "",
    "tenant_id": "common",
    "source": "auto"
  }
}
```

- `ai.*` prompt fields: `null` means "use built-in default" (defaults live in
  `ai_service`). The API always returns the *effective* text plus the defaults.
- `github.repos`: list of `"owner/name"` strings. Empty list = import from all
  repos where the user has activity.
- `microsoft.source`: `"auto"` (Graph if connected, else Outlook COM if
  available), `"graph"`, `"outlook_com"`.

```python
# services/settings_service.py
DEFAULTS: dict                       # the structure above, seeded from env at first save
def load_settings() -> dict          # deep-merged with DEFAULTS; never raises (corrupt file → defaults + warning log)
def save_settings(settings) -> dict  # atomic write (tmp + os.replace); returns saved dict
def update_settings(patch) -> dict   # deep-merge patch into current, validate, save, return new
def get(path, default=None)          # e.g. get("general.daily_hours")
def validate(settings) -> list[str]  # returns list of error strings (empty = ok)

# secrets (separate file, chmod 600 where supported, never returned by API)
def load_secrets() -> dict           # {"openai_api_key": "", "github_token": {...}|None}
def save_secrets(secrets) -> dict
def set_secret(key, value) -> None
def get_secret(key, default=None)
def get_openai_api_key() -> str      # secrets value if set, else env OPENAI_API_KEY
```

Validation rules: `daily_hours` in (0, 24]; `timezone` must be a valid
ZoneInfo key; `history_keep_days` int 1..365; `temperature` 0..2; `repos`
items match `^[\w.-]+/[\w.-]+$`; `microsoft.source` in the allowed set;
`client_id`s are strings ≤ 200 chars.

### 3.3 `practices.json` — `practices_service`

Unchanged shape `[{"code","name","description"}]`. On first run copy
`DEFAULT_PRACTICES_FILE` → `PRACTICES_FILE`.

```python
def load_practices() -> list[dict]
def save_practices(practices) -> None
def add_practice(code, name, description) -> list      # raises ValueError on duplicate/invalid
def update_practice(code, name=None, description=None) -> list  # raises KeyError if missing
def delete_practice(code) -> list                      # raises KeyError if missing
```

### 3.4 Daily history — `state_service`

File per day `HISTORY_DIR/<YYYY-MM-DD>.json`:

```json
{
  "date": "2026-09-16",
  "entries": [
    {
      "id": "uuid4",
      "type": "manual",          // "manual" | "meeting" | "github"
      "text": "Testo libero...",
      "time": "09:15",           // local HH:MM when the entry was created
      "duration_min": null,      // int for meetings, null otherwise
      "source_id": null,         // dedup key for imported entries (see below)
      "meta": {}                 // provider-specific data (see below)
    }
  ],
  "imported_source_ids": [],     // all source ids ever imported for this day (commits shas, meeting ids, ...)
  "elaborated": null,            // {"timesheet":[{"pratica","ore","descrizione"}], "totale_ore", "note"}
  "elaborated_at": null,         // ISO datetime
  "elaborated_edited_at": null   // ISO datetime of last manual edit via PUT /api/elaborate
}
```

Backward compatibility (`_normalize`): legacy `type: "outlook"` → `"meeting"`;
`meeting_id` → `source_id` (prefixed `meeting:` if not already);
`imported_meeting_ids` → `imported_source_ids`; missing `meta` → `{}`.

Meta conventions:

- meeting: `{"provider": "graph"|"outlook_com", "start": "HH:MM", "end": "HH:MM", "is_allday": bool}`
- github commits (one entry per repo per day): `{"kind": "commits", "repo": "owner/name", "commits": [{"sha","message","url"}], "url": "https://github.com/owner/name"}`
- github pull request: `{"kind": "pull_request", "repo", "number", "action": "opened"|"merged"|"closed"|"reviewed", "url"}`
- github issue: `{"kind": "issue", "repo", "number", "action", "url"}`

Source id conventions: `meeting:<graph event id>`, `github:commit:<sha>`,
`github:pr:<owner/name>#<n>:<action>`, `github:issue:<owner/name>#<n>:<action>`.

```python
# services/state_service.py  (all functions take date=None → today; invalid date → today)
def resolve_date(date) -> str
def list_days() -> list[{"date","entry_count","elaborated"}]      # newest first, today always present
def load_state(date=None) -> dict
def save_state(state) -> None                                      # atomic write + prune
def get_entries(date=None) -> list
def add_entry(text, entry_type="manual", duration_min=None, source_id=None, meta=None, date=None) -> entry
def update_entry(entry_id, text=None, meta=None, duration_min=None, date=None) -> entry|None
def remove_entry(entry_id, date=None) -> bool
def find_entry(predicate, date=None) -> entry|None
def is_source_imported(source_id, date=None) -> bool
def mark_sources_imported(source_ids: Iterable[str], date=None) -> None
def set_elaboration(result, date=None) -> None
def update_elaboration(result, date=None) -> dict                  # manual edit; sets elaborated_edited_at
def get_elaboration(date=None) -> {"result","elaborated_at","elaborated_edited_at"}
```

Retention: keep newest `general.history_keep_days` files.

---

## 4. AI — `ai_service`

Prompt = four editable sections joined with blank lines. Placeholders use
double braces and are replaced with plain string replacement (so JSON braces in
the templates are safe):

| Placeholder | Value |
|-------------|-------|
| `{{practices}}` | one line per practice: `- <code> (<name>): <description>` |
| `{{codes}}` | comma-separated codes |
| `{{daily_hours}}` | e.g. `8.00` |
| `{{daily_minutes}}` | e.g. `480` |
| `{{user_name}}` | |
| `{{date}}` | YYYY-MM-DD |
| `{{weekday}}` | Italian weekday name of `{{date}}` (e.g. `martedì`), empty for invalid dates |
| `{{entries}}` | rendered entry lines (see below) |

Default sections (keep the spirit and the mandatory rules of the v1 prompt,
fix typos, add GitHub rule):

- `system_intro`: role + `CODICI PRATICA DISPONIBILI:\n{{practices}}`
- `system_rules`: numbered REGOLE TASSATIVE — total EXACTLY `{{daily_hours}}` h
  (`{{daily_minutes}}` min); classify with one of `{{codes}}`; group similar
  activities; 0.25h increments; Italian professional descriptions starting
  with a capital and ending with a period; meetings have known durations and
  must be described starting with "Riunione:" ; GitHub entries (commits / pull
  requests) are development work — use commit messages to describe what was
  done, grouped by repository/topic, never paste raw hashes; distribute
  remaining time proportionally; compress non-meeting items if over total.
- `system_output`: "FORMATO OUTPUT — rispondi ESCLUSIVAMENTE con JSON valido…"
  with the example `{"timesheet":[{"pratica","ore","descrizione"}],"totale_ore","note"}`.
- `user_template`: `Data: {{date}} ({{weekday}})\nUtente: {{user_name}}\n\nATTIVITÀ DELLA GIORNATA:\n{{entries}}\n\nElabora il timesheet. Il totale DEVE essere esattamente {{daily_hours}} ore.`

Entry rendering (`render_entries(entries) -> str`):

- manual: `- [09:15] testo`
- meeting: `- [09:00–09:30] Riunione: <text> (durata: 30 min) [Riunione]`
- github commits: `- [GitHub owner/repo] 4 commit:` followed by indented lines `    • message` (first line of message, ≤ 120 chars, max 30 commits then `    • … e altri N`)
- github PR/issue: `- [GitHub owner/repo] Pull request #12 aperta: title`

```python
DEFAULT_PROMPTS = {"system_intro": ..., "system_rules": ..., "system_output": ..., "user_template": ...}
PLACEHOLDERS = [...]  # documented list for the UI
def effective_prompts(ai_settings) -> dict            # None → default
def build_prompts(entries, practices, user_name, date, daily_hours, ai_settings) -> (system_prompt, user_prompt)
def render_entries(entries) -> str
def balance_to_total(items, total_hours) -> list      # pure; 0.25 rounding; adjusts largest item(s); never below 0.25
def elaborate_timesheet(entries, practices, user_name, date, daily_hours, ai_settings, model, api_key) -> dict
    # raises ValueError for config/validation problems (missing key, empty entries, unparsable/invalid AI JSON after one retry)
    # re-raises openai errors (routes map them to friendly messages/HTTP codes)
```

Validation of AI output: `timesheet` list non-empty; each item `pratica` str,
`ore` float > 0, `descrizione` str (capitalise first letter, ensure trailing
period); unknown `pratica` codes are kept but flagged in `note`
("Codice X non presente nell'elenco pratiche"). Balance to `daily_hours`.

---

## 5. GitHub — `github_service`

Constants:

```python
GITHUB_API        = "https://api.github.com"
DEVICE_CODE_URL   = "https://github.com/login/device/code"
ACCESS_TOKEN_URL  = "https://github.com/login/oauth/access_token"
OAUTH_SCOPES      = "repo read:user"
AUTH_REQUIRED     = "AUTH_REQUIRED"
API_HEADERS       = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Note2TimeSheet/2.0"}
```

Device flow (OAuth App with *Device Flow* enabled; only `client_id` needed):

1. `POST DEVICE_CODE_URL` form `client_id`, `scope`, header `Accept: application/json`
   → `{device_code, user_code, verification_uri, expires_in, interval}`.
2. Background thread polls `POST ACCESS_TOKEN_URL` with `client_id`,
   `device_code`, `grant_type=urn:ietf:params:oauth:grant-type:device_code`
   every `interval` s (+5 s on `slow_down`). Errors: `authorization_pending`
   (keep polling), `slow_down`, `expired_token`, `access_denied`,
   `incorrect_device_code`, `unsupported_grant_type` / `incorrect_client_credentials`
   (stop with error).
3. On success fetch `GET /user` → store in `secrets.github_token`:
   `{"access_token","token_type","scope","login","name","avatar_url","html_url","auth_method":"device"|"pat","connected_at"}`.

PAT: `connect_with_token(token)` validates with `GET /user` (401 → error
"Token non valido") and stores with `auth_method: "pat"`.

Activity fetch for a day (`fetch_activity(date_iso, repos, login)`):

1. `start_utc, end_utc = config.day_bounds_utc(date_iso)`.
2. **Events**: `GET /users/{login}/events?per_page=100&page=N` for N=1..3
   (max 300 events; API only returns events for the last 90 days). Stop paging
   when the last event's `created_at` < start. Keep events with
   `start <= created_at < end` and (if `repos` non-empty) `repo.name in repos`.
   - `PushEvent` → `payload.commits[]` (`sha`, `message`, `url`) → commits
     (dedupe by sha). Convert API commit url to `https://github.com/{repo}/commit/{sha}`.
   - `PullRequestEvent` → action `opened` / `reopened` / `closed` (→ `merged`
     if `payload.pull_request.merged` else `closed`) with `number`, `title`, `html_url`.
   - `PullRequestReviewEvent` → action `reviewed`.
   - `IssuesEvent` (opened/closed) and `IssueCommentEvent` (`commented`) → issues.
3. **Default-branch commits** for each monitored repo (cap 25 repos):
   `GET /repos/{owner}/{name}/commits?author={login}&since={start}Z&until={end}Z&per_page=100`
   → merge into commits by sha (`commit.message`, `html_url`). 404/409 (empty
   repo) → skip silently; 401 → `AUTH_REQUIRED`; 403 with `X-RateLimit-Remaining: 0`
   → error "Limite API GitHub raggiunto, riprova più tardi".
4. Return `{"commits": [{"sha","repo","message","url","date"}], "pull_requests": [{"repo","number","title","action","url","date"}], "issues": [...]}` sorted by date.

Entry drafts (`build_entry_drafts(activity, github_settings) -> list[dict]`, pure):

- commits grouped per repo → ONE draft `{type:"github", text, duration_min:None, source_ids:[...shas], meta:{kind:"commits", repo, commits:[{sha,message,url}], url}}`
  where `text` = `"owner/repo · N commit\n• msg1\n• msg2 …"` (first line of each
  message, ≤ 120 chars; max 30 lines then `• … e altri N`).
- each PR / issue → one draft with `source_ids:[<one id>]`, `text` =
  `"owner/repo · Pull request #12 aperta: title"` (actions in Italian:
  aperta / riaperta / chiusa / unita (merged) / revisionata; issues: aperta /
  chiusa / commentata).

Import (`routes/github.py` → `import_day(date)` helper in the service or route):

- For a commits draft: `new_shas = [s for s in source_ids if not imported]`; if
  none → skipped++. Else if an entry with `meta.kind=="commits"` and same repo
  exists for that day → merge commits lists (dedupe by sha), regenerate text,
  `update_entry`; else `add_entry`. Then `mark_sources_imported(new_shas)`.
- For PR/issue drafts: skip if imported, else `add_entry` + mark.
- Respect `include_commits` / `include_pull_requests` / `include_issues`.

Repos list: `list_repos(force=False)` → `GET /user/repos?per_page=100&affiliation=owner,collaborator,organization_member&sort=pushed`
(follow `Link: rel="next"` up to 5 pages) → `[{"full_name","private","pushed_at","default_branch","description"}]`,
cached in memory for 10 minutes.

Public API of the module:

```python
def is_configured() -> bool                  # client_id present (settings or env)
def is_connected() -> bool
def get_identity() -> dict|None              # token record without access_token
def start_device_login() -> dict|None        # {user_code, verification_uri, expires_in, interval} or None (not configured / API error → see auth_status().error)
def auth_status() -> dict                    # {"configured","connected","pending","device","error","identity"}
def connect_with_token(token) -> (identity|None, error|None)
def disconnect() -> None
def list_repos(force=False) -> (repos|None, error|None)
def fetch_activity(date_iso, repos, login=None) -> (activity|None, error|None)   # error may be AUTH_REQUIRED
def build_entry_drafts(activity, github_settings) -> list[dict]
def import_day(date_iso) -> dict             # {"new","skipped","summary":{"commits","pull_requests","issues"}} ; raises GitHubAuthError / GitHubError
```

All HTTP via `requests` with `timeout=20`; a single `_session()` with headers.
Tests mock `requests.Session.request` / `requests.get` / `requests.post`.

---

## 6. Microsoft — `graph_service` & `outlook_service`

`graph_service` keeps the v1 MSAL device-code design but reads `client_id` /
`tenant_id` from settings (env as seed) on each call, adds `disconnect()`
and account info, and exposes meeting start/end.

```python
AUTH_REQUIRED = "AUTH_REQUIRED"
SCOPES = ["Calendars.Read"]
def is_configured() -> bool
def is_connected() -> bool                       # silent token acquisition succeeds
def get_account() -> dict|None                   # {"username", "name"} from MSAL account / id token claims
def start_device_login() -> dict|None            # {user_code, verification_uri, message, expires_in}
def auth_status() -> dict                        # {"configured","connected","pending","device","error","account"}
def disconnect() -> None                         # remove accounts from cache + delete cache file
def get_meetings(date_iso) -> (meetings|None, error|None)
    # meeting: {"subject","duration","duration_label","is_allday","meeting_id","start":"HH:MM","end":"HH:MM"}  (local tz)
```

Graph query: `GET /me/calendarView?startDateTime=&endDateTime=&$select=subject,start,end,isAllDay,isCancelled,showAs&$orderby=start/dateTime&$top=100`
with `Prefer: outlook.timezone="<general.timezone>"` so returned times are
local already. Skip cancelled; skip `< 5 min` non-all-day; all-day → duration
= daily minutes.

`outlook_service.get_outlook_meetings(date_iso)` → same meeting shape, filtered
to the requested local day (v1 only did today). `meeting_id` from
`GlobalAppointmentID` / `EntryID` / hash fallback. `is_available()` true only
when `win32com` imports.

Provider resolution (`routes/microsoft.py`):

```
source = settings.microsoft.source
if source == "graph" or (source == "auto" and graph.is_connected()):  provider = "graph"
elif source == "outlook_com" or (source == "auto" and outlook.is_available()): provider = "outlook_com"
else: provider = None
```

Import creates entries `type="meeting"`, `text=subject`, `duration_min`,
`source_id="meeting:<meeting_id>"`, `meta={"provider","start","end","is_allday"}`.
If provider is graph and not connected → `401 {"success": false, "auth_required": true}` (the
frontend then calls `/api/microsoft/connect`).

---

## 7. HTTP API

All JSON. Errors: `{"success": false, "error": "<messaggio in italiano>"}` with
a proper status (400 validation, 401 auth required, 404 not found, 502
upstream, 500 unexpected). Every route that takes a day accepts `?date=` or
body `date` (invalid/missing → today). Responses never include secrets.

### 7.1 UI & config

| Method | Path | Response |
|--------|------|----------|
| GET | `/` | `index.html` |
| GET | `/api/config` | `{"app_name","app_version","user_name","daily_hours","timezone","today","openai_configured":bool,"openai_model","integrations":{"github":{"configured","connected","login","repos_count"},"microsoft":{"configured","connected","account","provider":"graph"|"outlook_com"|null,"outlook_com_available"}}}` |

### 7.2 Days & entries (unchanged semantics)

| Method | Path | Body / Query | Response |
|--------|------|--------------|----------|
| GET | `/api/days` | | `{"days":[{"date","entry_count","elaborated"}]}` |
| GET | `/api/entries?date=` | | `{"date","entries":[...]}` |
| POST | `/api/entry` | `{"text","date"}` | `{"success","entry"}` (400 if empty text, max 4000 chars) |
| PUT | `/api/entry/<id>` | `{"text","date"}` | `{"success","entry"}` (404) |
| DELETE | `/api/entry/<id>?date=` | | `{"success"}` (404) |

### 7.3 Elaboration

| Method | Path | Body | Response |
|--------|------|------|----------|
| POST | `/api/elaborate` | `{"date"}` | `{"success","result","elaborated_at"}`; 400 no entries / missing key; 401 invalid OpenAI key; 502 OpenAI connection/status error |
| GET | `/api/elaborate?date=` | | `{"result","elaborated_at","elaborated_edited_at"}` |
| PUT | `/api/elaborate` | `{"date","result":{"timesheet":[{"pratica","ore","descrizione"}],"note"}}` | validates (ore > 0 numbers, strings), recomputes `totale_ore`, saves → `{"success","result","elaborated_edited_at"}` |

### 7.4 Practices (unchanged)

`GET /api/practices` → `{"practices"}`; `POST /api/practices` `{"code","name","description"}`;
`PUT /api/practices/<code>`; `DELETE /api/practices/<code>`. All return `{"success","practices"}`.

### 7.5 Settings

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/settings` | | `{"settings": <settings.json content>, "ai_defaults": DEFAULT_PROMPTS, "ai_effective": effective_prompts, "placeholders": [...], "openai_key_set": bool, "openai_key_source": "settings"|"env"|null, "timezones": ["Europe/Rome", ...common list]}` |
| PUT | `/api/settings` | partial patch e.g. `{"general":{"daily_hours":7.5},"ai":{"system_rules":"..."},"openai_api_key":"sk-..."}` | validates → `{"success","settings"}` or 400 `{"success":false,"errors":[...]}`. `openai_api_key` (if present) is stored in secrets; empty string clears it. Prompt fields: empty string or null → reset to default. |
| POST | `/api/settings/ai/reset` | `{"fields":["system_rules"]}` or `{}` = all | resets to defaults → `{"success","settings","ai_effective"}` |
| GET | `/api/settings/ai/preview?date=` | | `{"system_prompt","user_prompt","entry_count"}` — the exact prompts that would be sent for that day |

### 7.6 GitHub

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/github/status` | | `auth_status()` + `"repos": settings.github.repos` |
| POST | `/api/github/connect` | | `{"success","device":{...}}`; 400 if not configured (`"Configura il Client ID GitHub nelle impostazioni"`); 502 if GitHub unreachable |
| POST | `/api/github/token` | `{"token"}` | `{"success","identity"}`; 400/401 on invalid |
| POST | `/api/github/disconnect` | | `{"success"}` |
| GET | `/api/github/repos?refresh=1` | | `{"repos":[...], "selected":[...]}`; 401 auth_required if not connected |
| PUT | `/api/github/repos` | `{"repos":["owner/name"]}` | `{"success","repos"}` |
| POST | `/api/github/import` | `{"date"}` | `{"success","new","skipped","summary","entries"}`; 401 `{"success":false,"auth_required":true}`; 502 on API errors |

### 7.7 Microsoft

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/api/microsoft/status` | | `auth_status()` + `"provider"`, `"source"`, `"outlook_com_available"` |
| POST | `/api/microsoft/connect` | | `{"success","device":{...}}`; 400 if not configured; 502 on MSAL error |
| POST | `/api/microsoft/disconnect` | | `{"success"}` |
| POST | `/api/microsoft/import` | `{"date"}` | `{"success","new","skipped","entries","provider"}`; 401 auth_required; 400 no provider available |

---

## 8. Frontend

Single page, ES modules (`<script type="module" src="/static/js/app.js">`),
no framework, no build step. Reuse the v1 visual language (dark/light themes,
Inter, indigo accent, tokens in `:root`) — read the old `templates/index.html`
from git (`git show HEAD:templates/index.html`) for the CSS to port. Avoid
`innerHTML` with untrusted data: build DOM nodes or use `escHtml`.

Layout:

- **Header**: logo + "TimeSheet"; day selector (`history.js`) + "storico"
  badge; clock; user badge; theme toggle; **settings gear** (opens modal).
- **Left panel "Attività"**: entry cards. Badge per type: `Attività`
  (indigo), `Riunione` (violet, shows `HH:MM–HH:MM · 30 min`), `GitHub`
  (green/teal, shows repo name; commit lines rendered as a bullet list; clicking
  the repo opens `meta.url` in a new tab). Edit / delete on hover (delete needs
  double click as in v1). Composer: textarea (Enter = send, Shift+Enter =
  newline) + buttons: **GitHub import** (icon), **Riunioni import** (icon),
  **Invia**. Import buttons show a state dot (connected / not connected);
  when not connected clicking opens the settings modal on the *Integrazioni*
  tab with a toast hint. On 401 `auth_required` open the device-code modal.
- **Right panel "Timesheet"**: empty state + "Elabora" CTA; result with
  "Ultimo aggiornamento", buttons **Copia JSON** (keep! same payload shape as
  v1: `{data, utente, timesheet:[{pratica, ore, descrizione}], totale_ore}`)
  and **Elabora di nuovo**; table Pratica / Ore / Descrizione with inline
  edit (pratica via a select, ore & descrizione) that now **persists** with `PUT /api/elaborate`
  (debounced, toast on error); total coloured green when equal to
  `daily_hours`, red otherwise; note row.
- **Settings modal** with tabs (`settings.js`, `integrations.js`):
  - *Generale*: nome utente, ore giornaliere, fuso orario (select), giorni di
    storico, modello OpenAI, chiave OpenAI (password field, write-only; show
    "configurata ✓ (da impostazioni / da .env)" or "mancante"), tema.
  - *Integrazioni*: two cards.
    - **GitHub**: status (avatar, login, method), "Connetti con GitHub" (device
      flow) → device modal; "Oppure usa un token personale" (collapsible with
      input + "Collega"); "Disconnetti"; Client ID field with inline help
      ("Crea una OAuth App su github.com/settings/developers, abilita *Device
      Flow*, incolla il Client ID"); repository picker: searchable list with
      checkboxes, "Aggiorna elenco", selected count, "Salva repository";
      toggles commit / pull request / issue.
    - **Microsoft**: status (account), "Connetti account Microsoft" → device
      modal; "Disconnetti"; Client ID + Tenant fields with inline help (Azure
      *App registration*, public client flows enabled, delegated
      `Calendars.Read`); source select (Automatico / Account Microsoft /
      Outlook desktop) — Outlook option disabled with hint when not available.
  - *Pratiche*: v1 CRUD (list, inline edit, add form).
  - *Prompt AI*: four textareas (Introduzione, Regole, Formato output, Messaggio
    utente) each with "Ripristina default" and a "modificato" badge when it
    differs from default; placeholder chips (click to insert at cursor); soft
    warning if `{{practices}}` or `{{entries}}` missing; "Anteprima prompt del
    giorno" that calls `/api/settings/ai/preview` and shows both prompts in a
    read-only block; "Salva".
- **Device-code modal** (shared): title per provider, big code (click to
  copy), "Apri pagina di login" button (also opened automatically), spinner
  "In attesa del login…", polls `/api/<provider>/status` every 3 s until
  `connected` or `error` or expiry; on success toast + refresh config + (if
  triggered from an import button) run the import.
- Toasts as in v1. Keyboard: Esc closes modals.

State is kept in a single `state` object per module; updates are immutable
(`Object.assign({}, …)`, `map`/`filter`).

---

## 9. Packaging

### Dockerfile

`python:3.12-slim`, `PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
APP_PORT=5600 APP_HOST=0.0.0.0 AUTO_OPEN_BROWSER=false TIMESHEET_DATA_DIR=/app/data
TZ=Europe/Rome`, install requirements, copy app, `EXPOSE 5600`, `CMD ["python","app.py"]`.
Add a `HEALTHCHECK` hitting `/api/config`.

### docker-compose.yml

```yaml
services:
  timesheet-v2:
    build: .
    image: timesheet-app:v2
    container_name: timesheet-app-v2
    restart: unless-stopped
    env_file: [.env]
    environment:
      APP_PORT: 5600
      APP_HOST: 0.0.0.0
      AUTO_OPEN_BROWSER: "false"
      TIMESHEET_DATA_DIR: /app/data
      TZ: Europe/Rome
    ports: ["5600:5600"]
    volumes: ["${TIMESHEET_HOST_DATA_DIR:-./data}:/app/data"]   # host folder from .env, default ./data
```

### start_timesheet.bat

Robust launcher: `cd /d %~dp0`; find `python` (or `py -3`); create `.venv` if
missing; `pip install -r requirements.txt` (quiet, only if
`.venv\.deps-installed` is older than `requirements.txt`); run `python app.py`;
`pause` on error so the window doesn't vanish. Optional `start_timesheet_min.bat`
that launches minimized.

### requirements

`flask>=3.0`, `python-dotenv>=1.0`, `requests>=2.28`, `openai>=1.0`,
`msal>=1.28`, `tzdata>=2024.1`, `pywin32>=306; platform_system == "Windows"`.
Dev: `pytest>=8`, `pytest-mock` (optional).

---

## 10. Testing

`pytest` from repo root. `tests/conftest.py` sets `TIMESHEET_DATA_DIR` to a
`tmp_path` **before** importing `config`/services (use `monkeypatch.setenv` in a
session fixture + `importlib.reload`, or make config read env lazily via a
`paths()` function — pick one approach and use it consistently). External
HTTP (`requests`, `openai`, `msal`) is always mocked. Target ≥ 80 % coverage of
`services/` and `routes/`.
