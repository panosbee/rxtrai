/**
 * RxTrail v2 — Main app shell: navigation, views, FAB, side menu, modals, toasts.
 * Vanilla JS SPA, no framework. Premium UX.
 */
import { initAuth, logout } from './auth.js';
import { renderTodayView } from './views/today.js';
import { renderWeekView } from './views/week.js';
import { renderCasesView } from './views/cases.js';
import { renderNetworkView } from './views/network.js';
import { renderDashboardView } from './views/dashboard.js';
import { openSettingsModal, openImportModal } from './views/settings.js';
import { preloadVoices } from './components/voice.js';
import { initAgentChat } from './components/agentChat.js';

const API_BASE = 'http://127.0.0.1:8000';

let currentView = 'today';
let currentUser = null;

// ── Init ────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  preloadVoices();
  initAgentChat();
  initAuth(onAuthSuccess);
  bindNavigation();
  bindFab();
  bindModal();
  initPullToRefresh();
  registerSW();
  initInstallPrompt();
  updateHeaderDate();

  // Offline: ειδοποιήσεις + συγχρονισμός στην επιστροφή
  import('./api.js').then(({ flushOfflineQueue }) => {
    if (navigator.onLine) {
      flushOfflineQueue().then(r => {
        if (r.sent > 0) showToast(`🔄 Συγχρονίστηκαν ${r.sent} ενέργειες από offline!`, 'success');
      });
    }
  });
  let offlineToastShown = false;
  window.addEventListener('offline-queued', () => {
    if (offlineToastShown) return;
    offlineToastShown = true;
    showToast('📴 Χωρίς σύνδεση — οι ενέργειες σου θα σταλούν μόλις επιστρέψεις.', 'warning', 4000);
    setTimeout(() => { offlineToastShown = false; }, 6000);
  });
  window.addEventListener('offline-synced', (e) => {
    showToast(`🔄 Συγχρονίστηκαν ${e.detail?.sent || 0} ενέργειες!`, 'success');
    refreshCurrentView();
  });

  // Desktop sidebar
  if (window.innerWidth >= 1024) {
    import('./views/desktopSidebar.js').then(m => m.initDesktopSidebar());
  }
  window.addEventListener('resize', () => {
    if (window.innerWidth >= 1024) {
      const sb = document.getElementById('desktop-sidebar');
      if (sb && sb.style.display !== 'flex') {
        import('./views/desktopSidebar.js').then(m => m.initDesktopSidebar());
      }
    }
  });
});

function onAuthSuccess(user) {
  currentUser = user;
  localStorage.setItem('rxtrail_user_id', user.id || '');
  localStorage.setItem('rxtrail_user_email', user.email || '');
  window._userRole = user.role || 'member';
  const nameEl = document.getElementById('side-user-name');
  const emailEl = document.getElementById('side-user-email');
  const avatarEl = document.getElementById('side-user-avatar');
  const topAvatar = document.getElementById('topbar-avatar');

  const initial = (user.name || user.email || '?')[0].toUpperCase();
  if (nameEl) nameEl.textContent = user.name || user.email;
  if (emailEl) emailEl.textContent = user.email || '';
  if (avatarEl) avatarEl.textContent = initial;
  if (topAvatar) topAvatar.textContent = initial;

  // Smooth splash fade-out
  const splash = document.getElementById('splash');
  if (splash) {
    splash.classList.add('fade-out');
    setTimeout(() => { if (splash.parentNode) splash.remove(); }, 400);
  }

  // Navigate to today first
  navigateTo('today');

  // Admin-only UI
  const isAdmin = !!user.is_admin;
  window._isAdmin = isAdmin;
  document.querySelectorAll('.admin-only').forEach(el => {
    el.classList.toggle('hidden', !isAdmin);
  });
  if (isAdmin) {
    const sb = document.getElementById('desktop-sidebar');
    if (sb && sb.style.display === 'flex') {
      import('./views/desktopSidebar.js').then(m => m.initDesktopSidebar());
    }
  }

  // Πρώτη είσοδος admin-created χρήστη → υποχρεωτική αλλαγή κωδικού
  if (user.must_change_password) {
    setTimeout(async () => {
      const { openChangePassword } = await import('./components/changePassword.js');
      openChangePassword(true);
    }, 700);
  }

  // Check if onboarding wizard needed (after a short delay for UI to settle)
  setTimeout(async () => {
    const { maybeShowOnboarding } = await import('./components/onboarding.js');
    await maybeShowOnboarding();
  }, 1400);
}

