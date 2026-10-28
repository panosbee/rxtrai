/**
 * RxTrail — Today view: scored daily queue + quick actions + voice.
 * M2 — full implementation with TTS.
 */
import { get, post } from '../api.js';
import { openModal, closeModal } from '../app.js';
import { speakBrief, isSpeaking, stopSpeaking } from '../components/voice.js';

export async function renderTodayView(container) {
  container.innerHTML = `
    <div id="today-summary" class="mb-3" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;"></div>
    <div class="flex-between mb-2">
      <span class="text-sm text-muted" id="today-date"></span>
      <span class="text-sm" id="today-notif-count"></span>
    </div>
    <div id="today-queue"><div class="flex-center" style="padding:40px;"><div class="spinner" style="border-top-color:var(--c-primary);border-color:var(--c-border);"></div></div></div>
    <div id="today-empty-cta" class="hidden"></div>
  `;

  document.getElementById('today-date').textContent = new Date().toLocaleDateString('el-GR', {
    weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
  });

  await loadToday();
  loadSummary();
}

async function loadToday() {
  const container = document.getElementById('today-queue');
  try {
    const [queueData, notifCount] = await Promise.all([
      get('/queue/today'),
      get('/notifications/count'),
    ]);

    document.getElementById('today-notif-count').textContent = `🔔 ${notifCount.count} εκκρεμότητες`;

    const items = queueData.items || [];
    if (!items.length) {
      container.innerHTML = `
        <div class="empty-state">
          <span class="empty-illustration">🎯</span>
          <h3>Καμία εκκρεμότητα σήμερα!</h3>
          <p>Όλα είναι υπό έλεγχο. Τι θες να κάνεις;</p>
          <div class="action-chips" style="justify-content:center;">
            <button class="chip" onclick="document.querySelector('[data-view=plan]').click()">📅 Δες το πλάνο</button>
            <button class="chip" onclick="event.stopPropagation();var m=document.getElementById('fab-menu');var f=document.getElementById('fab');if(m.classList.contains('hidden')){m.classList.remove('hidden');f.classList.add('open');}else{m.classList.add('hidden');f.classList.remove('open');}">➕ Νέα καταχώρηση</button>
            <button class="chip" onclick="document.querySelector('[data-view=cases]').click()">📁 Περιστατικά</button>
          </div>
        </div>`;
      document.getElementById('today-empty-cta')?.classList.remove('hidden');
      return;
    }

    // Add "read all" button
    const readAllHtml = items.length > 0 ? `
      <button id="read-all-btn" class="btn-text btn-sm mb-2">🔊 Ανάγνωση όλων</button>
    ` : '';

    container.innerHTML = readAllHtml + items.map(item => {
      const statusClass = (item.kind === 'AT_RISK') ? 'qc-atrisk' :
                          (item.kind === 'REPEAT_DUE') ? 'qc-active' :
                          (item.kind === 'SLA_NEW') ? 'qc-new' : '';
      const score = item.final_score || item.score || 0;
      const valueBadge = item.est_monthly_value ? `<span class="badge-value">€${Math.round(item.est_monthly_value)}/μ</span>` : '';

      return `
      <div class="queue-card ${statusClass}" data-case-id="${item.case_id || ''}">
        <div class="qc-header">
          <span class="qc-title">${kindIcons[item.kind] || '📌'} ${escapeHtml(item.case_alias || kindLabels[item.kind] || item.kind)}</span>
          <span class="text-sm">${valueBadge}<span class="badge-score ${scoreTag(score)}">⚡ ${Math.round(score)}</span></span>
        </div>
        <div class="qc-meta">${kindLabels[item.kind] || item.kind}${item.case?.area ? ' · ' + escapeHtml(item.case.area) : ''}</div>
        ${item.why ? `<div class="qc-why">«${escapeHtml(item.why)}»</div>` : ''}
        <div class="qc-actions">
          ${item.case_id ? `<button class="btn-text btn-sm open-case-btn" data-case-id="${item.case_id}">📋 Άνοιγμα →</button>` : ''}
          ${(item.kind === 'REPEAT_DUE' || item.kind === 'AT_RISK') && item.case_id ? `<button class="btn-primary btn-sm repeat-btn" data-case-id="${item.case_id}" onclick="event.stopPropagation();window._quickRepeat('${item.case_id}')">🔁 Επανάληψη</button>` : ''}
          ${item.kind === 'CONTACT_DUE' && item.case_id ? `<button class="btn-secondary btn-sm contact-btn" data-case-id="${item.case_id}" onclick="event.stopPropagation();window._quickContact('${item.case_id}','NO_ANSWER')">📞 Δεν απάντησε</button>` : ''}
          <button class="icon-btn speak-item-btn" data-text="${escapeHtml(item.why || '')}" data-label="${kindLabels[item.kind] || ''}" title="Ανάγνωση" style="font-size:16px;width:36px;height:36px;">🔊</button>
        </div>
      </div>
    `}).join('');

    container.querySelectorAll('.open-case-btn').forEach(btn => {
      btn.addEventListener('click', (e) => { e.stopPropagation(); openCaseDetail(btn.dataset.caseId); });
    });

    // Speaker buttons — TTS for each queue item
    container.querySelectorAll('.speak-item-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (isSpeaking()) { stopSpeaking(); btn.textContent = '🔊'; return; }
        btn.textContent = '⏹';
        speakBrief(btn.dataset.text, btn.dataset.label).finally(() => { btn.textContent = '🔊'; });
      });
    });

    // Read-all button
    const readAllBtn = document.getElementById('read-all-btn');
    if (readAllBtn) {
      readAllBtn.addEventListener('click', async () => {
        if (isSpeaking()) { stopSpeaking(); readAllBtn.textContent = '🔊 Ανάγνωση όλων'; return; }
        readAllBtn.textContent = '⏹ Διακοπή';
        for (const item of items) {
          if (!isSpeaking()) break;
          await speakBrief(item.why || '', kindLabels[item.kind] || '');
        }
        readAllBtn.textContent = '🔊 Ανάγνωση όλων';
      });
    }

    container.querySelectorAll('.queue-item').forEach(card => {
      card.style.cursor = 'pointer';
      card.addEventListener('click', () => { const cid = card.dataset.caseId; if (cid) openCaseDetail(cid); });
    });
  } catch (err) {
    container.innerHTML = `<div class="list-empty text-danger">Σφάλμα: ${err.message}</div>`;
  }
}

