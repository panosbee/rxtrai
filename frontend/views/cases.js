/**
 * RxTrail — Cases view: kanban + αναζήτηση (ΑΜΚΑ/όνομα) + πλήρης καρτέλα
 * με edit, σημειώσεις, πολλαπλές γνωματεύσεις, παράδοση.
 */
import { get, post, patch } from '../api.js';
import { openModal, closeModal } from '../app.js';

export async function renderCasesView(container) {
  container.innerHTML = `
    <div class="flex-between mb-2">
      <h3>Περιστατικά</h3>
      <button id="new-case-btn" class="btn-primary btn-sm">+ Νέο</button>
    </div>
    <div class="flex-between mb-2" style="gap:8px;">
      <input type="search" id="cases-search" placeholder="🔍 Αναζήτηση με ΑΜΚΑ ή όνομα..." class="form-group" style="flex:1;margin:0;">
      <button id="cases-search-clear" class="btn-text btn-sm hidden">✕ Καθαρισμός</button>
    </div>
    <div id="cases-kanban" class="kanban">${statusColumns.map(s => `
      <div class="kanban-col" data-status="${s}"><h3>${statusLabels[s]}</h3><div class="kanban-items" id="kanban-${s}">—</div></div>
    `).join('')}</div>
    <div id="cases-search-results" class="hidden"></div>
  `;
  document.getElementById('new-case-btn').addEventListener('click', () => {
    import('../components/newCaseForm.js').then(m => m.openNewCaseForm(() => loadKanban()));
  });

  const searchEl = document.getElementById('cases-search');
  searchEl.addEventListener('input', debounce(() => {
    const q = searchEl.value.trim();
    if (q.length >= 2) {
      document.getElementById('cases-search-clear').classList.remove('hidden');
      loadSearchResults(q);
    } else {
      document.getElementById('cases-search-clear').classList.add('hidden');
      document.getElementById('cases-search-results').classList.add('hidden');
      document.getElementById('cases-kanban').classList.remove('hidden');
    }
  }, 350));
  document.getElementById('cases-search-clear').addEventListener('click', () => {
    searchEl.value = '';
    document.getElementById('cases-search-clear').classList.add('hidden');
    document.getElementById('cases-search-results').classList.add('hidden');
    document.getElementById('cases-kanban').classList.remove('hidden');
  });

  await loadKanban();
}

async function loadSearchResults(q) {
  const resultsEl = document.getElementById('cases-search-results');
  const kanbanEl = document.getElementById('cases-kanban');
  try {
    const data = await get(`/cases?q=${encodeURIComponent(q)}&limit=50`);
    const items = data.items || [];
    kanbanEl.classList.add('hidden');
    resultsEl.classList.remove('hidden');
    if (!items.length) {
      resultsEl.innerHTML = '<div class="list-empty">Δεν βρέθηκαν περιστατικά με αυτό το ΑΜΚΑ/όνομα.</div>';
      return;
    }
    resultsEl.innerHTML = `
      <p class="text-sm text-muted mb-2">Βρέθηκαν ${data.total || items.length}:</p>
      ${items.map(c => `
        <div class="queue-card" data-case-id="${c._id}">
          <div class="qc-header">
            <span class="qc-title">${es(c.alias)}</span>
            <span class="status-chip status-${c.status}">${c.status}</span>
          </div>
          <div class="qc-meta">${es(c.area||'—')}${c.amka ? ' · ΑΜΚΑ: ' + es(c.amka) : ''}${c.doctor_name ? ' · ' + es(c.doctor_name) : ''}</div>
        </div>
      `).join('')}
    `;
    resultsEl.querySelectorAll('.queue-card').forEach(card => {
      card.addEventListener('click', () => openCaseDetail(card.dataset.caseId));
    });
  } catch (err) {
    resultsEl.innerHTML = `<div class="list-empty text-danger">${es(err.message)}</div>`;
  }
}

