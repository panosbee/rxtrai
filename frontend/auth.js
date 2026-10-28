/**
 * RxTrail — Auth UI: login μόνο (η εγγραφή γίνεται από τον admin).
 * Vanilla JS, module.
 */
import { post, get, setToken } from './api.js';

let _onAuth = null; // callback when auth succeeds

export function initAuth(onAuthenticated) {
  _onAuth = onAuthenticated;

  // Login form
  document.getElementById('login-form').addEventListener('submit', handleLogin);

  // Check existing token
  const token = localStorage.getItem('rxtrail_token');
  if (token) {
    setToken(token);
    verifyToken();
  } else {
    showAuth();
  }
}

async function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  const errEl = document.getElementById('login-error');

  try {
    errEl.classList.add('hidden');
    const data = await post('/auth/login', { email, password });
    setToken(data.access_token);
    localStorage.setItem('rxtrail_user_id', data.user.id || '');
    localStorage.setItem('rxtrail_user_email', data.user.email || '');
    window._userRole = data.user.role || 'member';
    window._isAdmin = !!data.user.is_admin;
    hideAuth();
    if (_onAuth) _onAuth(data.user);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove('hidden');
  }
}

async function verifyToken() {
  try {
    const user = await get('/auth/me');
    window._userRole = user.role || 'member';
    window._isAdmin = !!user.is_admin;
    hideAuth();
    if (_onAuth) _onAuth(user);
  } catch {
    setToken('');
    showAuth();
  }
}

function showAuth() {
  const splash = document.getElementById('splash');
  if (splash) splash.classList.add('hidden');
  const auth = document.getElementById('auth-screen');
  if (auth) auth.classList.remove('hidden');
  const shell = document.getElementById('app-shell');
  if (shell) shell.classList.add('hidden');
}

function hideAuth() {
  const splash = document.getElementById('splash');
  if (splash) splash.classList.add('hidden');
  const auth = document.getElementById('auth-screen');
  if (auth) auth.classList.add('hidden');
  const shell = document.getElementById('app-shell');
  if (shell) shell.classList.remove('hidden');
}

export function logout() {
  setToken('');
  showAuth();
}
