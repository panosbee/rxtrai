/**
 * RxTrail — Admin view: διαχείριση χρηστών (ΜΟΝΟ για τον admin).
 *
 * Δυνατότητες:
 *   - Λίστα χρηστών (email, όνομα, org, ρόλος, κατάσταση, ημ/νία)
 *   - Δημιουργία χρήστη (email + κωδικός + όνομα)
 *   - Επαναφορά κωδικού
 *   - Ενεργοποίηση / απενεργοποίηση λογαριασμού
 *   - Διαγραφή χρήστη + δεδομένων του
 */
import { get, post, del } from '../api.js';
import { openModal, showToast } from '../app.js';

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export async function renderAdminView(container) {
  container.innerHTML = `
    <div class="admin-panel">
      <div class="flex-between mb-3">
        <div>
          <h2 style="font-size:20px;font-weight:800;margin:0;">🛡️ Διαχείριση Χρηστών</h2>
          <p class="text-sm text-muted mt-1">Δημιούργησε, άλλαξε κωδικό ή διέγραψε χρήστες. Οι εγγραφές είναι κλειστές — μόνο εσύ φτιάχνεις λογαριασμούς. Κάθε χρήστης βλέπει ΜΟΝΟ τα δικά του δεδομένα.</p>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <button id="admin-export-btn" class="btn-secondary btn-sm">📥 Εξαγωγή Excel (όλα)</button>
          <button id="admin-new-btn" class="btn-primary btn-sm">+ Νέος χρήστης</button>
        </div>
      </div>

      <div id="admin-stats" class="stat-grid mb-3"></div>
      <p class="text-faint" style="font-size:11.5px;margin-top:-8px;margin-bottom:12px;">↑ Σύνολα ΟΛΟΥ του συστήματος (άθροισμα όλων των λογαριασμών) — όχι δικά σου δεδομένα.</p>

      <div class="card">
        <div class="card-header"><span class="card-title">👥 Χρήστες & τα δικά τους δεδομένα</span></div>
        <div id="admin-users-table"></div>
      </div>
    </div>
  `;

  document.getElementById('admin-new-btn').addEventListener('click', () => openCreateUserModal());
  document.getElementById('admin-export-btn').addEventListener('click', () => {
    const token = localStorage.getItem('rxtrail_token');
    const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
    const base = isLocal ? 'http://127.0.0.1:8000' : window.location.origin;
    const prefix = isLocal ? '' : '/api';
    window.open(`${base}${prefix}/admin/export.xlsx?token=${encodeURIComponent(token)}`, '_blank');
  });

  await Promise.all([loadStats(container), loadUsers(container)]);
}

async function loadStats(container) {
  const statsEl = container.querySelector('#admin-stats');
  try {
    const s = await get('/admin/stats');
    const items = [
      ['👥 Χρήστες', s.users],
      ['🏢 Οργανισμοί', s.orgs],
      ['📁 Περιστατικά', s.cases],
      ['🩺 Προσωπικό', s.doctors],
      ['🏥 Δομές', s.structures],
      ['📦 SKU', s.skus],
    ];
    statsEl.innerHTML = items.map(([label, val]) => `
      <div class="stat-card"><div class="stat-value">${val}</div><div class="stat-label">${label}</div></div>
    `).join('');
  } catch {
    statsEl.innerHTML = '<div class="text-danger text-sm">Δεν φόρτωσαν τα στατιστικά</div>';
  }
}

