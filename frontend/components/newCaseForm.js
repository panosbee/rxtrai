/**
 * RxTrail — ΕΝΙΑΙΑ πλήρης φόρμα νέου περιστατικού.
 *
 * Περιέχει:
 *   - Manual πεδία (πηγή, ψευδώνυμο, περιοχή, κατηγορίες, περιγραφή)
 *   - 🎤 AI Voice → DeepSeek intake → αυτόματη συμπλήρωση
 *   - 📸 Upload φωτογραφιών (multiple, με preview)
 *   - 📄 Upload αρχείων/PDF (με ονομασία)
 *
 * Χρησιμοποιείται από:
 *   - "+ Νέο" στο Cases kanban
 *   - FAB → "🎤 Νέο περιστατικό"
 *   - Όπου αλλού δημιουργείται case
 */
import { get, post, uploadFile } from '../api.js';
import { listenGreek } from './voice.js';

/**
 * Ανοίγει την πλήρη φόρμα νέου περιστατικού στο modal.
 * @param {Function} onCreated — callback όταν δημιουργηθεί το case (προαιρετικό)
 */
export async function openNewCaseForm(onCreated = null) {
  const { openModal, closeModal } = await import('../app.js');

  // Φόρτωση πηγών για το dropdown
  let sources = [];
  try {
    const data = await get('/sources?limit=200');
    sources = data.items || [];
  } catch (e) { /* θα φανεί στο dropdown */ }

  const sourceOptions = sources.length
    ? sources.map(s =>
        `<option value="${s._id}">${es(s.name)} (${s.type || ''})</option>`
      ).join('')
    : '<option value="" disabled>— Καμία πηγή — Πήγαινε στο Δίκτυο → Πηγές για να προσθέσεις</option>';

  openModal('🆕 Νέο Περιστατικό', `
    <form id="new-case-full-form" style="max-height:70vh;overflow-y:auto;padding-right:4px;">

      <!-- ═══ MANUAL ΠΕΔΙΑ ═══ -->
      <div class="form-group"><label>Πηγή σύστασης (προαιρετικό)</label>
        <select id="ncf-source"><option value="">— Χωρίς πηγή —</option>${sourceOptions}</select>
        ${!sources.length ? '<p class="text-sm text-warning mt-1">⚠️ Δεν υπάρχουν πηγές. <button type="button" id="ncf-quick-source-btn" class="btn-text btn-sm" style="color:var(--c-primary);text-decoration:underline;">+ Προσθήκη πηγής τώρα</button></p>' : ''}
      </div>
      <div class="form-group"><label>Ψευδώνυμο *</label>
        <input id="ncf-alias" placeholder="π.χ. Παππούς Γάζι — κατάκλιση" required>
      </div>
      <div class="form-group"><label>ΑΜΚΑ (προαιρετικό)</label>
        <input id="ncf-amka" placeholder="11 ψηφία" maxlength="30">
      </div>
      <div class="form-group"><label>Περιοχή</label>
        <input id="ncf-area" placeholder="π.χ. Γάζι, Ηράκλειο">
      </div>
      <div class="form-group"><label>Κατηγορίες αναγκών (κόμμα)</label>
        <input id="ncf-cat" placeholder="WOUND, INCONT, ONS...">
      </div>
      <div class="form-group"><label>Περιγραφή / Σημειώσεις</label>
        <textarea id="ncf-desc" rows="3" placeholder="Ελεύθερη περιγραφή..."></textarea>
      </div>

      <!-- ═══ 🎤 AI VOICE ═══ -->
      <div class="card" style="padding:12px;margin-bottom:12px;background:var(--c-bg);">
        <div class="flex-between">
          <strong style="font-size:14px;">🎤 AI Voice Intake</strong>
          <span class="text-sm text-muted">Μίλα ή γράψε ελεύθερα → αυτόματη συμπλήρωση</span>
        </div>
        <div style="display:flex;gap:8px;margin-top:8px;">
          <textarea id="ncf-voice-text" rows="2" placeholder="π.χ. Με πήρε η Μαρία από το Βοήθεια στο Σπίτι, παππούς στο Γάζι, κατάκλιση..." style="flex:1;"></textarea>
        </div>
        <div class="flex-between mt-1">
          <button type="button" id="ncf-mic-btn" class="btn-primary btn-sm">🎤 Μικρόφωνο</button>
          <button type="button" id="ncf-ai-btn" class="btn-primary btn-sm">🤖 Ανάλυση AI →</button>
        </div>
        <p id="ncf-ai-status" class="text-sm text-muted hidden mt-1"></p>
      </div>

      <!-- ═══ 📸 ΦΩΤΟΓΡΑΦΙΕΣ ═══ -->
      <div style="border-top:1px solid var(--c-border);padding-top:12px;margin-bottom:12px;">
        <strong style="font-size:14px;">📸 Φωτογραφίες</strong>
        <div id="ncf-photos-preview" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;"></div>
        <input type="file" id="ncf-photo-input" accept="image/*" multiple style="display:none;">
        <button type="button" id="ncf-add-photo-btn" class="btn-text btn-sm mt-1">+ Προσθήκη φωτογραφίας</button>
      </div>

      <!-- ═══ 📄 ΑΡΧΕΙΑ / PDF ═══ -->
      <div style="border-top:1px solid var(--c-border);padding-top:12px;margin-bottom:12px;">
        <strong style="font-size:14px;">📄 Αρχεία / PDF</strong>
        <div id="ncf-files-list" style="margin-top:8px;"></div>
        <input type="file" id="ncf-file-input" accept=".pdf,.doc,.docx" multiple style="display:none;">
        <button type="button" id="ncf-add-file-btn" class="btn-text btn-sm mt-1">+ Προσθήκη αρχείου</button>
      </div>

      <!-- ═══ SUBMIT ═══ -->
      <button type="submit" id="ncf-submit-btn" class="btn-primary full-width mt-2">💾 Δημιουργία Περιστατικού</button>
      <p id="ncf-error" class="error-msg hidden mt-2"></p>
    </form>
  `);

  // ── State ──────────────────────────────────────────────────────────────
  const photoFiles = [];
  const docFiles = [];  // { file, name }

  // ── Φωτογραφίες ──────────────────────────────────────────────────────
  document.getElementById('ncf-add-photo-btn').addEventListener('click', () => {
    document.getElementById('ncf-photo-input').click();
  });
  document.getElementById('ncf-photo-input').addEventListener('change', () => {
    const input = document.getElementById('ncf-photo-input');
    for (const f of input.files) {
      photoFiles.push(f);
      const url = URL.createObjectURL(f);
      const preview = document.getElementById('ncf-photos-preview');
      const card = document.createElement('div');
      card.style.cssText = 'position:relative;width:80px;height:80px;border-radius:8px;overflow:hidden;border:1px solid var(--c-border);';
      card.innerHTML = `
        <img src="${url}" style="width:100%;height:100%;object-fit:cover;">
        <button type="button" class="remove-photo-btn" style="position:absolute;top:2px;right:2px;background:var(--c-danger);color:#fff;border:none;border-radius:50%;width:20px;height:20px;font-size:12px;cursor:pointer;line-height:1;">✕</button>
      `;
      card.querySelector('.remove-photo-btn').addEventListener('click', () => {
        const idx = photoFiles.indexOf(f);
        if (idx >= 0) photoFiles.splice(idx, 1);
        card.remove();
        URL.revokeObjectURL(url);
      });
      preview.appendChild(card);
    }
    input.value = '';
  });

  // ── Αρχεία PDF ───────────────────────────────────────────────────────
  document.getElementById('ncf-add-file-btn').addEventListener('click', () => {
    document.getElementById('ncf-file-input').click();
  });
  document.getElementById('ncf-file-input').addEventListener('change', () => {
    const input = document.getElementById('ncf-file-input');
    for (const f of input.files) {
      const name = prompt('Όνομα αρχείου:', f.name) || f.name;
      docFiles.push({ file: f, name });
      const list = document.getElementById('ncf-files-list');
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--c-border);font-size:13px;';
      row.innerHTML = `<span>📄 ${es(name)} <span class="text-muted">(${formatSize(f.size)})</span></span>
        <button type="button" class="remove-file-btn icon-btn" style="font-size:14px;">🗑️</button>`;
      row.querySelector('.remove-file-btn').addEventListener('click', () => {
        const idx = docFiles.findIndex(d => d.file === f);
        if (idx >= 0) docFiles.splice(idx, 1);
        row.remove();
      });
      list.appendChild(row);
    }
    input.value = '';
  });

  // ── 🎤 AI Voice ──────────────────────────────────────────────────────
  let micAbort = null;
  document.getElementById('ncf-mic-btn').addEventListener('click', async () => {
    const btn = document.getElementById('ncf-mic-btn');
    const status = document.getElementById('ncf-ai-status');

    // If already recording, STOP
    if (micAbort) {
      micAbort();
      micAbort = null;
      btn.innerHTML = '🎤 Μικρόφωνο';
      btn.classList.remove('recording');
      status.textContent = '⏹ Η εγγραφή σταμάτησε';
      setTimeout(() => { status.classList.add('hidden'); }, 2000);
      return;
    }

    // START recording
    status.classList.remove('hidden');
    status.textContent = '🔴 Ηχογράφηση... (πάτα Stop για να σταματήσεις)';
    btn.innerHTML = '⏹ Stop';
    btn.classList.add('recording');

    const { promise, abort } = listenGreek(60000);  // 60 second timeout
    micAbort = abort;

    try {
      const spoken = await promise;
      const textarea = document.getElementById('ncf-voice-text');
      textarea.value = (textarea.value ? textarea.value + ' ' : '') + spoken;
      status.textContent = '✅ Ηχογραφήθηκε! Πάτα "Ανάλυση AI"';
    } catch (err) {
      status.textContent = '❌ ' + err.message;
    }
    micAbort = null;
    btn.innerHTML = '🎤 Μικρόφωνο';
    btn.classList.remove('recording');
  });

  document.getElementById('ncf-ai-btn').addEventListener('click', async () => {
    const text = document.getElementById('ncf-voice-text').value.trim();
    if (!text) { alert('Γράψε ή μίλα πρώτα στο πεδίο AI Voice'); return; }

    const btn = document.getElementById('ncf-ai-btn');
    const status = document.getElementById('ncf-ai-status');
    btn.disabled = true;
    btn.textContent = '⏳ Ανάλυση...';
    status.classList.remove('hidden');
    status.textContent = 'Το AI αναλύει...';

    try {
      const result = await post('/llm/intake', { text });
      if (result.fallback) {
        status.textContent = '⚠️ Το AI δεν ανταποκρίθηκε σωστά. Συμπλήρωσε χειροκίνητα.';
      } else {
        // Αυτόματη συμπλήρωση ΜΟΝΟ όσων βρήκε (τα υπόλοιπα τα συμπληρώνεις εσύ)
        let filled = 0;
        if (result.area) { document.getElementById('ncf-area').value = result.area; filled++; }
        if (result.alias_suggestion) { document.getElementById('ncf-alias').value = result.alias_suggestion; filled++; }
        if (result.need_categories?.length) { document.getElementById('ncf-cat').value = result.need_categories.join(', '); filled++; }
        if (result.freehand_summary) { document.getElementById('ncf-desc').value = result.freehand_summary; filled++; }
        // Source suggestion — ΜΟΝΟ αν ταίριαξε πραγματικά (δεν μαντεύει)
        if (result.source_suggestions?.length) {
          const bestSource = result.source_suggestions[0];
          const srcSelect = document.getElementById('ncf-source');
          for (const opt of srcSelect.options) {
            if (opt.value === bestSource._id) { opt.selected = true; break; }
          }
        }

        // Ερωτήσεις για ό,τι λείπει — ή υπενθύμιση να συμπληρώσεις μόνος
        const qs = result.followup_questions || [];
        let html = filled
          ? `✅ Συμπλήρωσα ${filled} πεδία από όσα είπες.`
          : '🤖 Δεν εντόπισα αρκετά στοιχεία — συμπλήρωσε παρακάτω.';
        if (qs.length) {
          html += `<br>❓ <strong>Λείπουν:</strong> ${qs.map(q => String(q).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')).join(' · ')}`;
          html += '<br><span class="text-muted">Απάντησέ τα χειροκίνητα στα πεδία.</span>';
        }
        html += '<br>Έλεγξε τα πεδία και πάτα «Δημιουργία».';
        status.innerHTML = html;

        // Focus στο πρώτο άδειο σημαντικό πεδίο
        if (!document.getElementById('ncf-alias').value) document.getElementById('ncf-alias').focus();
      }
    } catch (err) {
      status.textContent = '❌ Σφάλμα: ' + err.message;
    }
    btn.disabled = false;
    btn.textContent = '🤖 Ανάλυση AI →';
  });

  // ── Quick-add source when no sources exist ─────────────────────────
  const quickSourceBtn = document.getElementById('ncf-quick-source-btn');
  if (quickSourceBtn) {
    quickSourceBtn.addEventListener('click', async () => {
      const name = prompt('Όνομα πηγής (π.χ. "Βοήθεια στο Σπίτι Γαζίου"):');
      if (!name) return;
      const type = prompt('Τύπος (PHYSIO, HOMECARE_CREW, NURSE, PATIENT_ASSOC, DOCTOR, WORD_OF_MOUTH):', 'HOMECARE_CREW');
      if (!type) return;
      try {
        await post('/sources', { name, type, phone: '' });
        // Reload form
        closeModal();
        openNewCaseForm(onCreated);
      } catch (err) {
        alert('Σφάλμα: ' + err.message);
      }
    });
  }

  // ── SUBMIT ───────────────────────────────────────────────────────────
  // Ψαχτήρι πηγής
  import('./searchSelect.js').then(m => {
    m.makeSearchable(document.getElementById('ncf-source'), '🔍 Ψάξε πηγή...');
  });

  document.getElementById('new-case-full-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('ncf-error');
    const submitBtn = document.getElementById('ncf-submit-btn');
    errEl.classList.add('hidden');

    const sourceId = document.getElementById('ncf-source').value;

    const alias = document.getElementById('ncf-alias').value.trim();
    if (!alias) { errEl.textContent = 'Βάλε ψευδώνυμο'; errEl.classList.remove('hidden'); return; }

    submitBtn.disabled = true;
    submitBtn.textContent = '⏳ Δημιουργία...';

    try {
      // 1. Δημιουργία case
      const caseData = await post('/cases', {
        source_id: sourceId,
        alias: alias,
        area: document.getElementById('ncf-area').value.trim(),
        amka: document.getElementById('ncf-amka').value.trim(),
        need_categories: document.getElementById('ncf-cat').value.split(',').map(s => s.trim()).filter(Boolean),
        needs_freehand: document.getElementById('ncf-desc').value.trim(),
      });
      const caseId = caseData._id;

      // 2. Upload φωτογραφιών
      const token = localStorage.getItem('rxtrail_token');
      for (const photo of photoFiles) {
        const form = new FormData();
        form.append('file', photo);
        form.append('name', photo.name || 'Φωτογραφία');
        form.append('doc_type', 'PHOTO');
        const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
        const base = isLocal ? '' : '/api';
        await fetch(`${base}/documents/upload/${caseId}`, {
          method: 'POST', headers: { 'Authorization': `Bearer ${token}` }, body: form,
        }).catch(() => {});
      }

      // 3. Upload αρχείων PDF
      for (const { file, name } of docFiles) {
        const form = new FormData();
        form.append('file', file);
        form.append('name', name);
        form.append('doc_type', file.type === 'application/pdf' ? 'PRESCRIPTION' : 'DOCUMENT');
        await fetch(`${base}/documents/upload/${caseId}`, {
          method: 'POST', headers: { 'Authorization': `Bearer ${token}` }, body: form,
        }).catch(() => {});
      }

      // 4. Κλείσιμο & refresh
      closeModal();
      if (onCreated) onCreated(caseData);

      // Notify all views that cases changed (always, regardless of active view)
      window.dispatchEvent(new CustomEvent('agent-cases-changed'));

      // Refresh kanban αν είμαστε στο cases view
      const casesView = document.getElementById('view-cases');
      if (casesView?.classList.contains('active')) {
        const { renderCasesView } = await import('../views/cases.js');
        await renderCasesView(casesView);
      }
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
      submitBtn.disabled = false;
      submitBtn.textContent = '💾 Δημιουργία Περιστατικού';
    }
  });
}

// ── Helpers ────────────────────────────────────────────────────────────────

function es(s) { return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function formatSize(b) {
  if (!b) return '0 B';
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
  return (b / 1048576).toFixed(1) + ' MB';
}