async function loadKanban() {
  try {
    const data = await get('/cases/kanban');
    for (const s of statusColumns) {
      const items = data[s] || [];
      const col = document.getElementById(`kanban-${s}`);
      if (!col) continue;
      if (!items.length) { col.innerHTML = '<div class="text-sm text-muted" style="padding:20px;text-align:center;">—</div>'; continue; }
      col.innerHTML = items.map(c => `
        <div class="kanban-card" data-case-id="${c._id}"><h4>${es(c.alias)}</h4><p>${c.area||''} · ${(c.need_categories||[]).slice(0,2).join(', ')}</p>${c.amka?`<p class="text-xs text-muted">ΑΜΚΑ: ${es(c.amka)}</p>`:''}${c.est_monthly_value?`<p class="text-success">${c.est_monthly_value}€</p>`:''}</div>
      `).join('');
    }
    document.querySelectorAll('.kanban-card').forEach(card => card.addEventListener('click', () => openCaseDetail(card.dataset.caseId)));
  } catch (err) { document.getElementById('cases-kanban').innerHTML = `<div class="list-empty text-danger">${err.message}</div>`; }
}

async function openCaseDetail(caseId) {
  try {
    const c = await get(`/cases/${caseId}`);
    const h = (c.status_history||[]).slice(-10).map(h=>`<div class="text-sm"><strong>${h.to}</strong> <span class="text-muted">— ${new Date(h.at).toLocaleDateString('el-GR')}</span></div>`).join('');
    const fulfs = (c.fulfilments||[]).slice(-10).reverse().map(f=>`<div class="text-sm">📦 ${new Date(f.fulfilled_at||f.created_at).toLocaleDateString('el-GR')} — κύκλος #${f.cycle_number||'?'}</div>`).join('');
    const a = (c.allowed_actions||[]).map(a=>`<button class="btn-primary btn-sm trans-btn" data-action="${a}" style="margin:3px;">${al[a]||a}</button>`).join('');

    // Γνωματεύσεις (πολλαπλές)
    const rxs = (c.prescriptions||[]);
    const rxList = rxs.length ? rxs.map(p => {
      const d = p.issued_at || p.created_at;
      const date = d ? new Date(d).toLocaleDateString('el-GR') : 'χωρίς ημερομηνία';
      const isPhoto = p.photo_path || p.status === 'OCR_DONE';
      const dr = p.doctor_name_raw || (c.doctor && c.doctor.name) || '';
      return `<div class="text-sm" style="padding:6px 0;border-bottom:1px solid var(--c-border-light);">
        ${isPhoto ? '📸' : '📝'} <strong>Γνωμάτευση ${date}</strong>${dr ? ' · ' + es(dr) : ''}${p.note ? `<br><span class="text-muted">${es(p.note).substring(0,120)}</span>` : ''}
      </div>`;
    }).join('') : '<div class="text-sm text-muted">Καμία γνωμάτευση ακόμα.</div>';

    // Σημειώσεις περιστατικού
    const caseNotes = (c.case_notes||[]);
    const notesList = caseNotes.length ? caseNotes.slice().reverse().map(n => `
      <div class="text-sm" style="padding:4px 0;border-bottom:1px solid var(--c-border-light);">
        <span class="text-faint" style="font-size:10.5px;">${new Date(n.at).toLocaleString('el-GR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}</span><br>${es(n.note)}
      </div>`).join('') : '<div class="text-sm text-muted">Καμία σημείωση.</div>';

    openModal(`📋 ${c.alias||'Περιστατικό'}`, `
      <span class="status-chip status-${(c.status||'').toLowerCase()}">${c.status}</span>
      <div class="mt-2"><strong>Περιοχή:</strong> ${c.area||'—'} · <strong>ΑΜΚΑ:</strong> ${c.amka||'—'} · <strong>Ανάγκες:</strong> ${(c.need_categories||[]).join(', ')||'—'}</div>
      <div class="mt-2"><strong>Εκτίμηση:</strong> ${c.est_monthly_value?c.est_monthly_value+'€':'—'} · <strong>Σημειώσεις:</strong> ${c.needs_freehand||'—'}</div>
      ${c.doctor?`<div><strong>Γιατρός:</strong> ${c.doctor.name}</div>`:''}
      ${c.source?`<div><strong>Πηγή:</strong> ${c.source.name}</div>`:''}

      <div class="mt-2" style="border-top:1px solid var(--c-border);padding-top:12px;">
        <div class="flex-between">
          <strong>💊 Γνωματεύσεις (${rxs.length})</strong>
          <button id="add-rx-btn" class="btn-text btn-sm">+ Προσθήκη γνωμάτευσης</button>
        </div>
        <div class="mt-1" style="max-height:160px;overflow-y:auto;">${rxList}</div>
      </div>

      <div class="mt-2" style="border-top:1px solid var(--c-border);padding-top:12px;">
        <div class="flex-between">
          <strong>📝 Σημειώσεις περιστατικού</strong>
          <button id="case-note-btn" class="btn-text btn-sm">+ Σημείωση</button>
        </div>
        <div class="mt-1" style="max-height:140px;overflow-y:auto;">${notesList}</div>
      </div>

      ${c.allowed_actions?.length?`<div class="mt-2" style="border-top:1px solid var(--c-border);padding-top:12px;"><strong>Ενέργειες:</strong><div class="mt-1">${a}</div></div>`:''}
      <div class="mt-2" style="border-top:1px solid var(--c-border);padding-top:12px;">
        <div class="flex-between" style="gap:6px;flex-wrap:wrap;">
          <button id="edit-case-btn" class="btn-secondary btn-sm">✏️ Επεξεργασία</button>
          <button id="handover-btn" class="btn-secondary btn-sm">🤝 Παράδοση σε συνάδελφο</button>
        </div>
        <span id="handover-result" class="text-sm"></span>
      </div>
      ${fulfs?`<details class="mt-2"><summary class="text-sm text-muted">📦 Εκτελέσεις</summary><div class="mt-1">${fulfs}</div></details>`:''}
      <div id="doc-section-placeholder-${c._id}"></div>
      <details class="mt-2"><summary class="text-sm text-muted">Ιστορικό</summary><div class="mt-1">${h||'—'}</div></details>
      <p id="case-error" class="error-msg hidden mt-2"></p>
    `);
    // Load docs
    import('../components/documents.js').then(m => {
      const ph = document.getElementById(`doc-section-placeholder-${c._id}`);
      if (ph) { ph.innerHTML = m.renderDocSection(c._id); m.loadDocs(c._id); }
    });
    document.querySelectorAll('.trans-btn').forEach(btn=>btn.addEventListener('click', async ()=>{
      const e=document.getElementById('case-error');e.classList.add('hidden');
      const { transitionWithForm } = await import('../components/transitionForm.js');
      await transitionWithForm(c._id, btn.dataset.action,
        () => { closeModal(); loadKanban(); },
        (err) => { e.textContent = err.message; e.classList.remove('hidden'); });
    }));

    // Παράδοση σε συνάδελφο
    document.getElementById('handover-btn')?.addEventListener('click', () => openHandover(c._id));
    // Επεξεργασία
    document.getElementById('edit-case-btn')?.addEventListener('click', () => openEditCase(c));
    // Σημείωση
    document.getElementById('case-note-btn')?.addEventListener('click', () => openCaseNote(c._id));
    // Νέα γνωμάτευση (2η, 3η...)
    document.getElementById('add-rx-btn')?.addEventListener('click', () => openAddPrescription(c));
  } catch(err){alert(err.message);}
}

