/**
 * RxTrail — Week planner with lock/unlock + calendar sync.
 */
import { get, post } from '../api.js';

let isLocked = false;

export async function renderWeekView(container) {
  container.innerHTML = `
    <div class="flex-between mb-2"><h3>📅 Εβδομάδα</h3>
      <div><button id="wk-refresh" class="btn-text btn-sm">⟳</button><button id="wk-lock" class="btn-primary btn-sm">🔒 Κλείδωσε</button></div>
    </div>
    <div id="wk-status" class="text-sm text-muted mb-2"></div>
    <div style="display:flex;gap:8px;overflow-x:auto;">${['mon','tue','wed','thu','fri'].map(d=>`
      <div class="kanban-col" style="flex:1;min-width:150px;" id="wk-${d}"><h3 id="wk-label-${d}"></h3><div id="wk-items-${d}"></div></div>
    `).join('')}</div>
  `;
  document.getElementById('wk-refresh').addEventListener('click',loadProposal);
  document.getElementById('wk-lock').addEventListener('click',toggleLock);
  await loadProposal();
}

async function loadProposal() {
  try {
    const [data, apptData] = await Promise.all([
      get('/week/proposal'),
      get(`/appointments/week?date=${new Date().toISOString().slice(0,10)}`),
    ]);
    const p = data.proposal;
    const byDay = apptData.by_day || {};

    const apptCount = (apptData.items || []).length;
    document.getElementById('wk-status').textContent=`${data.total_items} στάσεις · ${apptCount} ραντεβού · ${Object.keys(data.zone_assignments||{}).length} ζώνες`;

    const DAY_MAP = {0:'mon',1:'tue',2:'wed',3:'thu',4:'fri',5:'sat',6:'sun'};

    for (const d of ['mon','tue','wed','thu','fri']) {
      const dayIdx = ['mon','tue','wed','thu','fri'].indexOf(d);
      const day = p[d]||{label:d,items:[],locked:false};
      document.getElementById(`wk-label-${d}`).textContent=`${day.label} ${day.locked?'🔒':''}`;
      const items=day.items||[];

      // Get appointments for this day
      const appts = byDay[String(dayIdx)] || [];
      const apptCards = appts.map(a => `
        <div class="kanban-card" style="border-left:3px solid var(--c-primary);cursor:pointer;">
          <h4>📅 ${es(a.title)}</h4>
          <p class="text-sm">${new Date(a.at).toLocaleTimeString('el-GR',{hour:'2-digit',minute:'2-digit'})} · ${KIND_LABELS[a.kind]||a.kind}</p>
          ${a.location?`<p class="text-sm text-muted">📍 ${es(a.location)}</p>`:''}
        </div>
      `).join('');

      const caseCards = items.map(i=>`<div class="kanban-card" data-case-id="${i.case_id||''}" style="cursor:pointer;"><h4>${es(i.alias||i.kind)}</h4><p>${i.area||''} · ${i.kind||''}</p>${i.appointment_at?`<p class="text-sm text-success">📅 ${new Date(i.appointment_at).toLocaleDateString('el-GR')}</p>`:''}</div>`).join('');

      document.getElementById(`wk-items-${d}`).innerHTML =
        (apptCards + caseCards) || '<div class="text-sm text-muted" style="padding:10px;text-align:center;">—</div>';
    }

    // Check if any day is locked
    isLocked = Object.values(p).some(day => day.locked);
    updateLockButton();

    // Bind click handlers to open case detail
    document.querySelectorAll('.kanban-card[data-case-id]').forEach(card => {
      card.addEventListener('click', () => {
        const caseId = card.dataset.caseId;
        if (caseId) {
          import('./today.js').then(m => m.openCaseDetail).catch(() => {});
          // Fallback: use same pattern as today view
          openCaseDetailFromWeek(caseId);
        }
      });
    });
  } catch(err) { document.getElementById('wk-status').textContent='Σφάλμα: '+err.message; }
}