// ── Dashboard summary in Today ──────────────────────────────────────────────

async function loadSummary() {
  try {
    const data = await get('/dashboard/month');
    const el = document.getElementById('today-summary');
    if (!el) return;
    el.innerHTML = `
      <div class="stat-card" onclick="document.querySelector('[data-view=cases]').click()" style="cursor:pointer;">
        <div class="stat-value" style="font-size:1.3rem;color:var(--c-success);">${data.active_cases}</div>
        <div class="stat-label">Ενεργά</div>
      </div>
      <div class="stat-card" onclick="document.querySelector('[data-view=cases]').click()" style="cursor:pointer;">
        <div class="stat-value" style="font-size:1.3rem;color:var(--c-danger);">${data.at_risk}</div>
        <div class="stat-label">Σε κίνδυνο</div>
      </div>
      <div class="stat-card" onclick="document.querySelector('[data-view=dashboard]').click()" style="cursor:pointer;">
        <div class="stat-value" style="font-size:1.3rem;">${data.monthly_revenue}€</div>
        <div class="stat-label">Τζίρος</div>
      </div>
    `;
  } catch (e) { /* silent — summary is bonus */ }
}

async function openCaseDetail(caseId) {
  try {
    const c = await get(`/cases/${caseId}`);
    renderCaseDetailModal(c);
  } catch (err) { alert('Σφάλμα: ' + err.message); }
}