async function loadUsers(container) {
  const tableEl = container.querySelector('#admin-users-table');
  try {
    const data = await get('/admin/users');
    const me = localStorage.getItem('rxtrail_user_email') || '';

    if (!data.items.length) {
      tableEl.innerHTML = '<div class="kp-empty">Κανένας χρήστης.</div>';
      return;
    }

    tableEl.innerHTML = data.items.map(u => {
      const isMe = u.email.toLowerCase() === me.toLowerCase();
      const created = u.created_at ? new Date(u.created_at).toLocaleDateString('el-GR') : '—';
      const stateChip = u.is_active
        ? '<span class="status-chip status-ACTIVE">Ενεργός</span>'
        : '<span class="status-chip status-LOST">Ανενεργός</span>';
      const adminChip = u.is_admin ? '<span class="status-chip status-ASSESSED">Admin</span>' : '';
      const pwChip = u.must_change_password
        ? '<span class="status-chip status-NEW">Να αλλάξει κωδικό</span>'
        : '';
      const c = u.counts || {};
      return `
        <div class="admin-user-row" data-id="${esc(u.id)}">
          <div class="admin-user-main">
            <div class="admin-user-name">${esc(u.name) || '—'} ${adminChip} ${isMe ? '<span class="status-chip status-AT_DOCTOR">Εσύ</span>' : ''}</div>
            <div class="admin-user-email">${esc(u.email)} · org: ${esc(u.org_name) || '—'}</div>
            <div class="admin-user-meta">${stateChip} ${pwChip} <span class="text-faint">${created}</span></div>
            <div class="admin-user-counts">
              📁 ${c.cases || 0} · 🩺 ${c.doctors || 0} · 🏥 ${c.structures || 0} · 📦 ${c.skus || 0} · 🔗 ${c.sources || 0}
              <span class="text-faint">(μόνο δικά του)</span>
            </div>
          </div>
          <div class="admin-user-actions">
            <button class="btn-secondary btn-sm act-stats" data-id="${esc(u.id)}">📊 Απόδοση</button>
            <button class="btn-secondary btn-sm act-export" data-id="${esc(u.id)}" title="Excel: Πηγές, Δομές, Περιστατικά, Υγειονομικό">📥 Excel</button>
            <button class="btn-secondary btn-sm act-week" data-id="${esc(u.id)}" data-name="${esc(u.name || u.email)}" title="Εβδομαδιαίο πρόγραμμα σε Excel">📅 Πρόγραμμα</button>
            <button class="btn-secondary btn-sm act-reset" data-id="${esc(u.id)}" data-email="${esc(u.email)}">🔑 Κωδικός</button>
            <button class="btn-secondary btn-sm act-toggle" data-id="${esc(u.id)}" data-active="${u.is_active}">${u.is_active ? '⏸️ Απενεργ/ση' : '▶️ Ενεργ/ση'}</button>
            <button class="btn-danger btn-sm act-delete" data-id="${esc(u.id)}" data-email="${esc(u.email)}" ${isMe ? 'disabled title="Δεν μπορείς να διαγράψεις τον εαυτό σου"' : ''}>🗑️</button>
          </div>
        </div>
      `;
    }).join('');

    tableEl.querySelectorAll('.act-stats').forEach(btn => {
      btn.addEventListener('click', () => openUserStats(btn.dataset.id));
    });

    // 📥 Excel ανά χρήστη (Πηγές, Δομές, Περιστατικά, Υγειονομικό)
    tableEl.querySelectorAll('.act-export').forEach(btn => {
      btn.addEventListener('click', () => {
        downloadUserExcel(btn.dataset.id);
      });
    });

    // 📅 Εβδομαδιαίο πρόγραμμα σε Excel
    tableEl.querySelectorAll('.act-week').forEach(btn => {
      btn.addEventListener('click', () => openWeekExport(btn.dataset.id, btn.dataset.name));
    });
    tableEl.querySelectorAll('.act-reset').forEach(btn => {
      btn.addEventListener('click', () => openResetPasswordModal(btn.dataset.id, btn.dataset.email));
    });
    tableEl.querySelectorAll('.act-toggle').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          const r = await post(`/admin/users/${btn.dataset.id}/toggle-active`, {});
          showToast(r.message || 'OK', 'success');
          loadUsers(container);
        } catch (err) {
          showToast(err.message, 'error');
        }
      });
    });
    tableEl.querySelectorAll('.act-delete').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm(`⚠️ Οριστική διαγραφή του χρήστη ${btn.dataset.email} ΚΑΙ ΟΛΩΝ των δεδομένων του (περιστατικά, γιατροί, δομές, SKU, γνώση); Δεν ανατρέπεται.`)) return;
        try {
          const r = await del(`/admin/users/${btn.dataset.id}`);
          showToast(r.message || 'Διαγράφηκε', 'success');
          loadStats(container);
          loadUsers(container);
        } catch (err) {
          showToast(err.message, 'error');
        }
      });
    });
  } catch (err) {
    tableEl.innerHTML = `<div class="text-danger text-sm">Σφάλμα: ${esc(err.message)}</div>`;
  }
}

