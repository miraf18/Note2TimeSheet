/**
 * Fetch wrapper + typed helpers for every endpoint of the HTTP API
 * (docs/ARCHITECTURE.md §7).
 *
 * All helpers return the parsed JSON body and throw an ApiError on a non-2xx
 * response. Helpers that take a `date` (YYYY-MM-DD) append it as `?date=` or
 * put it in the JSON body exactly as the spec requires; a falsy date means
 * "today" server-side.
 */

const JSON_HEADERS = Object.freeze({ 'Content-Type': 'application/json', Accept: 'application/json' });
const DEFAULT_ERROR = 'Errore di comunicazione con il server.';

/* ── Errors ────────────────────────────────────────────────────── */

/**
 * Error thrown for non-2xx responses (and network failures, status 0).
 * `message` is the server's Italian error text when available,
 * `status` the HTTP status, `data` the parsed body (or {}).
 */
export class ApiError extends Error {
  constructor(message, status, data) {
    super(message || DEFAULT_ERROR);
    this.name = 'ApiError';
    this.status = Number(status) || 0;
    this.data = data && typeof data === 'object' ? data : {};
  }

  /** 401 or body {"auth_required": true} → the provider needs a (re)login. */
  get authRequired() {
    return this.status === 401 || this.data.auth_required === true;
  }

  /** Validation error list (PUT /api/settings returns {"errors": [...]}). */
  get errors() {
    return Array.isArray(this.data.errors) ? this.data.errors : [];
  }
}

/** True when err is an ApiError signalling a required provider login. */
export function isAuthRequired(err) {
  return err instanceof ApiError && err.authRequired;
}

/** Best Italian message for any thrown value. */
export function errorMessage(err, fallback) {
  if (err instanceof ApiError) {
    if (err.errors.length) return err.errors.join(' ');
    return err.message || fallback || DEFAULT_ERROR;
  }
  if (err && err.message && /failed to fetch|networkerror/i.test(err.message)) {
    return 'Server non raggiungibile.';
  }
  return fallback || DEFAULT_ERROR;
}

/* ── URL helpers ───────────────────────────────────────────────── */

/** Append `?date=<date>` (or `&date=`) when date is truthy. */
export function withDate(url, date) {
  if (!date) return url;
  const sep = url.includes('?') ? '&' : '?';
  return url + sep + 'date=' + encodeURIComponent(date);
}

/** Build a query string from an object, skipping null/undefined/'' values. */
export function query(params) {
  const parts = Object.entries(params || {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v));
  return parts.length ? '?' + parts.join('&') : '';
}

function extractMessage(data, status) {
  if (data && typeof data === 'object') {
    if (typeof data.error === 'string' && data.error) return data.error;
    if (Array.isArray(data.errors) && data.errors.length) return data.errors.join(' ');
    if (typeof data.message === 'string' && data.message) return data.message;
  }
  return 'Errore HTTP ' + status;
}

async function parseBody(res) {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (err) {
    console.warn('[api] non-JSON response from', res.url, err);
    return { raw: text };
  }
}

/* ── Core fetch ────────────────────────────────────────────────── */

/**
 * fetch() wrapper.
 * - `opts.body` may be a plain object → JSON.stringify'd automatically.
 * - JSON headers are added by default (override via opts.headers).
 * - Resolves with the parsed body; rejects with ApiError on !res.ok.
 * @param {string} url
 * @param {RequestInit & {body?: any}} [opts]
 */
export async function apiFetch(url, opts) {
  const options = Object.assign({}, opts || {});
  const body = options.body;
  const isPlainBody = body !== undefined && body !== null &&
    typeof body === 'object' && !(body instanceof FormData) && !(body instanceof Blob);
  const init = Object.assign({}, options, {
    headers: Object.assign({}, JSON_HEADERS, options.headers || {}),
    body: isPlainBody ? JSON.stringify(body) : body,
  });

  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    console.error('[api] network error', url, err);
    throw new ApiError('Server non raggiungibile.', 0, { cause: String(err) });
  }

  const data = await parseBody(res);
  if (!res.ok) {
    throw new ApiError(extractMessage(data, res.status), res.status, data);
  }
  return data;
}

const get = (url) => apiFetch(url);
const post = (url, body) => apiFetch(url, { method: 'POST', body: body || {} });
const put = (url, body) => apiFetch(url, { method: 'PUT', body: body || {} });
const del = (url) => apiFetch(url, { method: 'DELETE' });

/* ── §7.1 Config ───────────────────────────────────────────────── */

/** GET /api/config */
export const getConfig = () => get('/api/config');

/* ── §7.2 Days & entries ───────────────────────────────────────── */

