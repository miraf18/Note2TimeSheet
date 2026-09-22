/**
 * Tiny immutable store shared by all frontend modules.
 *
 * Usage:
 *   const store = createStore({ entries: [], selectedDate: null });
 *   const unsubscribe = store.subscribe((state, prev, changed) => { ... });
 *   store.set({ entries: [...state.entries, entry] });     // shallow patch
 *   store.update((s) => ({ entries: s.entries.filter(...) })); // fn(state) -> patch
 *
 * State snapshots are shallow-frozen: assigning a top-level key throws in
 * strict mode (ES modules are always strict), which makes accidental mutation
 * visible. Nested objects/arrays are NOT frozen — treat them as read-only and
 * always build new ones (map/filter/Object.assign) before calling set().
 *
 * Subscribers receive (nextState, prevState, changedKeys) where changedKeys is
 * the array of top-level keys whose value changed by strict inequality (!==).
 * A set() whose patch changes nothing does not notify.
 */

/**
 * Compare a patch against the current state and return the list of top-level
 * keys that would actually change.
 * @param {object} state
 * @param {object} patch
 * @returns {string[]}
 */
function diffKeys(state, patch) {
  return Object.keys(patch).filter((key) => state[key] !== patch[key]);
}

/**
 * Create a store.
 * @param {object} initial  initial state (copied, never mutated)
 * @returns {{get: Function, set: Function, update: Function, subscribe: Function}}
 */
export function createStore(initial) {
  if (initial !== null && typeof initial !== 'object') {
    throw new TypeError('createStore(initial): initial must be an object');
  }

  let state = Object.freeze(Object.assign({}, initial || {}));
  const listeners = new Set();

  function notify(prev, changed) {
    listeners.forEach((fn) => {
      try {
        fn(state, prev, changed);
      } catch (err) {
        // A faulty subscriber must never break the others.
        console.error('[store] subscriber error', err);
      }
    });
  }

  /** @returns {object} current (frozen) state snapshot */
  function get() {
    return state;
  }

  /**
   * Shallow-merge a patch into the state and notify subscribers.
   * @param {object} patch  partial state
   * @returns {object} the new state
   */
  function set(patch) {
    if (patch === null || typeof patch !== 'object') {
      throw new TypeError('store.set(patch): patch must be an object');
    }
    const changed = diffKeys(state, patch);
    if (changed.length === 0) return state;
    const prev = state;
    state = Object.freeze(Object.assign({}, state, patch));
    notify(prev, changed);
    return state;
  }

  /**
   * Functional update: fn receives the current state and returns a patch
   * (a partial object; returning the full next state also works).
   * @param {(state: object) => object} fn
   * @returns {object} the new state
   */
  function update(fn) {
    if (typeof fn !== 'function') {
      throw new TypeError('store.update(fn): fn must be a function');
    }
    const patch = fn(state);
    return patch ? set(patch) : state;
  }

  /**
   * Register a listener. Returns an unsubscribe function.
   * @param {(state: object, prev: object, changed: string[]) => void} fn
   * @returns {() => void}
   */
  function subscribe(fn) {
    if (typeof fn !== 'function') {
      throw new TypeError('store.subscribe(fn): fn must be a function');
    }
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  return { get, set, update, subscribe };
}

/** Keys used by the Note2TimeSheet store (see docs/FRONTEND_CONTRACT.md). */
export const STORE_KEYS = Object.freeze([
  'config',
  'entries',
  'elaboration',
  'practices',
  'days',
  'selectedDate',
  'settings',
  'githubStatus',
  'microsoftStatus',
  'theme',
]);

/** Initial state shared by app.js and tests. */
export function initialState() {
  return {
    config: {},
    entries: [],
    elaboration: null,
    practices: [],
    days: [],
    selectedDate: null,
    settings: null,
    githubStatus: null,
    microsoftStatus: null,
    theme: 'dark',
  };
}