function renderCaseDetailModal(c) {
  const historyHtml = (c.status_history || []).slice(-10).map(h => `
    <div class="text-sm"><strong>${h.to}</strong> <span class="text-muted">— ${new Date(h.at).toLocaleDateString('el-GR')}</span></div>
  `).join('');

  // Standard allowed actions
  const actionsHtml = (c.allowed_actions || []).map(a => `
    <button class="btn-primary btn-sm transition-btn" data-action="${a}" style="margin:3px;">${actionLabels[a] || a}</button>
  `).join('');

  // Extra actions (repeat, contact, lose-to-competitor)
  let extraActions = '';
  if (c.status === 'ACTIVE' && !c.no_repeat) {
    extraActions += `<button class="btn-primary btn-sm" onclick="event.stopPropagation();window._quickRepeat('${c._id}')" style="margin:3px;">🔁 Επανάληψη</button>`;
  }
  if (c.status === 'ACTIVE' || c.status === 'AT_RISK') {
    extraActions += `<button class="btn-secondary btn-sm" onclick="event.stopPropagation();window._quickContact('${c._id}','NO_ANSWER')" style="margin:3px;">📞 Δεν απάντησε</button>`;
  }
  if (c.status === 'PRESCRIBED') {
    extraActions += `<button class="btn-danger btn-sm" onclick="event.stopPropagation();window._loseRx('${c._id}','NOT_EXECUTED')" style="margin:3px;">📝 Δεν εκτελέστηκε</button>`;
    extraActions += `<button class="btn-danger btn-sm" onclick="event.stopPropagation();window._loseRx('${c._id}','WENT_TO_COMPETITOR')" style="margin:3px;">🏢 Πήγε αλλού</button>`;
  }
  if (c.status === 'AT_DOCTOR') {
    extraActions += `<button class="btn-secondary btn-sm" onclick="event.stopPropagation();window._reassignDoctor('${c._id}')" style="margin:3px;">🔄 Αλλαγή γιατρού</button>`;
  }

  // Contact timeline (REQ-06)
  const contacts = c.contact_attempts || [];
  const contactHtml = contacts.length ? `
    <details class="mt-2"><summary class="text-sm" style="cursor:pointer;color:var(--c-primary);">📞 Επικοινωνία (${contacts.length})</summary>
      <div class="mt-1" style="max-height:120px;overflow-y:auto;font-size:12px;">
        ${contacts.slice(-10).reverse().map(a => `
          <div style="padding:4px 0;border-bottom:1px solid var(--c-border-light);">
            ${new Date(a.at).toLocaleDateString('el-GR')} · ${a.channel} · <strong>${a.outcome}</strong>
            ${a.next_try_at ? ` · επόμενο: ${new Date(a.next_try_at).toLocaleDateString('el-GR')}` : ''}
          </div>
        `).join('')}
      </div>
    </details>
  ` : '';

  // Fulfilment timeline (ημερολόγιο εκτελέσεων)
  const fulfils = c.fulfilments || [];
  const fulfHtml = fulfils.length ? `
    <details class="mt-2"><summary class="text-sm" style="cursor:pointer;color:var(--c-primary);">📦 Εκτελέσεις (${fulfils.length})</summary>
      <div class="mt-1" style="max-height:120px;overflow-y:auto;font-size:12px;">
        ${fulfils.slice(-10).reverse().map(f => `
          <div style="padding:4px 0;border-bottom:1px solid var(--c-border-light);">
            ${new Date(f.fulfilled_at || f.created_at).toLocaleDateString('el-GR')} · Κύκλος #${f.cycle_number || '?'}
            ${f.total_revenue ? ` · <strong>€${f.total_revenue}</strong>` : ''}
          </div>
        `).join('')}
      </div>
    </details>
  ` : '';

  const prescCount = (c.prescriptions || []).length;
  const fulfCount = (c.fulfilments || []).length;
  const noRepeatBadge = c.no_repeat ? ' <span style="background:var(--st-new);color:#fff;font-size:11px;border-radius:3px;padding:1px 5px;">Εφάπαξ</span>' : '';

  openModal(`📋 ${c.alias || 'Περιστατικό'}`, `
    <span class="tag tag-${(c.status || '').toLowerCase()}">${c.status}${noRepeatBadge}</span>
    ${c.est_monthly_value ? `<span class="ml-2 text-sm"><strong>€${c.est_monthly_value}/μήνα</strong></span>` : ''}
    <div class="mt-2"><strong>Περιοχή:</strong> ${c.area || '—'}</div>
    <div class="mt-2"><strong>Ανάγκες:</strong> ${(c.need_categories || []).join(', ') || '—'}</div>
    ${c.doctor ? `<div class="mt-2"><strong>Γιατρός:</strong> ${c.doctor.name}</div>` : ''}
    ${c.source ? `<div class="mt-2"><strong>Πηγή:</strong> ${c.source.name}</div>` : ''}
    ${c.next_repeat_due ? `<div class="mt-2"><strong>Επόμενη επανάληψη:</strong> ${new Date(c.next_repeat_due).toLocaleDateString('el-GR')}</div>` : ''}
    ${c.cycle_number ? `<div class="mt-2"><strong>Κύκλος:</strong> #${c.cycle_number}</div>` : ''}
    <div class="mt-2 text-sm text-muted">📝 Γνωματεύσεις: ${prescCount} · 📦 Εκτελέσεις: ${fulfCount}</div>

    ${contactHtml}

    ${fulfHtml}

    ${(actionsHtml || extraActions) ? `<div class="mt-2" style="border-top:1px solid var(--c-border);padding-top:12px;"><strong>Ενέργειες:</strong><div class="mt-1">${actionsHtml}${extraActions}</div></div>` : ''}

    <div id="doc-section-placeholder-${c._id}"></div>

    <details class="mt-2"><summary class="text-sm text-muted" style="cursor:pointer;">Ιστορικό</summary><div class="mt-1">${historyHtml || '—'}</div></details>
    <p id="transition-error" class="error-msg hidden mt-2"></p>
  `);

  // Load document section
  import('../components/documents.js').then(m => {
    const placeholder = document.getElementById(`doc-section-placeholder-${c._id}`);
    if (placeholder) {
      placeholder.innerHTML = m.renderDocSection(c._id);
      m.loadDocs(c._id);
    }
  });

  document.querySelectorAll('.transition-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const { transitionWithForm } = await import('../components/transitionForm.js');
      await transitionWithForm(c._id, btn.dataset.action,
        () => {
          closeModal();
          setTimeout(() => openCaseDetail(c._id), 200);
          setTimeout(() => loadToday(), 500);
        },
        (err) => {
          const errEl = document.getElementById('transition-error');
          errEl.textContent = err.message;
          errEl.classList.remove('hidden');
        });
    });
  });
}

const kindIcons = { AT_RISK: '🚨', REPEAT_DUE: '🔁', SLA_NEW: '🆕', SLA_ASSESSED: '📋', SLA_PRESCRIBED: '📝', ACTION_DUE: '📌', VISIT_SUGGEST: '👋', CONTACT_DUE: '📞' };
const kindLabels = { AT_RISK: 'Σε κίνδυνο', REPEAT_DUE: 'Επανάληψη', SLA_NEW: 'Νέο (SLA)', SLA_ASSESSED: 'Αξιολογήθηκε (SLA)', SLA_PRESCRIBED: 'Γνωμάτευση (SLA)', ACTION_DUE: 'Εκκρεμότητα', VISIT_SUGGEST: 'Πρόταση επίσκεψης', CONTACT_DUE: 'Επικοινωνία' };
const actionLabels = { assess: '✅ Αξιολόγηση', to_doctor: '👨‍⚕️ Σε γιατρό', prescribe: '📝 Γνωμάτευση', fulfil: '📦 Εκτέλεση', lose: '❌ Χάθηκε', close: '🏁 Έκλεισε', reactivate: '🔄 Επανενεργοποίηση', reassign: '🔄 Αλλαγή γιατρού' };
function scoreTag(s) { return s >= 60 ? 'risk' : s >= 40 ? 'risk' : s >= 25 ? 'new' : 'active'; }

// ── Global quick actions (called via inline onclick) ───────────────────────

window._quickRepeat = async function(caseId) {
  const { post } = await import('../api.js');
  try {
    const result = await post(`/cases/${caseId}/repeat`, {});
    const { showToast } = await import('../app.js');
    showToast(`🔁 Επανάληψη ολοκληρώθηκε! Κύκλος #${result.cycle_number || '?'}`, 'success');
    setTimeout(() => loadToday(), 800);
  } catch (err) {
    const { showToast } = await import('../app.js');
    showToast(err.message, 'error');
  }
};

