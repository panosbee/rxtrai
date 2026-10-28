/**
 * RxTrail — Prescription photo upload + OCR polling + confirm UI.
 * M5 — 2 tap: pick photo → confirm structured data.
 */
import { uploadFile, get, post } from '../api.js';

/**
 * Open the photo upload flow. Called from FAB or case detail.
 */
export function openPhotoUpload(caseId = '') {
  const modal = document.getElementById('modal-container');
  const overlay = document.getElementById('modal-overlay');
  const title = document.getElementById('modal-title');
  const body = document.getElementById('modal-body');
  if (!modal || !body) return;

  title.textContent = '📸 Γνωμάτευση';
  body.innerHTML = `
    <div id="rx-upload-step">
      <p class="text-sm text-muted mb-2">Βγάλε φωτογραφία τη γνωμάτευση ή επίλεξε από τη συσκευή.</p>
      <div class="form-group">
        <input type="file" id="rx-photo-input" accept="image/*" capture="environment" style="display:block;">
      </div>
      <button id="rx-upload-btn" class="btn-primary btn-sm" disabled>📤 Αποστολή & OCR</button>
      <p id="rx-error" class="error-msg hidden mt-2"></p>
    </div>
    <div id="rx-ocr-progress" class="hidden" style="text-align:center;padding:20px;">
      <div class="spinner" style="margin:20px auto;"></div>
      <p class="text-sm text-muted mt-2">OCR σε εξέλιξη... <span id="rx-progress-time">0</span>s</p>
    </div>
    <div id="rx-ocr-result" class="hidden"></div>
  `;

  overlay.classList.remove('hidden');
  modal.classList.remove('hidden');

  const fileInput = document.getElementById('rx-photo-input');
  const uploadBtn = document.getElementById('rx-upload-btn');

  fileInput.addEventListener('change', () => {
    uploadBtn.disabled = !fileInput.files || fileInput.files.length === 0;
  });

  uploadBtn.addEventListener('click', () => startUpload(fileInput.files[0], caseId));
}

async function startUpload(file, caseId) {
  if (!file) return;

  document.getElementById('rx-upload-step').classList.add('hidden');
  document.getElementById('rx-ocr-progress').classList.remove('hidden');

  // Start polling timer
  let elapsed = 0;
  const timerEl = document.getElementById('rx-progress-time');
  const timer = setInterval(() => { elapsed++; timerEl.textContent = elapsed; }, 1000);

  try {
    // Upload file
    const params = new URLSearchParams();
    if (caseId) params.set('case_id', caseId);
    const result = await uploadFile(`/prescriptions/from-photo?${params}`, file);
    const jobId = result.prescription_id;

    // Poll OCR status
    await pollOCRStatus(jobId, timer, 60);
  } catch (err) {
    clearInterval(timer);
    document.getElementById('rx-ocr-progress').classList.add('hidden');
    document.getElementById('rx-ocr-result').classList.remove('hidden');
    document.getElementById('rx-ocr-result').innerHTML = `
      <div class="list-empty text-danger">Σφάλμα: ${err.message}</div>
    `;
  }
}

async function pollOCRStatus(jobId, timer, maxSeconds) {
  let attempts = 0;
  const maxAttempts = maxSeconds * 2; // Poll every 500ms

  const poll = async () => {
    attempts++;
    try {
      const status = await get(`/prescriptions/ocr-status/${jobId}`);

      if (status.status === 'OCR_DONE') {
        clearInterval(timer);
        showOCRResult(status);
        return;
      }

      if (status.status === 'OCR_FAILED' || status.status === 'LLM_FAILED' || status.status === 'OCR_TIMEOUT') {
        clearInterval(timer);
        document.getElementById('rx-ocr-progress').classList.add('hidden');
        document.getElementById('rx-ocr-result').classList.remove('hidden');
        document.getElementById('rx-ocr-result').innerHTML = `
          <div class="list-empty text-danger">
            <h3>❌ Σφάλμα OCR</h3>
            <p>${status.error || 'Άγνωστο σφάλμα'}</p>
            <button class="btn-primary btn-sm mt-2" onclick="document.getElementById('modal-overlay').classList.add('hidden');document.getElementById('modal-container').classList.add('hidden');">Κλείσιμο</button>
          </div>
        `;
        return;
      }

      if (attempts < maxAttempts) {
        setTimeout(poll, 500);
      } else {
        clearInterval(timer);
        document.getElementById('rx-ocr-progress').classList.add('hidden');
        document.getElementById('rx-ocr-result').classList.remove('hidden');
        document.getElementById('rx-ocr-result').innerHTML = `
          <div class="list-empty text-warning">
            <h3>⏰ Χρονικό όριο</h3>
            <p>Το OCR δεν ολοκληρώθηκε σε ${maxSeconds}s. Δοκίμασε ξανά.</p>
          </div>
        `;
      }
    } catch (err) {
      clearInterval(timer);
      throw err;
    }
  };

  await poll();
}