// ── Επεξεργασία περιστατικού (οποιαδήποτε στιγμή) ─────────────────────────

function openEditCase(c) {
  openModal(`✏️ Επεξεργασία — ${c.alias||'Περιστατικό'}`, `
    <form id="edit-case-form">
      <div class="form-group"><label>Ψευδώνυμο</label><input id="ec-alias" value="${es(c.alias||'')}"></div>
      <div class="form-group"><label>Περιοχή</label><input id="ec-area" value="${es(c.area||'')}"></div>
      <div class="form-group"><label>ΑΜΚΑ</label><input id="ec-amka" value="${es(c.amka||'')}" placeholder="11 ψηφία"></div>
      <div class="form-group"><label>Κατηγορίες αναγκών (κόμμα)</label><input id="ec-cats" value="${es((c.need_categories||[]).join(', '))}"></div>
      <div class="form-group"><label>Εκτίμηση αξίας (€)</label><input type="number" id="ec-value" value="${c.est_monthly_value??''}"></div>
      <div class="form-group"><label>Περιγραφή / ανάγκες</label><textarea id="ec-needs" rows="3">${es(c.needs_freehand||'')}</textarea></div>
      <button type="submit" class="btn-primary full-width">💾 Αποθήκευση</button>
      <p id="ec-error" class="error-msg hidden mt-2"></p>
    </form>
  `);
  document.getElementById('edit-case-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('ec-error');
    errEl.classList.add('hidden');
    try {
      const { showToast } = await import('../app.js');
      const cats = document.getElementById('ec-cats').value.split(',').map(x => x.trim()).filter(Boolean);
      await patch(`/cases/${c._id}`, {
        alias: document.getElementById('ec-alias').value.trim() || c.alias,
        area: document.getElementById('ec-area').value.trim(),
        amka: document.getElementById('ec-amka').value.trim(),
        need_categories: cats,
        est_monthly_value: parseFloat(document.getElementById('ec-value').value) || null,
        needs_freehand: document.getElementById('ec-needs').value.trim(),
      });
      closeModal();
      showToast('✅ Το περιστατικό ενημερώθηκε', 'success');
      loadKanban();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

// ── Σημείωση περιστατικού ──────────────────────────────────────────────────

function openCaseNote(caseId) {
  openModal('📝 Σημείωση περιστατικού', `
    <p class="text-sm text-muted mb-2">Τι νέο έμαθες; (χρονολογείται αυτόματα και το γνωρίζει και ο Ασκληπιός)</p>
    <div class="form-group"><textarea id="cn-note" rows="4" placeholder="π.χ. Ο γιατρός έγραψε νέα συνταγή για άλλο πρόβλημα..."></textarea></div>
    <button id="cn-save" class="btn-primary full-width">💾 Αποθήκευση</button>
    <p id="cn-error" class="error-msg hidden mt-2"></p>
  `);
  document.getElementById('cn-save').addEventListener('click', async () => {
    const errEl = document.getElementById('cn-error');
    errEl.classList.add('hidden');
    const note = document.getElementById('cn-note').value.trim();
    if (!note) { errEl.textContent = 'Γράψε κάτι πρώτα'; errEl.classList.remove('hidden'); return; }
    try {
      const { showToast } = await import('../app.js');
      const r = await post(`/cases/${caseId}/note`, { note });
      closeModal();
      showToast('✅ ' + (r.message || 'Σημείωση αποθηκεύτηκε'), 'success');
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

// ── Νέα γνωμάτευση στο ίδιο περιστατικό (2η, 3η, ...) ─────────────────────

async function openAddPrescription(c) {
  let doctors = [];
  try { const d = await get('/doctors?limit=300'); doctors = d.items || []; } catch (e) {}
  const doctorOpts = doctors.map(d => `<option value="${d._id}">${es(d.name)} — ${d.specialty||''}${d.area?' ('+es(d.area)+')':''}</option>`).join('');
  openModal(`💊 Νέα γνωμάτευση — ${c.alias||''}`, `
    <p class="text-sm text-muted mb-2">Πρόσθεσε 2η/3η γνωμάτευση στο ΙΔΙΟ περιστατικό (π.χ. ο γιατρός έγραψε σήμερα για άλλο πρόβλημα). Η κατάσταση του περιστατικού δεν αλλάζει.</p>
    <div class="form-group"><label>Γιατρός</label><select id="ar-doctor"><option value="">— Κανένας/Άγνωστος —</option>${doctorOpts}</select></div>
    <div class="form-group"><label>Ημερομηνία γνωμάτευσης</label><input type="date" id="ar-date" value="${new Date().toISOString().slice(0,10)}"></div>
    <div class="form-group"><label>Σημείωση (τι αφορά)</label><textarea id="ar-note" rows="2" placeholder="π.χ. καθετήρας γαστροστομίας για άλλο πρόβλημα"></textarea></div>
    <button id="ar-save" class="btn-primary full-width">💾 Προσθήκη γνωμάτευσης</button>
    <p id="ar-error" class="error-msg hidden mt-2"></p>
  `);
  import('../components/searchSelect.js').then(m => {
    m.makeSearchable(document.getElementById('ar-doctor'), 'Ψάξε γιατρό...');
  });
  document.getElementById('ar-save').addEventListener('click', async () => {
    const errEl = document.getElementById('ar-error');
    errEl.classList.add('hidden');
    try {
      const { showToast } = await import('../app.js');
      const r = await post(`/cases/${c._id}/add-prescription`, {
        doctor_id: document.getElementById('ar-doctor').value,
        issued_at: document.getElementById('ar-date').value ? document.getElementById('ar-date').value + 'T12:00:00' : '',
        note: document.getElementById('ar-note').value.trim(),
      });
      closeModal();
      showToast('✅ ' + (r.message || 'Η γνωμάτευση προστέθηκε'), 'success');
      openCaseDetail(c._id);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

// ── Παράδοση ───────────────────────────────────────────────────────────────

async function openHandover(caseId) {
  const { showToast } = await import('../app.js');
  let colleagues = [];
  try {
    const data = await get('/auth/colleagues');
    colleagues = data.items || [];
  } catch (e) {
    showToast('❌ Δεν φόρτωσαν οι συνάδελφοι: ' + e.message, 'error');
    return;
  }
  if (!colleagues.length) {
    showToast('Δεν υπάρχουν άλλοι συνάδελφοι για παράδοση.', 'warning');
    return;
  }
  openModal('🤝 Παράδοση περιστατικού', `
    <p class="text-sm text-muted mb-2">Το περιστατικό μαζί με τον γιατρό, την πηγή, τα έγγραφα και τη γνώση του θα μεταφερθούν στον συνάδελφο. Θα πάρει ειδοποίηση.</p>
    <div class="form-group">
      <label>Συνάδελφος</label>
      <select id="ho-colleague">
        ${colleagues.map(c => `<option value="${es(c.email)}">${es(c.name || c.email)} (${es(c.email)})</option>`).join('')}
      </select>
    </div>
    <div class="form-group">
      <label>Σημείωση (προαιρετική)</label>
      <textarea id="ho-note" rows="2" placeholder="π.χ. Ασθενής στην Αθήνα, κοντά στο Ιπποκράτειο — πάρε τηλέφωνο την οικογένεια"></textarea>
    </div>
    <button id="ho-submit" class="btn-primary full-width">🤝 Παράδοση</button>
    <p id="ho-error" class="error-msg hidden mt-2"></p>
  `);
  document.getElementById('ho-submit').addEventListener('click', async () => {
    const errEl = document.getElementById('ho-error');
    errEl.classList.add('hidden');
    try {
      const email = document.getElementById('ho-colleague').value;
      const note = document.getElementById('ho-note').value.trim();
      const r = await post(`/cases/${caseId}/handover`, { target_user_email: email, note });
      closeModal();
      showToast('✅ ' + r.message, 'success');
      loadKanban();
    } catch (e) {
      errEl.textContent = e.message;
      errEl.classList.remove('hidden');
    }
  });
}

const statusColumns=['NEW','ASSESSED','AT_DOCTOR','PRESCRIBED','ACTIVE','AT_RISK'];
const statusLabels={NEW:'🆕 Νέο',ASSESSED:'✅ Αξιολογήθηκε',AT_DOCTOR:'👨‍⚕️ Σε γιατρό',PRESCRIBED:'📝 Γνωμάτευση',ACTIVE:'📦 Ενεργό',AT_RISK:'🚨 Σε κίνδυνο'};
const al={assess:'✅ Αξιολόγηση',to_doctor:'👨‍⚕️ Σε γιατρό',prescribe:'📝 Γνωμάτευση',fulfil:'📦 Εκτέλεση',lose:'❌ Χάθηκε',close:'🏁 Έκλεισε',reactivate:'🔄 Επανενεργοποίηση'};
function es(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