/** GET /api/days → {days:[{date, entry_count, elaborated}]} */
export const getDays = () => get('/api/days');

/** GET /api/entries?date= → {date, entries} */
export const getEntries = (date) => get(withDate('/api/entries', date));

/** POST /api/entry {text, date} → {success, entry} */
export const addEntry = (text, date) => post('/api/entry', { text, date });

/** PUT /api/entry/<id> {text, date} → {success, entry} */
export const updateEntry = (id, text, date) =>
  put('/api/entry/' + encodeURIComponent(id), { text, date });

/** DELETE /api/entry/<id>?date= → {success} */
export const deleteEntry = (id, date) =>
  del(withDate('/api/entry/' + encodeURIComponent(id), date));

/* ── §7.3 Elaboration ──────────────────────────────────────────── */

/** POST /api/elaborate {date} → {success, result, elaborated_at} */
export const elaborate = (date) => post('/api/elaborate', { date });

/** GET /api/elaborate?date= → {result, elaborated_at, elaborated_edited_at} */
export const getElaboration = (date) => get(withDate('/api/elaborate', date));

/** PUT /api/elaborate {date, result:{timesheet, note}} → {success, result, elaborated_edited_at} */
export const saveElaboration = (date, result) => put('/api/elaborate', { date, result });

/* ── §7.4 Practices ────────────────────────────────────────────── */

/** GET /api/practices → {practices} */
export const getPractices = () => get('/api/practices');

/** POST /api/practices {code, name, description} → {success, practices} */
export const addPractice = (practice) => post('/api/practices', {
  code: practice.code, name: practice.name, description: practice.description || '',
});

/** PUT /api/practices/<code> {name, description} → {success, practices} */
export const updatePractice = (code, patch) =>
  put('/api/practices/' + encodeURIComponent(code), patch || {});

/** DELETE /api/practices/<code> → {success, practices} */
export const deletePractice = (code) => del('/api/practices/' + encodeURIComponent(code));

/* ── §7.5 Settings ─────────────────────────────────────────────── */

/** GET /api/settings → {settings, ai_defaults, ai_effective, placeholders, openai_key_set, openai_key_source, timezones} */
export const getSettings = () => get('/api/settings');

/** PUT /api/settings <partial patch> → {success, settings} | 400 {success:false, errors:[...]} */
export const updateSettings = (patch) => put('/api/settings', patch || {});

/** POST /api/settings/ai/reset {fields:[...]} (or {} = all) → {success, settings, ai_effective} */
export const resetAiPrompts = (fields) =>
  post('/api/settings/ai/reset', Array.isArray(fields) && fields.length ? { fields } : {});

/** GET /api/settings/ai/preview?date= → {system_prompt, user_prompt, entry_count} */
export const previewPrompt = (date) => get(withDate('/api/settings/ai/preview', date));

/* ── §7.6 GitHub ───────────────────────────────────────────────── */

export const github = Object.freeze({
  /** GET /api/github/status → auth_status() + {repos} */
  status: () => get('/api/github/status'),
  /** POST /api/github/connect → {success, device:{user_code, verification_uri, expires_in, interval}} */
  connect: () => post('/api/github/connect'),
  /** POST /api/github/token {token} → {success, identity} */
  token: (token) => post('/api/github/token', { token }),
  /** POST /api/github/disconnect → {success} */
  disconnect: () => post('/api/github/disconnect'),
  /** GET /api/github/repos[?refresh=1] → {repos, selected} */
  repos: (refresh) => get('/api/github/repos' + query({ refresh: refresh ? 1 : null })),
  /** PUT /api/github/repos {repos:["owner/name"]} → {success, repos} */
  saveRepos: (repos) => put('/api/github/repos', { repos: Array.isArray(repos) ? repos : [] }),
  /** POST /api/github/import {date} → {success, new, skipped, summary, entries} */
  importDay: (date) => post('/api/github/import', { date }),
});

/* ── §7.7 Microsoft ────────────────────────────────────────────── */

export const microsoft = Object.freeze({
  /** GET /api/microsoft/status → auth_status() + {provider, source, outlook_com_available} */
  status: () => get('/api/microsoft/status'),
  /** POST /api/microsoft/connect → {success, device:{user_code, verification_uri, message, expires_in}} */
  connect: () => post('/api/microsoft/connect'),
  /** POST /api/microsoft/disconnect → {success} */
  disconnect: () => post('/api/microsoft/disconnect'),
  /** POST /api/microsoft/import {date} → {success, new, skipped, entries, provider} */
  importDay: (date) => post('/api/microsoft/import', { date }),
});

/** Provider namespaces by name, for generic code (device-code modal). */
export const providers = Object.freeze({ github, microsoft });