// ── Data-change events: force views to reload on next navigation ─────────────
window.addEventListener('agent-cases-changed', () => {
  const v = document.getElementById('view-cases');
  if (v) v.innerHTML = '';  // Clear so navigateTo re-renders fresh
});
window.addEventListener('agent-network-changed', () => {
  const v = document.getElementById('view-network');
  if (v) v.innerHTML = '';
});

// ── Navigation ──────────────────────────────────────────────────────────────
let pendingNetworkEntity = ''; // deep-link από sidebar submenu

function bindNavigation() {
  // Tab bar navigation
  document.querySelectorAll('#tabbar .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => navigateTo(btn.dataset.view));
  });

  // Topbar admin link (visible μόνο στον admin)
  const adminTopBtn = document.getElementById('topbar-admin');
  if (adminTopBtn) {
    adminTopBtn.addEventListener('click', () => navigateTo('admin'));
  }

  // Desktop sidebar items (global delegation — sidebar φτιάχνεται δυναμικά)
  document.addEventListener('click', (e) => {
    const item = e.target.closest('.sidebar-nav-item[data-view]');
    if (!item) return;
    const view = item.dataset.view;
    if (view) {
      pendingNetworkEntity = ''; // απλό κλικ σε view → όχι submenu deep-link
      navigateTo(view);
      syncSidebarActive(view);
    }
  });

  // Side menu + desktop sidebar nav links (global delegation)
  document.addEventListener('click', (e) => {
    const link = e.target.closest('[data-nav]');
    if (!link) return;
    e.preventDefault();
    closeSideMenu();
    const nav = link.dataset.nav;
    if (nav === 'dashboard') navigateTo('dashboard');
    if (nav === 'onboarding') openOnboardingWizard();
    if (nav === 'install') triggerInstall();
    if (nav === 'settings') openSettingsModal();
    if (nav === 'import') openImportModal();
    if (nav === 'export') exportData();
    if (nav === 'manual') navigateTo('manual');
    if (nav === 'share') navigateTo('share');
    if (nav === 'admin') navigateTo('admin');
    if (nav === 'password') {
      import('./components/changePassword.js').then(m => m.openChangePassword(false));
    }
    if (nav.startsWith('network:')) {
      pendingNetworkEntity = nav.split(':')[1];
      const netView = document.getElementById('view-network');
      if (currentView === 'network' && netView) netView.innerHTML = ''; // force re-render
      navigateTo('network');
      syncSidebarActive('network');
    }
  });

  document.getElementById('side-overlay').addEventListener('click', closeSideMenu);
  document.getElementById('btn-logout').addEventListener('click', () => {
    closeSideMenu();
    logout();
  });
}

function syncSidebarActive(viewName) {
  document.querySelectorAll('.sidebar-nav-item').forEach(b => {
    b.classList.toggle('active', b.dataset.view === viewName);
  });
}

