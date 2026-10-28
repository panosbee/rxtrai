/**
 * RxTrail — API client (fetch wrapper + JWT + auto-logout on 401).
 * Vanilla JS, no dependencies.
 */

// Auto-detect API base: local dev or production domain
const API_BASE = (() => {
  const host = window.location.hostname;
  if (host === '127.0.0.1' || host === 'localhost') {
    return `http://${host}:8000`;
  }
  return window.location.origin;
})();

let _token = localStorage.getItem('rxtrail_token') || '';

// ── Offline queue: ενέργειες που δεν στάλθηκαν (χωρίς σήμα) ─────────────
const OFFLINE_QUEUE_KEY = 'rxtrail_offline_queue';
const STALE_CACHE_KEY = 'rxtrail_stale_cache';
const STALE_TTL_MS = 1000 * 60 * 60 * 24 * 7; // 7 μέρες
const STALE_PREFIXES = ['/cases', '/queue', '/doctors', '/structures', '/skus', '/sources', '/notifications', '/week', '/today', '/dashboard'];

function _getQueue() {
  try { return JSON.parse(localStorage.getItem(OFFLINE_QUEUE_KEY) || '[]'); } catch { return []; }
}
function _setQueue(q) {
  localStorage.setItem(OFFLINE_QUEUE_KEY, JSON.stringify(q.slice(-100)));
}
export function getOfflineQueue() { return _getQueue(); }

export async function flushOfflineQueue() {
  const queue = _getQueue();
  if (!queue.length) return { sent: 0, remaining: 0 };
  let sent = 0;
  const remaining = [];
  for (const item of queue) {
    try {
      await api(item.method, item.path, item.body, { _skipOfflineQueue: true });
      sent++;
    } catch (e) {
      if (e.message === 'SESSION_EXPIRED') break; // μην ξαναστείλεις με ληγμένο token
      remaining.push(item);
    }
  }
  _setQueue(remaining);
  return { sent, remaining: remaining.length };
}

window.addEventListener('online', async () => {
  try {
    const r = await flushOfflineQueue();
    if (r.sent > 0) {
      window.dispatchEvent(new CustomEvent('offline-synced', { detail: r }));
    }
  } catch { /* ignore */ }
});

function _enqueueOffline(method, path, body) {
  if (method === 'GET') return;
  const queue = _getQueue();
  queue.push({ method, path, body, ts: Date.now() });
  _setQueue(queue);
  window.dispatchEvent(new CustomEvent('offline-queued', { detail: queue.length }));
}

function _cacheStale(path, data) {
  if (!data) return;
  try {
    const cache = JSON.parse(localStorage.getItem(STALE_CACHE_KEY) || '{}');
    cache[path] = { data, ts: Date.now() };
    localStorage.setItem(STALE_CACHE_KEY, JSON.stringify(cache));
  } catch { /* quota */ }
}

function _getStale(path) {
  try {
    const cache = JSON.parse(localStorage.getItem(STALE_CACHE_KEY) || '{}');
    const entry = cache[path];
    if (!entry || Date.now() - entry.ts > STALE_TTL_MS) return null;
    return entry.data;
  } catch { return null; }
}

function _isStaleCandidate(path) {
  return STALE_PREFIXES.some(p => path.startsWith(p));
}

export function setToken(t) { 
  _token = t; 
  if (t) {
    localStorage.setItem('rxtrail_token', t);
    // Also store login timestamp for expiry detection
    localStorage.setItem('rxtrail_login_at', Date.now().toString());
  } else {
    localStorage.removeItem('rxtrail_token');
    localStorage.removeItem('rxtrail_login_at');
  }
}
export function getToken() { return _token; }

/**
 * Check if token is likely expired (client-side JWT decode).
 * Returns true if token is expired or about to expire (< 1 hour left).
 */
