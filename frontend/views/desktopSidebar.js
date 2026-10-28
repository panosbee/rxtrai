/**
 * RxTrail — Desktop sidebar (≥1024px)
 * Spec §4: 240px left sidebar with logo + nav items + settings at bottom.
 * Navigation clicks handled by app.js (delegation), όχι inline onclick.
 */
export function initDesktopSidebar() {
  if (window.innerWidth < 1024) return;

  const sidebar = document.getElementById('desktop-sidebar');
  if (!sidebar) return;

  sidebar.style.display = 'flex';
  sidebar.innerHTML = `
    <div class="dsb-brand">
      <span class="dsb-logo">💊</span>
      <div class="dsb-name">RxTrail</div>
      <div class="dsb-sub">CRM Υγείας στο Σπίτι</div>
    </div>

    <nav class="dsb-nav">
      ${navItems.map(item => `
        <button class="sidebar-nav-item ${item.view === 'today' ? 'active' : ''}" data-view="${item.view}">
          ${item.icon}
          <span>${item.label}</span>
        </button>
        ${item.submenu ? `
          <div class="dsb-submenu">
            ${item.submenu.map(s => `
              <button class="sidebar-nav-item dsb-subitem" data-nav="network:${s.key}">
                ${s.icon}
                <span>${s.label}</span>
              </button>
            `).join('')}
          </div>
        ` : ''}
      `).join('')}
    </nav>

    <div class="dsb-footer">
      <a href="#" data-nav="password" class="dsb-link">🔑 Αλλαγή κωδικού</a>
      <a href="#" data-nav="admin" class="dsb-link admin-only ${window._isAdmin ? '' : 'hidden'}">🛡️ Διαχείριση</a>
      <a href="#" data-nav="manual" class="dsb-link">📖 Εγχειρίδιο</a>
      <a href="#" data-nav="share" class="dsb-link">🗂️ Κοινός Χώρος</a>
      <a href="#" data-nav="settings" class="dsb-link">⚙️ Ρυθμίσεις</a>
      <div class="dsb-version">RxTrail v2026.08.17 · build 7</div>
    </div>
  `;
}

const navItems = [
  { view: 'today', label: 'Σήμερα', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2"/></svg>' },
  { view: 'cases', label: 'Περιστατικά', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>' },
  { view: 'plan', label: 'Πλάνο', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M3 10h18M8 2v4m8-4v4"/></svg>' },
  {
    view: 'network', label: 'Δίκτυο',
    icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><circle cx="5" cy="5" r="3"/><circle cx="19" cy="5" r="3"/><circle cx="12" cy="19" r="3"/><line x1="7.5" y1="6.5" x2="10.5" y2="16.5"/><line x1="16.5" y1="6.5" x2="13.5" y2="16.5"/></svg>',
    submenu: [
      { key: 'doctors', label: 'Υγειονομικό Προσωπικό', icon: '🩺' },
      { key: 'structures', label: 'Δομές', icon: '🏥' },
      { key: 'sources', label: 'Πηγές', icon: '🔗' },
      { key: 'skus', label: 'SKU', icon: '📦' },
    ],
  },
  { view: 'dashboard', label: 'Ταμπλό', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/></svg>' },
  { view: 'knowledge', label: 'Γνώση', icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/><line x1="8" y1="7" x2="16" y2="7"/><line x1="8" y1="11" x2="14" y2="11"/></svg>' },
];