async function navigateTo(viewName) {
  if (currentView === viewName && document.getElementById(`view-${viewName}`)?.children.length > 0) return;

  const oldView = document.getElementById(`view-${currentView}`);
  currentView = viewName;

  // Update tab bar
  document.querySelectorAll('#tabbar .tab-btn').forEach(b => b.classList.toggle('active', b.dataset.view === viewName));
  syncSidebarActive(viewName);

  // Update views
  const newView = document.getElementById(`view-${viewName}`);
  if (oldView) oldView.classList.remove('active');
  if (newView) {
    newView.classList.add('active');
    void newView.offsetWidth;
  }

  document.getElementById('page-title').textContent = viewLabels[viewName] || 'RxTrail';

  // Render view
  const container = newView;
  if (!container) return;

  // Loading state
  container.innerHTML = '<div class="flex-center" style="padding:60px;"><div class="spinner" style="border-top-color:var(--c-primary);border-color:var(--c-border);"></div></div>';

  try {
    switch (viewName) {
      case 'today': await renderTodayView(container); break;
      case 'plan': {
        const m = await import('./views/plan.js');
        await m.renderPlanView(container);
        break;
      }
      case 'cases': await renderCasesView(container); break;
      case 'network': {
        if (pendingNetworkEntity) {
          const m = await import('./views/network.js');
          m.setNetworkEntity(pendingNetworkEntity);
          pendingNetworkEntity = '';
        }
        await renderNetworkView(container);
        break;
      }
      case 'dashboard': await renderDashboardView(container); break;
      case 'knowledge': {
        const m = await import('./components/knowledgePanel.js');
        await m.renderKnowledgePanel(container);
        break;
      }
      case 'manual': {
        const m = await import('./views/manual.js');
        await m.renderManualView(container);
        break;
      }
      case 'share': {
        const m = await import('./views/share.js');
        await m.renderShareView(container);
        break;
      }
      case 'admin': {
        const m = await import('./views/admin.js');
        await m.renderAdminView(container);
        break;
      }
    }
  } catch (err) {
    container.innerHTML = `<div class="empty-state"><span class="empty-illustration">⚠️</span><h3>Σφάλμα</h3><p>${err.message}</p></div>`;
  }

  closeFabMenu();
}

const viewLabels = {
  today: 'Σήμερα',
  plan: 'Πλάνο',
  cases: 'Περιστατικά',
  network: 'Δίκτυο',
  dashboard: 'Ταμπλό',
  knowledge: 'Βάση Γνώσης',
  manual: 'Εγχειρίδιο Χρήσης',
  share: 'Κοινός Χώρος',
  admin: 'Διαχείριση Χρηστών',
};

// ── Init ────────────────────────────────────────────────────────────────────
function updateHeaderDate() {
  const el = document.getElementById('header-date');
  if (el) {
    el.textContent = new Date().toLocaleDateString('el-GR', { weekday: 'long', day: 'numeric', month: 'long' });
  }
}

// ── FAB ─────────────────────────────────────────────────────────────────────
function bindFab() {
  const fab = document.getElementById('fab');
  const menu = document.getElementById('fab-menu');

  fab.addEventListener('click', () => {
    const isOpen = !menu.classList.contains('hidden');
    if (isOpen) {
      closeFabMenu();
    } else {
      menu.classList.remove('hidden');
      fab.classList.add('open');
    }
  });

  menu.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.action;
      closeFabMenu();
      handleFabAction(action);
    });
  });

  // Close FAB menu on outside click
  document.addEventListener('click', (e) => {
    if (!fab.contains(e.target) && !menu.contains(e.target)) {
      closeFabMenu();
    }
  });
}

function closeFabMenu() {
  const menu = document.getElementById('fab-menu');
  const fab = document.getElementById('fab');
  menu.classList.add('hidden');
  fab.classList.remove('open');
}

function handleFabAction(action) {
  if (action === 'new-case') {
    import('./components/newCaseForm.js').then(m => m.openNewCaseForm());
  } else if (action === 'new-visit') {
    import('./views/visits.js').then(m => {
      const el = document.getElementById('view-today');
      if (el) {
        m.renderVisitsView(el);
        setTimeout(() => {
          const btn = document.getElementById('nv-btn');
          if (btn) btn.click();
        }, 400);
      }
    });
  } else if (action === 'photo-rx') {
    import('./components/upload_wizard.js').then(m => m.openPhotoUpload());
  }
}

// ── Side menu ───────────────────────────────────────────────────────────────
function toggleSideMenu() {
  const menu = document.getElementById('side-menu');
  const overlay = document.getElementById('side-overlay');
  const isOpen = !menu.classList.contains('hidden');

  if (isOpen) {
    closeSideMenu();
  } else {
    menu.classList.remove('hidden');
    overlay.classList.remove('hidden');
  }
}