export function isTokenExpired() {
  try {
    const parts = _token.split('.');
    if (parts.length !== 3) return true;
    const payload = JSON.parse(atob(parts[1]));
    const exp = payload.exp * 1000; // JWT exp is in seconds
    const oneHour = 60 * 60 * 1000;
    return Date.now() > (exp - oneHour);
  } catch {
    return true;
  }
}

/**
 * Force logout: clear token, show login screen.
 * Imported dynamically to avoid circular dependency.
 */
async function forceLogout(reason) {
  _token = '';
  localStorage.removeItem('rxtrail_token');
  localStorage.removeItem('rxtrail_login_at');
  
  // Show auth screen
  const authScreen = document.getElementById('auth-screen');
  const appShell = document.getElementById('app-shell');
  const splash = document.getElementById('splash');
  
  if (splash) splash.classList.add('hidden');
  if (authScreen) authScreen.classList.remove('hidden');
  if (appShell) appShell.classList.add('hidden');
  
  // Show a friendly message
  const loginError = document.getElementById('login-error');
  if (loginError) {
    loginError.textContent = reason || 'Η σύνδεσή σου έληξε. Παρακαλώ ξανασυνδέσου.';
    loginError.classList.remove('hidden');
  }
  
  console.warn('[RxTrail] Auto-logout:', reason);
}

export async function api(method, path, body = null, optsExtra = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (_token) headers['Authorization'] = `Bearer ${_token}`;

  const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
  const url = isLocal ? `${API_BASE}${path}` : `${API_BASE}/api${path}`;

  const opts = { method, headers };
  if (body && method !== 'GET') opts.body = JSON.stringify(body);

  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    // Χωρίς σύνδεση: GET → stale cache, αλλιώς → offline queue
    if (method === 'GET' && _isStaleCandidate(path)) {
      const stale = _getStale(path);
      if (stale !== null) {
        window.dispatchEvent(new CustomEvent('served-stale', { detail: path }));
        return stale;
      }
    }
    if (!optsExtra._skipOfflineQueue) {
      _enqueueOffline(method, path, body);
      throw new Error('📴 Χωρίς σύνδεση — η ενέργεια θα συγχρονιστεί μόλις επιστρέψεις online.');
    }
    throw new Error('Δεν υπάρχει σύνδεση με τον server');
  }

  // ── 401 Auto-handling: force logout ──────────────────────────
  if (res.status === 401) {
    // Don't logout if we're calling /auth/login or /auth/register
    if (!path.startsWith('/auth/login') && !path.startsWith('/auth/register')) {
      await forceLogout('Η σύνδεσή σου έληξε (24 ώρες). Παρακαλώ ξανασυνδέσου.');
      throw new Error('SESSION_EXPIRED');
    }
  }

  if (res.status === 204) return null;

  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    const detail = data.detail;
    const msg = Array.isArray(detail)
      ? detail.map(d => d.msg || JSON.stringify(d)).join('; ')
      : (typeof detail === 'string' ? detail : (data && Object.keys(data).length ? JSON.stringify(data) : `Σφάλμα σέρβερ (HTTP ${res.status})`));
    throw new Error(msg || `HTTP ${res.status}`);
  }

  // Cache stale GETs (για offline ανάγνωση)
  if (method === 'GET' && _isStaleCandidate(path)) {
    _cacheStale(path, data);
  }

  return data;
}

// Convenience methods
export const get = (path) => api('GET', path);
export const post = (path, body) => api('POST', path, body);
export const patch = (path, body) => api('PATCH', path, body);
export const del = (path) => api('DELETE', path);

// File upload
export async function uploadFile(path, file, extraFields = {}) {
  const form = new FormData();
  form.append('file', file);
  for (const [k, v] of Object.entries(extraFields)) form.append(k, v);

  const headers = {};
  if (_token) headers['Authorization'] = `Bearer ${_token}`;

  const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
  const url = isLocal ? `${API_BASE}${path}` : `${API_BASE}/api${path}`;
  const res = await fetch(url, { method: 'POST', headers, body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
  return data;
}