window._quickContact = async function(caseId, outcome) {
  const { post } = await import('../api.js');
  try {
    const next = new Date();
    next.setDate(next.getDate() + 2);
    await post(`/cases/${caseId}/contact`, {
      outcome,
      channel: 'CALL',
      next_try_at: next.toISOString(),
    });
    const { showToast } = await import('../app.js');
    showToast('📞 Καταχωρήθηκε — ξανά σε 2 μέρες', 'success');
    setTimeout(() => loadToday(), 500);
  } catch (err) {
    const { showToast } = await import('../app.js');
    showToast(err.message, 'error');
  }
};

window._loseRx = async function(caseId, reason) {
  const { post } = await import('../api.js');
  const competitor = reason === 'WENT_TO_COMPETITOR' ? prompt('Όνομα ανταγωνιστή:') : null;
  if (reason === 'WENT_TO_COMPETITOR' && !competitor) return;
  try {
    await post(`/cases/${caseId}/transition`, {
      action: 'lose',
      payload: { lost_reason: reason, lost_competitor: competitor || undefined }
    });
    const { showToast } = await import('../app.js');
    showToast('Καταχωρήθηκε ως χαμένο', 'warning');
    setTimeout(() => loadToday(), 500);
  } catch (err) {
    const { showToast } = await import('../app.js');
    showToast(err.message, 'error');
  }
};

