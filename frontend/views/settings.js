/**
 * RxTrail — Settings + Import wizard.
 * Vanilla JS, modal-based.
 */
import { openModal, showToast } from '../app.js';
import { uploadFile, get, post } from '../api.js';

export function openSettingsModal() {
  get('/settings').then(org => {
    const s = org.settings || {};
    openModal('⚙️ Ρυθμίσεις', `
      <form id="settings-form">
        <div class="form-group"><label>Όνομα οργανισμού</label><input id="set-name" value="${es(org.name||'')}"></div>
        <hr class="mt-2 mb-2">
        <h4 class="text-sm">⏰ Ρολόγια & SLA</h4>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
          <div class="form-group"><label>Ειδοποίηση επανάληψης (ημέρα)</label><input type="number" id="set-repeat" value="${s.repeat_notify_day||25}" min="1" max="30"></div>
          <div class="form-group"><label>Σε κίνδυνο (ημέρα)</label><input type="number" id="set-risk" value="${s.at_risk_day||35}" min="1" max="60"></div>
          <div class="form-group"><label>SLA Nέου (ημέρες)</label><input type="number" id="set-sla-new" value="${s.new_sla_days||5}" min="1" max="30"></div>
          <div class="form-group"><label>SLA Αξιολόγησης</label><input type="number" id="set-sla-assessed" value="${s.assessed_sla_days||7}" min="1" max="30"></div>
          <div class="form-group"><label>SLA Γνωμάτευσης</label><input type="number" id="set-sla-prescribed" value="${s.prescribed_sla_days||7}" min="1" max="30"></div>
          <div class="form-group"><label>Διαγραφή φωτό (ημέρες)</label><input type="number" id="set-photo-ret" value="${s.photo_retention_days||30}" min="1" max="365"></div>
        </div>
        <button type="submit" class="btn-primary mt-2">💾 Αποθήκευση</button>
        <p id="set-error" class="error-msg hidden mt-2"></p>
        <p id="set-ok" class="text-success hidden mt-2">✅ Οι ρυθμίσεις αποθηκεύτηκαν</p>
      </form>
      <hr class="mt-3 mb-3">
      <div style="background:var(--sea-soft);border-radius:var(--r-card);padding:var(--sp-4);">
        <div class="flex-between">
          <div>
            <h4 style="margin:0;font-size:15px;">🔔 Ειδοποιήσεις στο κινητό</h4>
            <p class="text-sm text-muted mt-1" id="push-status-text" style="margin:4px 0 0;">Έλεγχος...</p>
          </div>
          <div style="display:flex;gap:6px;">
            <button id="push-toggle-btn" class="btn-secondary btn-sm">Ενεργοποίηση</button>
            <button id="push-test-btn" class="btn-text btn-sm hidden">Δοκιμή</button>
          </div>
        </div>
      </div>
    `);

    document.getElementById('settings-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const errEl = document.getElementById('set-error');
      const okEl = document.getElementById('set-ok');
      errEl.classList.add('hidden');
      okEl.classList.add('hidden');
      try {
        const { patch } = await import('../api.js');
        await patch('/settings', {
          name: document.getElementById('set-name').value.trim() || null,
          repeat_notify_day: parseInt(document.getElementById('set-repeat').value) || null,
          at_risk_day: parseInt(document.getElementById('set-risk').value) || null,
          new_sla_days: parseInt(document.getElementById('set-sla-new').value) || null,
          assessed_sla_days: parseInt(document.getElementById('set-sla-assessed').value) || null,
          prescribed_sla_days: parseInt(document.getElementById('set-sla-prescribed').value) || null,
          photo_retention_days: parseInt(document.getElementById('set-photo-ret').value) || null,
        });
        okEl.classList.remove('hidden');
        setTimeout(() => okEl.classList.add('hidden'), 3000);
      } catch (err) {
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
      }
    });

    initPushSection();
  }).catch(err => {
    openModal('⚙️ Ρυθμίσεις', `<div class="list-empty text-danger">Σφάλμα: ${err.message}</div>`);
  });
}

// ── Push notifications ─────────────────────────────────────────────────────

function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map(c => c.charCodeAt(0)));
}