function showOCRResult(result) {
  document.getElementById('rx-ocr-progress').classList.add('hidden');
  const el = document.getElementById('rx-ocr-result');
  el.classList.remove('hidden');

  const lines = result.lines || [];
  const linesHtml = lines.length ? lines.map((l, i) => `
    <div class="card" style="padding:10px;margin-bottom:6px;">
      <strong>${es(l.eopyy_code_raw || '—')}</strong> — ${es(l.name_raw || '')}<br>
      <span class="text-sm text-muted">Qty: ${l.qty || 1} · Διάρκεια: ${l.duration_days || 30} ημ</span>
    </div>
  `).join('') : '<p class="text-muted">Δεν εντοπίστηκαν γραμμές συνταγογράφησης</p>';

  el.innerHTML = `
    <h3>✅ OCR Ολοκληρώθηκε</h3>
    <p class="text-sm text-muted">${result.ocr_confidence ? 'Αξιοπιστία: ' + Math.round(result.ocr_confidence * 100) + '%' : ''}</p>
    <div class="mt-2"><strong>Γιατρός:</strong> ${es(result.doctor_name_raw || '—')}</div>
    <div class="mt-2"><strong>Ημερομηνία:</strong> ${es(result.issued_date || '—')}</div>
    <div class="mt-2"><strong>Γραμμές:</strong></div>
    <div class="mt-1">${linesHtml}</div>
    <div class="mt-2" style="display:flex;gap:8px;flex-wrap:wrap;">
      <button id="rx-create-case-btn" class="btn-primary btn-sm">📁 + Δημιουργία Περιστατικού</button>
      <button id="rx-confirm-btn" class="btn-secondary btn-sm">✅ Μόνο επιβεβαίωση</button>
      <button id="rx-close-btn" class="btn-text btn-sm">Κλείσιμο</button>
    </div>
    <p id="rx-confirm-error" class="error-msg hidden mt-2"></p>
  `;

  document.getElementById('rx-close-btn').addEventListener('click', () => {
    document.getElementById('modal-overlay').classList.add('hidden');
    document.getElementById('modal-container').classList.add('hidden');
  });

  document.getElementById('rx-confirm-btn').addEventListener('click', () => {
    document.getElementById('modal-overlay').classList.add('hidden');
    document.getElementById('modal-container').classList.add('hidden');
  });

  // Auto-create case from prescription
  const createCaseBtn = document.getElementById('rx-create-case-btn');
  if (createCaseBtn) {
    createCaseBtn.addEventListener('click', async () => {
      createCaseBtn.disabled = true;
      createCaseBtn.textContent = '⏳ Δημιουργία...';
      try {
        const r = await post(`/prescriptions/${result.job_id}/create-case`);
        document.getElementById('rx-confirm-error').classList.remove('hidden');
        document.getElementById('rx-confirm-error').style.color = 'var(--success)';
        document.getElementById('rx-confirm-error').textContent = r.message || 'Περιστατικό δημιουργήθηκε!';
        // Refresh cases view
        window.dispatchEvent(new CustomEvent('agent-cases-changed'));
        setTimeout(() => {
          document.getElementById('modal-overlay').classList.add('hidden');
          document.getElementById('modal-container').classList.add('hidden');
        }, 1800);
      } catch (err) {
        document.getElementById('rx-confirm-error').classList.remove('hidden');
        document.getElementById('rx-confirm-error').style.color = 'var(--danger)';
        document.getElementById('rx-confirm-error').textContent = err.message;
        createCaseBtn.disabled = false;
        createCaseBtn.textContent = '📁 + Δημιουργία Περιστατικού';
      }
    });
  }
}

function es(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