window._reassignDoctor = async function(caseId) {
  const { post, get } = await import('../api.js');
  try {
    const doctors = await get('/doctors?limit=50');
    const options = doctors.items.map(d => `<option value="${d._id}">${d.name} · ${d.specialty || ''}</option>`).join('');
    const html = `<div class="form-group"><label>Νέος γιατρός (άφησε κενό για επιστροφή σε Αξιολόγηση)</label><select id="reassign-doctor"><option value="">— Επιστροφή σε Αξιολόγηση —</option>${options}</select></div><button id="do-reassign-btn" class="btn-primary btn-sm mt-2">🔄 Αλλαγή</button>`;
    const { openModal, closeModal } = await import('../app.js');
    openModal('🔄 Αλλαγή γιατρού', html);
    document.getElementById('do-reassign-btn').addEventListener('click', async () => {
      const newDocId = document.getElementById('reassign-doctor').value;
      await post(`/cases/${caseId}/transition`, {
        action: 'reassign',
        payload: { new_doctor_id: newDocId || null }
      });
      closeModal();
      const { showToast } = await import('../app.js');
      showToast('Ο γιατρός άλλαξε', 'success');
      setTimeout(() => loadToday(), 500);
    });
  } catch (err) {
    const { showToast } = await import('../app.js');
    showToast(err.message, 'error');
  }
};
function escapeHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