async function initPushSection() {
  const statusEl = document.getElementById('push-status-text');
  const toggleBtn = document.getElementById('push-toggle-btn');
  const testBtn = document.getElementById('push-test-btn');
  if (!toggleBtn) return;

  const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  if (!supported) {
    statusEl.textContent = 'Ο browser σου δεν υποστηρίζει ειδοποιήσεις.';
    toggleBtn.disabled = true;
    return;
  }

  let enabled = false;
  try {
    const st = await get('/notifications/push/status');
    enabled = !!st.enabled;
  } catch { /* default off */ }

  const render = () => {
    statusEl.textContent = enabled
      ? '✅ Ενεργές — θα ειδοποιείσαι κάθε πρωί για επαναλήψεις που λήγουν.'
      : 'Απενεργοποιημένες — ενεργοποίησέ τες για να μη χάνεις επαναλήψεις.';
    toggleBtn.textContent = enabled ? 'Απενεργοποίηση' : 'Ενεργοποίηση';
    testBtn.classList.toggle('hidden', !enabled);
  };
  render();

  toggleBtn.addEventListener('click', async () => {
    try {
      if (!enabled) {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') {
          showToast('⚠️ Δεν έδωσες άδεια ειδοποιήσεων στον browser', 'warning');
          return;
        }
        const pub = await get('/notifications/push/public-key');
        const reg = await navigator.serviceWorker.ready;
        let sub = await reg.pushManager.getSubscription();
        if (!sub) {
          sub = await reg.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(pub.public_key),
          });
        }
        await post('/notifications/push/subscribe', { subscription: sub.toJSON() });
        enabled = true;
        showToast('✅ Ειδοποιήσεις ενεργοποιήθηκαν', 'success');
      } else {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) await sub.unsubscribe();
        await post('/notifications/push/unsubscribe', {});
        enabled = false;
        showToast('Ειδοποιήσεις απενεργοποιήθηκαν', 'info');
      }
      render();
    } catch (err) {
      showToast('❌ ' + err.message, 'error');
    }
  });

  testBtn.addEventListener('click', async () => {
    try {
      const r = await post('/notifications/push/test', {});
      showToast('✅ ' + r.message, 'success');
    } catch (err) {
      showToast('❌ ' + err.message, 'error');
    }
  });
}

// ── Import wizard ───────────────────────────────────────────────────────────

export function openImportModal() {
  openModal('📥 Εισαγωγή δεδομένων', `
    <div class="import-step" id="import-step-1">
      <h3>Βήμα 1: Επίλεξε τύπο & αρχείο</h3>
      <div class="form-group">
        <label>Τύπος δεδομένων</label>
        <select id="import-kind">
          <option value="SKU">SKU (προϊόντα)</option>
          <option value="STRUCTURES">Δομές</option>
          <option value="DOCTORS">Γιατροί</option>
          <option value="CONTACTS">Επαφές</option>
        </select>
      </div>
      <div class="form-group">
        <label>Αρχείο Excel (.xlsx)</label>
        <input type="file" id="import-file" accept=".xlsx">
      </div>
      <button id="import-analyze-btn" class="btn-primary btn-sm">Ανάλυση αρχείου →</button>
      <p id="import-error" class="error-msg hidden"></p>
    </div>
    <div id="import-step-2" class="hidden"></div>
    <div id="import-step-3" class="hidden"></div>
  `);

  document.getElementById('import-analyze-btn').addEventListener('click', analyzeFile);
}

async function analyzeFile() {
  const fileEl = document.getElementById('import-file');
  const kind = document.getElementById('import-kind').value;
  const errEl = document.getElementById('import-error');
  const file = fileEl.files[0];

  if (!file) {
    errEl.textContent = 'Επίλεξε ένα αρχείο .xlsx';
    errEl.classList.remove('hidden');
    return;
  }

  errEl.classList.add('hidden');
  const btn = document.getElementById('import-analyze-btn');
  btn.disabled = true;
  btn.textContent = 'Ανάλυση...';

  try {
    const data = await uploadFile(`/imports/analyze?kind=${kind}`, file);

    // Check for multiple sheets
    const sheets = data.sheet_names || [];
    if (sheets.length > 1) {
      showSheetSelector(data, file, kind);
    } else {
      showMappingStep(data);
    }
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove('hidden');
    btn.disabled = false;
    btn.textContent = 'Ανάλυση αρχείου →';
  }
}