function closeSideMenu() {
  document.getElementById('side-menu').classList.add('hidden');
  document.getElementById('side-overlay').classList.add('hidden');
}

// ── Modal ───────────────────────────────────────────────────────────────────
function bindModal() {
  document.getElementById('modal-close').addEventListener('click', closeModal);
  document.getElementById('modal-overlay').addEventListener('click', closeModal);
}

export function openModal(title, bodyHtml) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = bodyHtml;
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('modal-container').classList.remove('hidden');
  // Trap focus for a11y
  setTimeout(() => {
    const closeBtn = document.getElementById('modal-close');
    if (closeBtn) closeBtn.focus();
  }, 100);
}

let modalLocked = false;
export function setModalLocked(locked) {
  modalLocked = !!locked;
  const closeBtn = document.getElementById('modal-close');
  if (closeBtn) closeBtn.style.display = modalLocked ? 'none' : '';
}

export function closeModal() {
  if (modalLocked) return;
  document.getElementById('modal-overlay').classList.add('hidden');
  document.getElementById('modal-container').classList.add('hidden');
  // Re-focus on the FAB
  const fab = document.getElementById('fab');
  if (fab) fab.focus();
}

// ── Toast notifications ─────────────────────────────────────────────────────
export function showToast(message, type = 'info', duration = 3000, undoAction = null) {
  const container = document.getElementById('toast-container') || createToastContainer();
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  const icon = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' }[type] || 'ℹ️';
  toast.innerHTML = `<span>${icon}</span> ${message}`;

  if (undoAction) {
    const undoBtn = document.createElement('button');
    undoBtn.textContent = '↩ Αναίρεση';
    undoBtn.style.cssText = 'background:rgba(255,255,255,.25);border:none;color:#fff;padding:4px 10px;border-radius:20px;margin-left:8px;font-weight:700;cursor:pointer;font-size:13px;';
    undoBtn.addEventListener('click', () => {
      undoAction();
      toast.remove();
    });
    toast.appendChild(undoBtn);
  }

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(-20px)';
    toast.style.transition = 'all 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

export function showUndoableDelete(message, deleteFn, restoreFn) {
  showToast(message, 'warning', 5000, async () => {
    try { await restoreFn(); showToast('✅ Επαναφέρθηκε!', 'success'); }
    catch(e) { showToast('❌ Η αναίρεση απέτυχε', 'error'); }
  });
  // Actually delete after showing toast
  setTimeout(async () => {
    try { await deleteFn(); }
    catch(e) { showToast('❌ Σφάλμα διαγραφής', 'error'); }
  }, 100);
}

function createToastContainer() {
  const container = document.createElement('div');
  container.id = 'toast-container';
  container.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;';
  document.body.appendChild(container);
  return container;
}

export { closeModal as closeModalFn, currentUser, API_BASE };

// ── Export ──────────────────────────────────────────────────────────────────
async function exportData() {
  try {
    const token = localStorage.getItem('rxtrail_token');
    const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
    const base = isLocal ? API_BASE : window.location.origin;
    const prefix = isLocal ? '' : '/api';
    window.open(`${base}${prefix}/dashboard/export/xlsx?token=${encodeURIComponent(token)}`, '_blank');
  } catch (e) {
    showToast('Σφάλμα εξαγωγής', 'error');
  }
}

// ── Onboarding ──────────────────────────────────────────────────────────────
async function openOnboardingWizard() {
  const { maybeShowOnboarding } = await import('./components/onboarding.js');
  const shown = await maybeShowOnboarding(true);  // force=true — always show when opened from menu
  if (!shown) {
    showToast('✅ Όλα τα βήματα έχουν ολοκληρωθεί!', 'success');
  }
}

// ── Pull-to-refresh ──────────────────────────────────────────────────────────
function initPullToRefresh() {
  const container = document.getElementById('view-container');
  if (!container) return;

  let pullStart = 0;
  let pulling = false;
  const indicator = document.createElement('div');
  indicator.id = 'ptr-indicator';
  indicator.style.cssText = 'text-align:center;padding:0;height:0;overflow:hidden;transition:height .2s;font-size:13px;color:var(--c-text-muted);';
  container.prepend(indicator);

  container.addEventListener('touchstart', (e) => {
    if (container.scrollTop <= 0) {
      pullStart = e.touches[0].clientY;
      pulling = true;
    }
  }, { passive: true });

  container.addEventListener('touchmove', (e) => {
    if (!pulling) return;
    const pull = e.touches[0].clientY - pullStart;
    if (pull > 30 && container.scrollTop <= 0) {
      indicator.style.height = Math.min(pull - 30, 50) + 'px';
      indicator.textContent = pull > 80 ? '↺ Άφησε για ανανέωση' : '↓ Τράβα για ανανέωση';
    }
  }, { passive: true });

  container.addEventListener('touchend', async () => {
    if (!pulling) return;
    pulling = false;
    const pull = indicator.style.height;
    indicator.style.height = '0';
    if (parseInt(pull) > 40) {
      indicator.textContent = '⟳ Ανανέωση...';
      indicator.style.height = '30px';
      await refreshCurrentView();
      indicator.style.height = '0';
      indicator.textContent = '';
    }
  });
}

async function refreshCurrentView() {
  try {
    const el = document.getElementById(`view-${currentView}`);
    if (!el) return;
    switch (currentView) {
      case 'plan': {
        const m = await import('./views/plan.js');
        await m.renderPlanView(el);
        break;
      }
      case 'cases': {
        const m = await import('./views/cases.js');
        await m.renderCasesView(el);
        break;
      }
      case 'network': {
        const m = await import('./views/network.js');
        await m.renderNetworkView(el);
        break;
      }
      case 'dashboard': {
        const m = await import('./views/dashboard.js');
        await m.renderDashboardView(el);
        break;
      }
    }
  } catch (e) { /* silent */ }
}

// ── PWA: Service Worker — proper lifecycle, no aggressive unregister ────────
async function registerSW() {
  if (!('serviceWorker' in navigator)) return;

  try {
    const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    console.log('[PWA] SW registered:', registration.scope);

    // Check for updates on every page load
    registration.addEventListener('updatefound', () => {
      const newWorker = registration.installing;
      if (!newWorker) return;
      newWorker.addEventListener('statechange', () => {
        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
          console.log('[PWA] New version available — skip waiting');
          newWorker.postMessage('SKIP_WAITING');
          // Reload the page to get the new version
          window.location.reload();
        }
      });
    });

    // If there's a waiting worker, activate it
    if (registration.waiting) {
      registration.waiting.postMessage('SKIP_WAITING');
      window.location.reload();
    }
  } catch (err) {
    console.warn('[PWA] SW registration failed:', err);
  }

  // Listen for SW update messages — force reload when new version installed
  navigator.serviceWorker.addEventListener('message', (event) => {
    if (event.data?.type === 'SW_UPDATED') {
      console.log('[PWA] SW updated to v' + event.data.version + ' — reloading');
      window.location.reload();
    }
  });
}

// ── PWA: Install (sidebar button only, NO banner) ─────────────────────
let deferredPrompt = null;

function initInstallPrompt() {
  // Check if already installed
  if (window.matchMedia('(display-mode: standalone)').matches) {
    return; // Already in PWA mode
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    window.__deferredPWA = e;
    // NO banner — user installs from sidebar ☰ → 📲
  });

  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    window.__deferredPWA = null;
    showToast('✅ Η εφαρμογή εγκαταστάθηκε!', 'success', 3000);
  });
}

function triggerInstall() {
  if (deferredPrompt) {
    deferredPrompt.prompt();
    deferredPrompt.userChoice.then((result) => {
      console.log('[PWA] Install:', result.outcome);
      if (result.outcome === 'accepted') {
        deferredPrompt = null;
        window.__deferredPWA = null;
      }
    });
  } else {
    // Desktop / unsupported: show instructions
    showToast('📲 Άνοιξε το μενού του browser (⋮ ή ⋯) → «Προσθήκη στην αρχική οθόνη» ή «Install app»', 'info', 6000);
  }
}