function openCreateUserModal() {
  openModal('➕ Νέος χρήστης', `
    <form id="admin-create-form" autocomplete="off">
      <p class="text-sm text-muted mb-3">Θα δημιουργηθεί λογαριασμός με το δικό του «οργανισμό». Ο χρήστης θα μπει με τον κωδικό που θα ορίσεις και θα αναγκαστεί να τον αλλάξει στην πρώτη είσοδο.</p>
      <div class="form-group"><label>Όνομα</label><input id="ac-name" placeholder="π.χ. Μαρία Παπαδοπούλου" required></div>
      <div class="form-group"><label>Email</label><input type="email" id="ac-email" placeholder="maria@example.com" required></div>
      <div class="form-group"><label>Αρχικός κωδικός (≥8 χαρακτήρες)</label><input type="text" id="ac-password" minlength="8" placeholder="προσωρινός κωδικός" required></div>
      <button type="submit" class="btn-primary full-width">Δημιουργία χρήστη</button>
      <p id="ac-error" class="error-msg hidden mt-2"></p>
    </form>
  `);
  document.getElementById('admin-create-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('ac-error');
    errEl.classList.add('hidden');
    try {
      const { closeModal } = await import('../app.js');
      const r = await post('/admin/users', {
        name: document.getElementById('ac-name').value.trim(),
        email: document.getElementById('ac-email').value.trim(),
        password: document.getElementById('ac-password').value,
      });
      closeModal();
      showToast(`✅ ${r.message}`, 'success');
      const container = document.getElementById('view-admin');
      loadStats(container);
      loadUsers(container);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

function openResetPasswordModal(userId, email) {
  openModal(`🔑 Νέος κωδικός για ${email}`, `
    <form id="admin-reset-form" autocomplete="off">
      <p class="text-sm text-muted mb-3">Ο χρήστης θα μπει με τον νέο κωδικό και θα τον αλλάξει στην επόμενη είσοδο.</p>
      <div class="form-group"><label>Νέος κωδικός (≥8 χαρακτήρες)</label><input type="text" id="ar-password" minlength="8" required></div>
      <button type="submit" class="btn-primary full-width">Ορισμός κωδικού</button>
      <p id="ar-error" class="error-msg hidden mt-2"></p>
    </form>
  `);
  document.getElementById('admin-reset-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('ar-error');
    errEl.classList.add('hidden');
    try {
      const { closeModal } = await import('../app.js');
      const r = await post(`/admin/users/${userId}/reset-password`, {
        password: document.getElementById('ar-password').value,
      });
      closeModal();
      showToast(`✅ ${r.message}`, 'success');
      const container = document.getElementById('view-admin');
      loadUsers(container);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

// ── 📊 Απόδοση χρήστη: charts (SVG, χωρίς βιβλιοθήκες) ─────────────────────

const STATUS_LABELS = {
  NEW: '🆕 Νέο', ASSESSED: '✅ Αξιολογήθηκε', AT_DOCTOR: '👨‍⚕️ Σε γιατρό',
  PRESCRIBED: '📝 Γνωμάτευση', ACTIVE: '📦 Ενεργό', AT_RISK: '🚨 Σε κίνδυνο',
  LOST: '❌ Χάθηκε', CLOSED: '🏁 Έκλεισε',
};
const STATUS_COLORS = {
  NEW: '#B7791F', ASSESSED: '#7C5CBF', AT_DOCTOR: '#2B6CB0', PRESCRIBED: '#0E7E5A',
  ACTIVE: '#14A173', AT_RISK: '#E05E0C', LOST: '#94A1AA', CLOSED: '#566A76',
};

/** Bar chart (SVG) — values ανά μήνα. */
function barChart(values, labels, { color = '#0A7E8C', money = false } = {}) {
  const width = 520, height = 150, baseY = height - 22;
  const max = Math.max(1, ...values);
  const bw = width / values.length;
  const bars = values.map((v, i) => {
    const h = Math.max(v > 0 ? 3 : 0, Math.round((v / max) * (baseY - 26)));
    const x = i * bw + bw * 0.18;
    const y = baseY - h;
    const label = money ? (Math.round(v) + '€') : String(v);
    return `
      <rect x="${x.toFixed(1)}" y="${y}" width="${(bw * 0.64).toFixed(1)}" height="${h}" rx="4" fill="${color}" opacity="0.88"></rect>
      <text x="${(x + bw * 0.32).toFixed(1)}" y="${(y - 4)}" text-anchor="middle" font-size="9.5" fill="#47606C">${v > 0 ? label : ''}</text>
      <text x="${(x + bw * 0.32).toFixed(1)}" y="${height - 6}" text-anchor="middle" font-size="9.5" fill="#7C909C">${labels[i]}</text>
    `;
  }).join('');
  return `<svg viewBox="0 0 ${width} ${height}" style="width:100%;height:auto;display:block;">${bars}</svg>`;
}

/** Οριζόντιες μπάρες κατανομής (π.χ. καταστάσεις). */
function hBarChart(pairs) {
  const total = pairs.reduce((s, [, v]) => s + v, 0) || 1;
  return pairs.map(([key, val]) => `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">
      <span style="width:130px;font-size:12px;color:#47606C;">${STATUS_LABELS[key] || key}</span>
      <div style="flex:1;background:#EDF2F4;border-radius:6px;height:16px;overflow:hidden;">
        <div style="width:${Math.round((val / total) * 100)}%;height:100%;background:${STATUS_COLORS[key] || '#0A7E8C'};border-radius:6px;"></div>
      </div>
      <span style="width:34px;text-align:right;font-size:12px;font-weight:700;color:#10212B;">${val}</span>
    </div>
  `).join('');
}

async function openUserStats(userId) {
  openModal('📊 Απόδοση χρήστη', '<div class="flex-center" style="padding:40px;"><div class="spinner"></div></div>');
  let s;
  try {
    s = await get(`/admin/users/${userId}/stats`);
  } catch (err) {
    document.getElementById('modal-body').innerHTML =
      `<div class="list-empty text-danger">Σφάλμα: ${esc(err.message)}</div>`;
    return;
  }

  const labels = (s.months || []).map(m => {
    const [y, mo] = m.split('-');
    return ['', 'Ιαν', 'Φεβ', 'Μάρ', 'Απρ', 'Μάι', 'Ιούν', 'Ιούλ', 'Αύγ', 'Σεπ', 'Οκτ', 'Νοέ', 'Δεκ'][parseInt(mo, 10)] || m;
  });
  const ser = s.series || {};
  const t = s.totals || {};
  const c = s.counts || {};
  const statusPairs = Object.entries(s.cases_by_status || {}).sort((a, b) => b[1] - a[1]);

  const joined = s.user.created_at ? new Date(s.user.created_at).toLocaleDateString('el-GR') : '—';
  const lastLogin = s.user.last_login_at ? new Date(s.user.last_login_at).toLocaleString('el-GR') : '—';

  const card = (title, svg) => `
    <div class="card" style="padding:var(--sp-3);margin-bottom:10px;">
      <div style="font-weight:700;font-size:13.5px;margin-bottom:6px;">${title}</div>
      ${svg}
    </div>`;

  document.getElementById('modal-title').textContent = `📊 ${s.user.name || s.user.email}`;
  document.getElementById('modal-body').innerHTML = `
    <div class="text-sm text-muted mb-2">${esc(s.user.email)} · org: ${esc(s.user.org_name || '—')} · εγγραφή: ${joined} · τελευταία είσοδος: ${lastLogin}</div>

    <div class="stat-grid mb-3" style="grid-template-columns:repeat(3,1fr);">
      <div class="stat-card"><div class="stat-value">${c.cases || 0}</div><div class="stat-label">Περιστατικά</div></div>
      <div class="stat-card"><div class="stat-value">${c.prescriptions || 0}</div><div class="stat-label">Γνωματεύσεις</div></div>
      <div class="stat-card"><div class="stat-value">${c.fulfilments || 0}</div><div class="stat-label">Εκτελέσεις</div></div>
      <div class="stat-card"><div class="stat-value">${c.visits || 0}</div><div class="stat-label">Επισκέψεις</div></div>
      <div class="stat-card"><div class="stat-value">${c.doctors || 0}</div><div class="stat-label">Γιατροί</div></div>
      <div class="stat-card"><div class="stat-value">${Math.round(t.active_value || 0)}€</div><div class="stat-label">Αξία ενεργών</div></div>
    </div>

    ${card('📁 Νέα περιστατικά / μήνα', barChart(ser.cases || [], labels, { color: '#0A7E8C' }))}
    ${card('💊 Γνωματεύσεις / μήνα', barChart(ser.prescriptions || [], labels, { color: '#7C5CBF' }))}
    ${card('📦 Εκτελέσεις / μήνα', barChart(ser.fulfilments || [], labels, { color: '#14A173' }))}
    ${card('💶 Τζίρος / μήνα (από εκτελέσεις)', barChart(ser.revenue || [], labels, { color: '#C9912E', money: true }))}

    <div class="card" style="padding:var(--sp-3);">
      <div style="font-weight:700;font-size:13.5px;margin-bottom:8px;">📌 Κατανομή περιστατικών</div>
      ${statusPairs.length ? hBarChart(statusPairs) : '<div class="text-sm text-muted">Δεν υπάρχουν περιστατικά.</div>'}
      <div class="text-sm text-muted mt-2">Συνολικός τζίρος: <strong>${Math.round(t.total_revenue || 0)}€</strong> · Δομές: ${c.structures || 0} · SKU: ${c.skus || 0} · Πηγές: ${c.sources || 0}</div>
    </div>
  `;
}

// ── 📥 Βοηθητικά: exports ανά χρήστη ─────────────────────────────────────

function _downloadUrl(path) {
  const token = localStorage.getItem('rxtrail_token');
  const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
  const base = isLocal ? 'http://127.0.0.1:8000' : window.location.origin;
  const prefix = isLocal ? '' : '/api';
  return `${base}${prefix}${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

function downloadUserExcel(userId) {
  window.open(_downloadUrl(`/admin/users/${userId}/export.xlsx`), '_blank');
}

function openWeekExport(userId, userName) {
  const today = new Date().toISOString().slice(0, 10);
  openModal('📅 Πρόγραμμα εβδομάδας σε Excel', `
    <p class="text-sm text-muted mb-2">Κατέβασε το εβδομαδιαίο πρόγραμμα του <strong>${esc(userName || '')}</strong>. Διάλεξε οποιαδήποτε ημερομηνία μέσα στην εβδομάδα που θέλεις.</p>
    <div class="form-group"><label>Ημερομηνία (οποιαδήποτε μέρα της εβδομάδας)</label><input type="date" id="wk-export-date" value="${today}"></div>
    <div style="display:flex;gap:8px;margin-bottom:10px;">
      <button type="button" id="wk-this-btn" class="btn-secondary btn-sm" style="flex:1;">Αυτή η εβδομάδα</button>
      <button type="button" id="wk-next-btn" class="btn-secondary btn-sm" style="flex:1;">Επόμενη εβδομάδα</button>
    </div>
    <button id="wk-export-btn" class="btn-primary full-width">📥 Κατέβασμα Excel</button>
  `);
  const dateEl = document.getElementById('wk-export-date');
  const setWeek = (offsetDays) => {
    const d = new Date();
    d.setDate(d.getDate() + offsetDays);
    dateEl.value = d.toISOString().slice(0, 10);
  };
  document.getElementById('wk-this-btn').addEventListener('click', () => setWeek(0));
  document.getElementById('wk-next-btn').addEventListener('click', () => setWeek(7));
  document.getElementById('wk-export-btn').addEventListener('click', async () => {
    const { closeModal } = await import('../app.js');
    const week = dateEl.value || today;
    window.open(_downloadUrl(`/admin/users/${userId}/week.xlsx?week=${week}`), '_blank');
    closeModal();
  });
}