function showSheetSelector(data, file, kind) {
  const sheets = data.sheet_names || [];
  const sheetOptions = sheets.map(s => `
    <option value="${es(s)}" ${s === data.selected_sheet ? 'selected' : ''}>${es(s)}</option>
  `).join('');

  document.getElementById('import-step-1').classList.add('hidden');
  document.getElementById('import-step-2').innerHTML = `
    <h3>📊 Επίλεξε tab (φύλλο)</h3>
    <p class="text-sm text-muted">Το αρχείο έχει ${sheets.length} tabs. Επίλεξε ποιο θες να εισάγεις:</p>
    <div class="form-group mt-2">
      <select id="sheet-select" class="form-group">${sheetOptions}</select>
    </div>
    <button id="sheet-continue-btn" class="btn-primary btn-sm mt-2">Συνέχεια →</button>
    <p id="sheet-error" class="error-msg hidden mt-2"></p>
  `;
  document.getElementById('import-step-2').classList.remove('hidden');

  document.getElementById('sheet-continue-btn').addEventListener('click', async () => {
    const sheetName = document.getElementById('sheet-select').value;
    const errEl = document.getElementById('sheet-error');
    errEl.classList.add('hidden');
    try {
      const newData = await uploadFile(`/imports/analyze?kind=${kind}&sheet_name=${encodeURIComponent(sheetName)}`, file);
      showMappingStep(newData);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

function showMappingStep(data) {
  document.getElementById('import-step-1').classList.add('hidden');

  const { headers, sample_rows, suggested_mapping, import_job_id, total_rows } = data;

  // Build mapping UI
  const mappingRows = headers.map(h => {
    const suggested = suggested_mapping[h] || '';
    const fieldOptions = importFieldOptions[document.getElementById('import-kind').value] || [];
    return `
      <div class="mapping-row">
        <span>${h}</span>
        <select class="mapping-select" data-header="${es(h)}">
          <option value="">— Αγνόησε —</option>
          ${fieldOptions.map(f => `<option value="${f.value}" ${f.value === suggested ? 'selected' : ''}>${f.label}</option>`).join('')}
        </select>
      </div>
    `;
  }).join('');

  // Sample preview
  const previewHtml = sample_rows.length ? `
    <h4 class="mt-2 mb-2">Δείγμα (πρώτες ${sample_rows.length} γραμμές)</h4>
    <div style="overflow-x:auto;">
      <table class="preview-table">
        <thead><tr>${headers.map(h => `<th>${h}</th>`).join('')}</tr></thead>
        <tbody>
          ${sample_rows.map(row => `<tr>${row.map(c => `<td>${es(c)}</td>`).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>
  ` : '';

  document.getElementById('import-step-2').innerHTML = `
    <h3>Βήμα 2: Αντιστοίχιση στηλών</h3>
    <p class="text-sm text-muted">${total_rows} εγγραφές βρέθηκαν${data.selected_sheet ? ' · Tab: «' + es(data.selected_sheet) + '»' : ''}. Αντιστοίχισε κάθε στήλη σε πεδίο.</p>
    <div class="mt-2">${mappingRows}</div>
    ${previewHtml}
    <button id="import-commit-btn" class="btn-primary btn-sm mt-2">✅ Εισαγωγή δεδομένων</button>
    <p id="import-commit-error" class="error-msg hidden"></p>
  `;
  document.getElementById('import-step-2').classList.remove('hidden');

  document.getElementById('import-commit-btn').addEventListener('click', async () => {
    const mapping = {};
    document.querySelectorAll('.mapping-select').forEach(sel => {
      if (sel.value) mapping[sel.dataset.header] = sel.value;
    });

    const errEl = document.getElementById('import-commit-error');
    try {
      // Step: save mapping
      const { get: apiGet, post: apiPost } = await import('../api.js');
      await apiPost('/imports/preview', { mapping, import_job_id });
      const result = await apiPost('/imports/commit', { import_job_id });

      const report = result.report || {};
      const icon = report.errors?.length ? '⚠️' : '✅';
      const statusLine = report.errors?.length
        ? `<p class="text-warning mt-2">${report.errors.length} γραμμές είχαν σφάλμα</p>`
        : '';

      // Show detailed success with error details
      const errorDetails = report.errors?.length
        ? `<div class="mt-2"><p class="text-warning"><strong>⚠️ ${report.errors.length} σφάλματα:</strong></p>
           <div style="max-height:150px;overflow-y:auto;font-size:12px;text-align:left;background:var(--surface);padding:var(--sp-3);border-radius:var(--r-btn);">
             ${report.errors.slice(0, 20).map(e => `<div style="padding:2px 0;border-bottom:1px solid var(--line);">
               <strong>Γραμμή ${e.row}:</strong> <span class="text-danger">${es(e.error.substring(0, 150))}${e.error.length > 150 ? '…' : ''}</span>
             </div>`).join('')}
             ${report.errors.length > 20 ? `<p class="text-muted mt-2">…και άλλα ${report.errors.length - 20} σφάλματα</p>` : ''}
           </div></div>`
        : '';

      const skipInfo = report.skipped ? `<div class="stat-row"><span>⏭️ Παραλείφθηκαν:</span> <strong>${report.skipped}</strong></div>` : '';

      document.getElementById('import-step-2').classList.add('hidden');
      document.getElementById('import-step-3').innerHTML = `
        <h3>${icon} Εισαγωγή ολοκληρώθηκε!</h3>
        <div class="import-report mt-2">
          <div class="stat-row"><span>🆕 Νέες εγγραφές:</span> <strong>${report.inserted || 0}</strong></div>
          <div class="stat-row"><span>🔄 Ενημερώθηκαν:</span> <strong>${report.updated || 0}</strong></div>
          ${skipInfo}
          ${report.errors?.length ? `<div class="stat-row"><span>❌ Σφάλματα:</span> <strong class="text-danger">${report.errors.length}</strong></div>` : ''}
        </div>
        ${errorDetails}
        <p class="text-sm text-muted mt-2">Μπορείς να κλείσεις αυτό το παράθυρο.</p>
        ${report.inserted === 0 && report.updated === 0 ? '<p class="text-warning mt-2">⚠️ Δεν έγινε καμία εισαγωγή. Έλεγξε την αντιστοίχιση στηλών — ο πιο συνηθισμένος λόγος είναι ότι λείπει η αντιστοίχιση για τον κωδικό προϊόντος (eopyy_code).</p>' : ''}
      `;
      document.getElementById('import-step-3').classList.remove('hidden');

      // Refresh current view if Network tab is active
      if (report.inserted > 0 || report.updated > 0) {
        setTimeout(() => {
          window.dispatchEvent(new CustomEvent('import-complete', { detail: { kind: document.getElementById('import-kind').value, report } }));
        }, 500);
      }
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

function es(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const importFieldOptions = {
  SKU: [
    { value: 'product_id', label: 'Κωδικός Προϊόντος (Product ID)' },
    { value: 'eopyy_code', label: 'Κωδικός ΕΟΠΥΥ' },
    { value: 'commercial_name', label: 'Εμπορική ονομασία' },
    { value: 'description', label: 'Περιγραφή' },
    { value: 'category', label: 'Κατηγορία' },
    { value: 'icd10_code', label: 'Κωδικός ICD-10' },
    { value: 'reimb_price', label: 'Τιμή αποζημίωσης (€)' },
    { value: 'patient_copay_pct', label: 'Συμμετοχή ασθενή %' },
    { value: 'max_qty_month', label: 'Μέγιστη ποσότητα/μήνα' },
    { value: 'duration_days_default', label: 'Διάρκεια (ημέρες)' },
  ],
  STRUCTURES: [
    { value: 'name', label: 'Όνομα' },
    { value: 'type', label: 'Τύπος' },
    { value: 'city', label: 'Πόλη' },
    { value: 'prefecture', label: 'Νομός' },
    { value: 'address', label: 'Διεύθυνση' },
    { value: 'phone', label: 'Τηλέφωνο' },
  ],
  DOCTORS: [
    { value: 'name', label: 'Όνομα' },
    { value: 'specialty', label: 'Ειδικότητα' },
    { value: 'phone', label: 'Τηλέφωνο' },
  ],
  CONTACTS: [
    { value: 'name', label: 'Όνομα' },
    { value: 'role_title', label: 'Ρόλος' },
    { value: 'phone', label: 'Τηλέφωνο' },
    { value: 'structure_id', label: 'Δομή (ID)' },
  ],
};