async function openCaseDetailFromWeek(caseId) {
  try {
    const { get } = await import('../api.js');
    const c = await get(`/cases/${caseId}`);
    const { openModal, closeModal } = await import('../app.js');

    const historyHtml = (c.status_history || []).slice(-10).map(h =>
      `<div class="text-sm"><strong>${h.to}</strong> <span class="text-muted">— ${new Date(h.at).toLocaleDateString('el-GR')}</span></div>`
    ).join('');

    const actionsHtml = (c.allowed_actions || []).map(a =>
      `<button class="btn-primary btn-sm transition-btn" data-action="${a}" style="margin:3px;">${al[a]||a}</button>`
    ).join('');

    const docModule = await import('../components/documents.js');

    openModal(`📋 ${c.alias || 'Περιστατικό'}`, `
      <span class="tag tag-${(c.status||'').toLowerCase()}">${c.status}</span>
      <div class="mt-2"><strong>Περιοχή:</strong> ${c.area||'—'} · <strong>Ανάγκες:</strong> ${(c.need_categories||[]).join(', ')||'—'}</div>
      <div class="mt-2"><strong>Εκτίμηση:</strong> ${c.est_monthly_value?c.est_monthly_value+'€':'—'}</div>
      ${c.doctor?`<div><strong>Γιατρός:</strong> ${c.doctor.name}</div>`:''}
      ${c.source?`<div><strong>Πηγή:</strong> ${c.source.name}</div>`:''}
      ${c.allowed_actions?.length?`<div class="mt-2" style="border-top:1px solid var(--c-border);padding-top:12px;"><strong>Ενέργειες:</strong><div class="mt-1">${actionsHtml}</div></div>`:''}
      <div id="doc-section-placeholder-${c._id}"></div>
      <details class="mt-2"><summary class="text-sm text-muted">Ιστορικό</summary><div class="mt-1">${historyHtml||'—'}</div></details>
      <p id="transition-error" class="error-msg hidden mt-2"></p>
    `);

    // Load docs
    const placeholder = document.getElementById(`doc-section-placeholder-${c._id}`);
    if (placeholder) {
      placeholder.innerHTML = docModule.renderDocSection(c._id);
      docModule.loadDocs(c._id);
    }

    // Bind transitions
    document.querySelectorAll('.transition-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const errEl = document.getElementById('transition-error');
        errEl.classList.add('hidden');
        const { transitionWithForm } = await import('../components/transitionForm.js');
        await transitionWithForm(c._id, btn.dataset.action,
          () => { closeModal(); loadProposal(); },
          (err) => { errEl.textContent = err.message; errEl.classList.remove('hidden'); });
      });
    });
  } catch(err) { alert(err.message); }
}

async function toggleLock() {
  try {
    if (isLocked) {
      await post('/week/unlock');
      isLocked = false;
    } else {
      await post('/week/lock');
      isLocked = true;
    }
    updateLockButton();
    await loadProposal();
  } catch(err) { alert(err.message); }
}

function updateLockButton() {
  const btn = document.getElementById('wk-lock');
  if (btn) {
    btn.textContent = isLocked ? '🔓 Ξεκλείδωσε' : '🔒 Κλείδωσε';
  }
}

async function lockWeek() { try { await post('/week/lock'); await loadProposal(); } catch(err) { alert(err.message); } }
function es(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
const al={assess:'✅ Αξιολόγηση',to_doctor:'👨‍⚕️ Σε γιατρό',prescribe:'📝 Γνωμάτευση',fulfil:'📦 Εκτέλεση',lose:'❌ Χάθηκε',close:'🏁 Έκλεισε',reactivate:'🔄 Επανενεργοποίηση'};
const KIND_LABELS={DOCTOR:'Γιατρός',CASE:'Περιστατικό',PERSONAL:'Προσωπικό',OTHER:'Άλλο'};
