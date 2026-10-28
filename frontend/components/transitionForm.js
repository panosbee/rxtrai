/**
 * RxTrail — Transition helper: opens a mini-form when the action needs data,
 * otherwise executes directly. Used by today.js, cases.js, plan.js.
 */
import { get, post, patch, del } from '../api.js';
import { openModal, closeModal } from '../app.js';

const NEED_CATEGORIES = ['WOUND','INCONT','CATHETER','OSTOMY','TRACHEO','OXYGEN','ENTERAL','ORTHO','HYALURONIC','ONCO_SUPPORT','ONS','URO','OTHER'];
const LOST_REASONS = ['WENT_TO_COMPETITOR','NOT_EXECUTED','NO_ANSWER','PATIENT_DECLINED','MEDICAL_CHANGE','PRICE','SERVICE','DOCTOR','OTHER'];
const CLOSED_REASONS = ['DECEASED','RECOVERED'];

/**
 * Execute a transition. If the action requires extra data, opens a form first.
 * @param {string} caseId
 * @param {string} action
 * @param {Function} onDone — called after successful transition (refresh views)
 * @param {Function} onError — optional
 */
export async function transitionWithForm(caseId, action, onDone, onError) {
  const requiresForm = {
    assess: 'Αξιολόγηση',
    to_doctor: 'Ανάθεση γιατρού',
    prescribe: 'Γνωμάτευση',
    fulfil: 'Εκτέλεση',
    lose: 'Χάθηκε',
    close: 'Έκλεισε',
  }[action];

  if (!requiresForm) {
    // Simple actions: execute directly
    try {
      await post(`/cases/${caseId}/transition`, { action, payload: {} });
      if (onDone) onDone();
    } catch (err) {
      if (onError) onError(err); else alert(err.message);
    }
    return;
  }

  // Build form per action
  let formHtml = '';
  if (action === 'assess') {
    formHtml = `
      <p class="text-sm text-muted mb-2">Τι ανάγκες έχει ο ασθενής; (επίλεξε όσα ισχύουν)</p>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;">
        ${NEED_CATEGORIES.map(c => `<label style="display:flex;align-items:center;gap:4px;background:var(--sea-mist);padding:6px 10px;border-radius:8px;font-size:13px;cursor:pointer;"><input type="checkbox" class="need-cat" value="${c}"> ${c}</label>`).join('')}
      </div>
      <div class="form-group"><label>Εκτίμηση μηνιαίας αξίας (€, προαιρετικά)</label><input type="number" id="assess-value" placeholder="π.χ. 250"></div>
      <div class="form-group"><label>Σημειώσεις (προαιρετικά)</label><textarea id="assess-notes" rows="2" placeholder="π.χ. κατάκλιση σταδίου ΙΙΙ, διαβήτης"></textarea></div>
    `;
  } else if (action === 'to_doctor') {
    formHtml = `
      <p class="text-sm text-muted mb-2">Διάλεξε τον γιατρό που θα γράψει τη γνωμάτευση.</p>
      <div class="form-group"><select id="to-doctor-select"><option value="">Φόρτωση γιατρών...</option></select></div>
      <div class="form-group"><label>Ημερομηνία ραντεβού (προαιρετικά)</label><input type="datetime-local" id="to-doctor-appt"></div>
    `;
  } else if (action === 'prescribe') {
    formHtml = `
      <p class="text-sm text-muted mb-2">Ήρθε η γνωμάτευση; Επίλεξε υπάρχουσα ή δημιούργησε νέα.</p>
      <div class="form-group"><label>Γνωμάτευση</label><select id="prescribe-select"><option value="">Φόρτωση...</option></select></div>
      <div id="prescribe-new-fields">
        <div class="form-group"><label>Γιατρός *</label><select id="prescribe-doctor"><option value="">— Επίλεξε γιατρό —</option></select></div>
        <div class="form-group"><label>Ημερομηνία γνωμάτευσης (προαιρετικά)</label><input type="date" id="prescribe-date"></div>
      </div>
    `;
  } else if (action === 'fulfil') {
    const todayStr = new Date().toISOString().slice(0, 10);
    formHtml = `
      <p class="text-sm text-muted mb-2">Καταχώρησε την εκτέλεση της γνωμάτευσης.</p>
      <div class="form-group"><label>Ημερομηνία εκτέλεσης</label><input type="date" id="fulfil-date" value="${todayStr}"></div>
      <div class="form-group"><label>Διάρκεια συνταγής (μέρες)</label><input type="number" id="fulfil-duration" value="30" min="1" max="365"></div>
      <div class="form-group"><label>Υπενθύμιση πριν τη λήξη (μέρες)</label><input type="number" id="fulfil-notify" value="2" min="0" max="30"></div>
      <p id="fulfil-reminder-hint" class="text-sm text-success mb-2"></p>
    `;
  } else if (action === 'lose') {
    formHtml = `
      <p class="text-sm text-muted mb-2">Γιατί χάθηκε το περιστατικό;</p>
      <div class="form-group"><select id="lose-reason">
        ${LOST_REASONS.map(r => `<option value="${r}">${r.replace(/_/g,' ')}</option>`).join('')}
      </select></div>
      <div class="form-group hidden" id="lose-competitor-wrap"><label>Όνομα ανταγωνιστή *</label><input id="lose-competitor" placeholder="π.χ. Μedipharma"></div>
      <div class="form-group"><label>Σημείωση (προαιρετικά)</label><input id="lose-note" placeholder="Τι έγινε;"></div>
    `;
  } else if (action === 'close') {
    formHtml = `
      <p class="text-sm text-muted mb-2">Γιατί έκλεισε το περιστατικό;</p>
      <div class="form-group"><select id="close-reason">
        ${CLOSED_REASONS.map(r => `<option value="${r}">${r === 'DECEASED' ? 'Απεβίωσε' : 'Ανάρρωσε'}</option>`).join('')}
      </select></div>
    `;
  }

  openModal(`📋 ${requiresForm}`, `
    <form id="transition-form">
      ${formHtml}
      <button type="submit" class="btn-primary full-width mt-2">💾 Ολοκλήρωση</button>
      <p id="transition-form-error" class="error-msg hidden mt-2"></p>
    </form>
  `);

  // Load doctors for to_doctor
  if (action === 'to_doctor') {
    try {
      const data = await get('/doctors?limit=200');
      const opts = (data.items || []).map(d => `<option value="${d._id}">${d.name} — ${d.specialty || ''}${d.area ? ' (' + d.area + ')' : ''}</option>`).join('');
      document.getElementById('to-doctor-select').innerHTML = '<option value="">— Επίλεξε γιατρό —</option>' + opts;
    } catch (e) { /* ignore */ }
    import('./searchSelect.js').then(m => {
      m.makeSearchable(document.getElementById('to-doctor-select'), '🔍 Ψάξε γιατρό (όνομα, ειδικότητα, περιοχή)...');
    });
  }

  // Load prescriptions + doctors for prescribe
  if (action === 'prescribe') {
    let caseData = null;
    let allRx = [];
    try { caseData = await get(`/cases/${caseId}`); } catch (e) { /* ignore */ }
    try {
      const data = await get('/prescriptions?limit=200');
      allRx = data.items || [];
    } catch (e) { /* ignore */ }

    const linkedIds = new Set((caseData && caseData.prescriptions || []).map(p => p._id));
    // Linked to this case + unlinked ones (e.g. uploaded photo without case)
    const candidates = allRx.filter(p => linkedIds.has(p._id) || !p.case_id);
    const rxLabel = p => {
      const d = p.issued_at || p.created_at;
      const date = d ? new Date(d).toLocaleDateString('el-GR') : 'χωρίς ημερομηνία';
      const isPhoto = p.photo_path || p.status === 'OCR_DONE' || p.status === 'OCR_PENDING';
      const state = p.status === 'OCR_DONE' ? ' · OCR έτοιμο' : (p.status === 'OCR_PENDING' ? ' · OCR σε εξέλιξη' : '');
      return `${isPhoto ? '📸 Φωτογραφία' : '📝 Γνωμάτευση'} ${date}${state}`;
    };
    const rxOpts = '<option value="">— Νέα καταχώρηση —</option>'
      + candidates.filter(p => linkedIds.has(p._id)).map(p => `<option value="${p._id}">${rxLabel(p)} (σε αυτό το περιστατικό)</option>`).join('')
      + candidates.filter(p => !linkedIds.has(p._id)).map(p => `<option value="${p._id}">${rxLabel(p)} (χωρίς περιστατικό)</option>`).join('');
    const sel = document.getElementById('prescribe-select');
    sel.innerHTML = rxOpts;
    const showNew = () => document.getElementById('prescribe-new-fields').classList.toggle('hidden', sel.value !== '');
    sel.addEventListener('change', showNew);
    showNew();
    try {
      const data = await get('/doctors?limit=200');
      const docs = (data.items || []).map(d => `<option value="${d._id}"${caseData && caseData.doctor_id === d._id ? ' selected' : ''}>${d.name} — ${d.specialty || ''}${d.area ? ' (' + d.area + ')' : ''}</option>`).join('');
      document.getElementById('prescribe-doctor').innerHTML = '<option value="">— Επίλεξε γιατρό —</option>' + docs;
    } catch (e) { /* ignore */ }
    import('./searchSelect.js').then(m => {
      m.makeSearchable(document.getElementById('prescribe-doctor'), '🔍 Ψάξε γιατρό...');
    });
  }

  // Competitor field toggle
  if (action === 'lose') {
    document.getElementById('lose-reason').addEventListener('change', (e) => {
      document.getElementById('lose-competitor-wrap').classList.toggle('hidden', e.target.value !== 'WENT_TO_COMPETITOR');
    });
  }

  // Fulfil: live reminder hint
  if (action === 'fulfil') {
    const updateHint = () => {
      const dateVal = document.getElementById('fulfil-date').value;
      const duration = parseInt(document.getElementById('fulfil-duration').value || '30', 10);
      const notify = parseInt(document.getElementById('fulfil-notify').value || '2', 10);
      const hint = document.getElementById('fulfil-reminder-hint');
      if (!hint) return;
      if (dateVal) {
        const d = new Date(dateVal + 'T12:00:00');
        d.setDate(d.getDate() + Math.max(0, duration - notify));
        hint.textContent = `🔁 Θα σου θυμίσω στις ${d.toLocaleDateString('el-GR')}`;
      } else {
        hint.textContent = '';
      }
    };
    ['fulfil-date', 'fulfil-duration', 'fulfil-notify'].forEach(id => {
      document.getElementById(id).addEventListener('input', updateHint);
    });
    updateHint();
  }

  document.getElementById('transition-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('transition-form-error');
    errEl.classList.add('hidden');

    const payload = {};
    if (action === 'assess') {
      const cats = Array.from(document.querySelectorAll('.need-cat:checked')).map(cb => cb.value);
      if (!cats.length) { errEl.textContent = 'Επίλεξε τουλάχιστον μία κατηγορία αναγκών.'; errEl.classList.remove('hidden'); return; }
      payload.need_categories = cats;
      const val = document.getElementById('assess-value').value;
      if (val) payload.est_monthly_value = parseFloat(val);
      const notes = document.getElementById('assess-notes').value.trim();
      if (notes) payload.needs_freehand = notes;
    } else if (action === 'to_doctor') {
      const docId = document.getElementById('to-doctor-select').value;
      if (!docId) { errEl.textContent = 'Επίλεξε γιατρό.'; errEl.classList.remove('hidden'); return; }
      payload.doctor_id = docId;
      const appt = document.getElementById('to-doctor-appt').value;
      if (appt) payload.doctor_appointment_at = new Date(appt).toISOString();
    } else if (action === 'prescribe') {
      let rxId = document.getElementById('prescribe-select').value;
      if (!rxId) {
        const docId = document.getElementById('prescribe-doctor').value;
        if (!docId) { errEl.textContent = 'Επίλεξε γιατρό για τη νέα γνωμάτευση.'; errEl.classList.remove('hidden'); return; }
        const dateVal = document.getElementById('prescribe-date').value;
        const rxBody = { case_id: caseId, doctor_id: docId };
        if (dateVal) rxBody.issued_at = new Date(dateVal + 'T12:00:00').toISOString();
        const created = await post('/prescriptions', rxBody);
        rxId = created._id;
      } else {
        // Link the selected (photo) prescription to this case if unlinked
        try { await patch(`/prescriptions/${rxId}`, { case_id: caseId }); } catch (e) { /* ignore */ }
      }
      payload.prescription_id = rxId;
    } else if (action === 'fulfil') {
      const dateVal = document.getElementById('fulfil-date').value;
      const duration = parseInt(document.getElementById('fulfil-duration').value || '30', 10);
      const notify = parseInt(document.getElementById('fulfil-notify').value || '2', 10);
      const fulfilledAt = dateVal ? new Date(dateVal + 'T12:00:00').toISOString() : new Date().toISOString();

      let caseData = null;
      try { caseData = await get(`/cases/${caseId}`); } catch (e) { /* ignore */ }

      const fulBody = {
        prescription_id: (caseData && caseData.prescription_id) || '',
        case_id: caseId,
        fulfilled_at: fulfilledAt,
      };
      if (caseData && caseData.doctor_id) fulBody.doctor_id = caseData.doctor_id;
      const created = await post('/fulfilments', fulBody);

      payload.fulfilment_id = created._id;
      payload.duration_days = duration;
      payload.notify_days = notify;
      payload.fulfilled_at = fulfilledAt;

      try {
        const updated = await post(`/cases/${caseId}/transition`, { action, payload });
        closeModal();
        const { showToast } = await import('../app.js');
        const nextDue = updated && updated.next_repeat_due
          ? new Date(updated.next_repeat_due).toLocaleDateString('el-GR')
          : '…';
        showToast(`📦 Εκτέλεση καταχωρήθηκε! Κύκλος #${updated.cycle_number || '?'} — επόμενη επανάληψη ${nextDue}`, 'success');
        if (onDone) onDone();
        return;
      } catch (err) {
        try { await del(`/fulfilments/${created._id}`); } catch (e) { /* ignore */ }
        if (/Ελάχιστο μεσοδιάστημα/.test(err.message || '')) {
          errEl.innerHTML = `<span>${err.message}</span>`;
          const forceBtn = document.createElement('button');
          forceBtn.type = 'button';
          forceBtn.className = 'btn-secondary btn-sm mt-1 full-width';
          forceBtn.textContent = '🔁 Έκανα δεύτερη εκτέλεση σήμερα — παράκαμψη';
          forceBtn.addEventListener('click', async () => {
            forceBtn.disabled = true;
            forceBtn.textContent = '⏳ …';
            try {
              const recreated = await post('/fulfilments', fulBody);
              const updated2 = await post(`/cases/${caseId}/transition`, {
                action,
                payload: { ...payload, fulfilment_id: recreated._id, force: true },
              });
              closeModal();
              const { showToast } = await import('../app.js');
              showToast(`📦 Εκτέλεση καταχωρήθηκε! Κύκλος #${updated2.cycle_number || '?'}`, 'success');
              if (onDone) onDone();
            } catch (err2) {
              try { await del(`/fulfilments/${recreated._id}`); } catch (e) { /* ignore */ }
              forceBtn.disabled = false;
              forceBtn.textContent = '🔁 Έκανα δεύτερη εκτέλεση σήμερα — παράκαμψη';
              errEl.textContent = err2.message;
            }
          });
          errEl.appendChild(forceBtn);
          errEl.classList.remove('hidden');
          return;
        }
        errEl.textContent = err.message;
        errEl.classList.remove('hidden');
        return;
      }
    } else if (action === 'lose') {
      const reason = document.getElementById('lose-reason').value;
      payload.lost_reason = reason;
      if (reason === 'WENT_TO_COMPETITOR') {
        const comp = document.getElementById('lose-competitor').value.trim();
        if (!comp) { errEl.textContent = 'Βάλε το όνομα του ανταγωνιστή.'; errEl.classList.remove('hidden'); return; }
        payload.lost_competitor = comp;
      }
      const note = document.getElementById('lose-note').value.trim();
      if (note) payload.lost_reason_text = note;
    } else if (action === 'close') {
      payload.closed_reason = document.getElementById('close-reason').value;
    }

    try {
      const updated = await post(`/cases/${caseId}/transition`, { action, payload });
      closeModal();
      if (action === 'fulfil') {
        const { showToast } = await import('../app.js');
        const nextDue = updated && updated.next_repeat_due
          ? new Date(updated.next_repeat_due).toLocaleDateString('el-GR')
          : '…';
        showToast(`📦 Εκτέλεση καταχωρήθηκε! Κύκλος #${updated.cycle_number || '?'} — επόμενη επανάληψη ${nextDue}`, 'success');
      }
      if (onDone) onDone();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}
