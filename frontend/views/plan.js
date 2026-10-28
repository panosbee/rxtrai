/**
 * RxTrail — Plan view: Calendar (monthly) + Week planner (proposal/lock).
 * Merged into one unified planning tab.
 */
import { get, post, patch, del } from '../api.js';
import { openModal, closeModal } from '../app.js';

const DAY_NAMES = ['Δευ', 'Τρί', 'Τετ', 'Πέμ', 'Παρ', 'Σάβ', 'Κυρ'];
const MONTH_NAMES = ['Ιαν', 'Φεβ', 'Μάρ', 'Απρ', 'Μάι', 'Ιούν', 'Ιούλ', 'Αύγ', 'Σεπ', 'Οκτ', 'Νοέ', 'Δεκ'];
const KIND_ICONS = { DOCTOR: '👨‍⚕️', CASE: '📋', PERSONAL: '👤', OTHER: '📌' };
const KIND_LABELS = { DOCTOR: 'Γιατρός', CASE: 'Περιστατικό', PERSONAL: 'Προσωπικό', OTHER: 'Άλλο' };

let currentYear, currentMonth;
let appointmentsCache = {};
let isLocked = false;
let weekOffset = 0; // 0 = τρέχουσα εβδομάδα, -1/1 = προηγούμενη/επόμενη

export async function renderPlanView(container) {
  const now = new Date();
  currentYear = now.getFullYear();
  currentMonth = now.getMonth() + 1;

  container.innerHTML = `
    <!-- Calendar section -->
    <div class="flex-between mb-2">
      <div style="display:flex;align-items:center;gap:8px;">
        <button id="cal-prev" class="icon-btn">◀</button>
        <h3 id="cal-title" style="font-size:15px;"></h3>
        <button id="cal-next" class="icon-btn">▶</button>
      </div>
      <button id="cal-add-btn" class="btn-primary btn-sm">+ Ραντεβού</button>
    </div>
    <div id="cal-grid" style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px;margin-bottom:16px;"></div>
    <div id="cal-day-detail" style="display:none;margin-bottom:12px;"></div>

    <!-- Week planner section -->
    <div class="flex-between mb-2" style="border-top:1px solid var(--c-border);padding-top:12px;">
      <div style="display:flex;align-items:center;gap:8px;">
        <h3>📅 Εβδομάδα</h3>
        <button id="wk-prev" class="icon-btn" style="width:32px;height:32px;" title="Προηγούμενη εβδομάδα">◀</button>
        <span id="wk-range" class="text-sm text-muted"></span>
        <button id="wk-next" class="icon-btn" style="width:32px;height:32px;" title="Επόμενη εβδομάδα">▶</button>
      </div>
      <div>
        <button id="wk-refresh" class="btn-text btn-sm">⟳</button>
        <button id="wk-lock" class="btn-primary btn-sm">🔒 Κλείδωσε</button>
      </div>
    </div>
    <div id="wk-status" class="text-sm text-muted mb-2"></div>
    <div style="display:flex;gap:6px;overflow-x:auto;">${['mon','tue','wed','thu','fri','sat','sun'].map(d=>`
      <div class="kanban-col" style="flex:1;min-width:110px;" id="wk-${d}">
        <h3 id="wk-label-${d}" style="text-align:center;font-size:12px;"></h3>
        <div id="wk-items-${d}"></div>
      </div>
    `).join('')}</div>
  `;

  // Calendar events
  document.getElementById('cal-prev').addEventListener('click', () => { currentMonth--; if(currentMonth<1){currentMonth=12;currentYear--;} renderMonth(); });
  document.getElementById('cal-next').addEventListener('click', () => { currentMonth++; if(currentMonth>12){currentMonth=1;currentYear++;} renderMonth(); });
  document.getElementById('cal-add-btn').addEventListener('click', () => openAppointmentForm());

  // Week planner events
  document.getElementById('wk-refresh').addEventListener('click', loadProposal);
  document.getElementById('wk-lock').addEventListener('click', toggleLock);
  document.getElementById('wk-prev').addEventListener('click', () => { weekOffset--; loadProposal(); });
  document.getElementById('wk-next').addEventListener('click', () => { weekOffset++; loadProposal(); });

  await Promise.all([renderMonth(), loadProposal()]);
}

// ── Calendar ────────────────────────────────────────────────────────────────

async function renderMonth() {
  document.getElementById('cal-title').textContent = `${MONTH_NAMES[currentMonth-1]} ${currentYear}`;

  try {
    const data = await get(`/appointments/month/${currentYear}/${currentMonth}`);
    appointmentsCache = {};
    for (const a of (data.items || [])) {
      const day = new Date(a.at).getDate();
      if (!appointmentsCache[day]) appointmentsCache[day] = [];
      appointmentsCache[day].push(a);
    }
  } catch (e) { /* empty */ }

  const grid = document.getElementById('cal-grid');
  const firstDay = new Date(currentYear, currentMonth - 1, 1);
  const lastDay = new Date(currentYear, currentMonth, 0);
  const startDow = (firstDay.getDay() + 6) % 7;
  const totalDays = lastDay.getDate();
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${today.getMonth()+1}-${today.getDate()}`;

  let html = '';
  for (const d of DAY_NAMES) {
    html += `<div style="text-align:center;font-weight:600;font-size:10px;padding:3px;color:var(--c-text-secondary);">${d}</div>`;
  }
  for (let i = 0; i < startDow; i++) {
    html += '<div style="min-height:50px;background:var(--c-bg);border-radius:4px;"></div>';
  }
  for (let day = 1; day <= totalDays; day++) {
    const dateStr = `${currentYear}-${String(currentMonth).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
    const isToday = dateStr === todayStr;
    const apps = appointmentsCache[day] || [];
    const dots = apps.length ? apps.slice(0,3).map(a =>
      `<span style="font-size:8px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--c-primary);">${KIND_ICONS[a.kind]||''} ${es(a.title||'').substring(0,8)}</span>`
    ).join('') : '';

    html += `<div class="cal-day ${isToday?'cal-today':''}" data-date="${dateStr}"
      style="min-height:50px;padding:3px;border-radius:5px;cursor:pointer;background:${isToday?'var(--c-primary-light)':'var(--c-surface)'};border:1px solid var(--c-border-light);font-size:11px;">
      <strong style="font-size:12px;">${day}</strong>
      ${dots}
      ${apps.length > 3 ? `<span style="font-size:8px;color:var(--c-text-muted);">+${apps.length-3}</span>` : ''}
    </div>`;
  }
  grid.innerHTML = html;

  grid.querySelectorAll('.cal-day').forEach(cell => {
    cell.addEventListener('click', () => showDayDetail(cell.dataset.date));
  });
}

function showDayDetail(dateStr) {
  const apps = appointmentsCache[parseInt(dateStr.split('-')[2])] || [];
  const detail = document.getElementById('cal-day-detail');
  detail.style.display = 'block';

  if (!apps.length) {
    detail.innerHTML = `<div class="card"><p class="text-sm">Κανένα ραντεβού για ${new Date(dateStr).toLocaleDateString('el-GR')}</p>
      <button class="btn-primary btn-sm mt-1" id="cal-add-empty-btn">+ Ραντεβού</button></div>`;
    document.getElementById('cal-add-empty-btn')?.addEventListener('click', () => openAppointmentForm(dateStr));
    return;
  }

  detail.innerHTML = `
    <h4 style="margin-bottom:8px;">📅 ${new Date(dateStr).toLocaleDateString('el-GR', {weekday:'long', day:'numeric', month:'long'})}</h4>
    ${apps.map(a => `
      <div class="card" style="padding:10px;margin-bottom:6px;">
        <div class="flex-between">
          <div>
            <strong>${KIND_ICONS[a.kind]||''} ${es(a.title)}</strong>
            <div class="text-sm text-muted">${new Date(a.at).toLocaleTimeString('el-GR',{hour:'2-digit',minute:'2-digit'})}${a.end_at?' - '+new Date(a.end_at).toLocaleTimeString('el-GR',{hour:'2-digit',minute:'2-digit'}):''} · ${KIND_LABELS[a.kind]||a.kind}</div>
            ${a.location?`<div class="text-sm">📍 ${es(a.location)}</div>`:''}
            ${a.notes?`<div class="text-sm text-muted">📝 ${es(a.notes)}</div>`:''}
          </div>
          <div style="display:flex;gap:4px;">
            <button class="icon-btn cal-note-btn" data-id="${a._id}" title="📝 Σημείωση — τι έμαθες" style="font-size:14px;">📝</button>
            <button class="icon-btn edit-appt-btn" data-id="${a._id}" title="Επεξεργασία" style="font-size:14px;">✏️</button>
            <button class="icon-btn delete-appt-btn" data-id="${a._id}" title="Διαγραφή" style="font-size:14px;">🗑️</button>
          </div>
        </div>
      </div>
    `).join('')}
    <button id="cal-add-day-btn" class="btn-primary btn-sm mt-1">+ Ραντεβού</button>
  `;

  detail.querySelector('#cal-add-day-btn')?.addEventListener('click', () => openAppointmentForm(dateStr));
  detail.querySelectorAll('.edit-appt-btn').forEach(b => b.addEventListener('click', () => {
    const appt = apps.find(a => a._id === b.dataset.id);
    if (appt) openAppointmentForm(dateStr, appt);
  }));
  detail.querySelectorAll('.cal-note-btn').forEach(b => b.addEventListener('click', () => {
    openAppointmentNote(b.dataset.id);
  }));
  detail.querySelectorAll('.delete-appt-btn').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Διαγραφή ραντεβού;')) return;
    try {
      await del(`/appointments/${b.dataset.id}`);
    } catch (err) {
      if (!/404|δεν βρέθηκε|not found/i.test(err.message || '')) {
        alert('Σφάλμα διαγραφής: ' + err.message);
        return;
      }
      // 404 = ήδη σβησμένο — προχώρα σε refresh
    }
    await Promise.all([renderMonth(), loadProposal()]);
    showDayDetail(dateStr);
  }));
}

// ── Appointment form ────────────────────────────────────────────────────────

async function openAppointmentForm(prefillDate = '', editAppt = null) {
  let doctors = [];
  try { const d = await get('/doctors?limit=200'); doctors = d.items || []; } catch(e){}
  const doctorOpts = doctors.map(d => `<option value="${d._id}" ${editAppt?.doctor_id===d._id?'selected':''}>${es(d.name)} (${d.specialty||''})</option>`).join('');

  const title = editAppt ? '✏️ Επεξεργασία Ραντεβού' : '🆕 Νέο Ραντεβού';
  
  // Use LOCAL time for datetime-local input (not UTC ISO)
  const dateVal = editAppt ? _toLocalDatetimeStr(new Date(editAppt.at)) : (prefillDate ? prefillDate + 'T09:00' : '');
  const endVal = editAppt?.end_at ? _toLocalDatetimeStr(new Date(editAppt.end_at)) : '';

  openModal(title, `
    <form id="appt-form">
      <div class="form-group"><label>Τίτλος *</label><input id="af-title" value="${es(editAppt?.title||'')}" required></div>
      <div class="form-group"><label>Τύπος</label><select id="af-kind">
        ${['DOCTOR','CASE','PERSONAL','OTHER'].map(k => `<option value="${k}" ${editAppt?.kind===k?'selected':''}>${KIND_LABELS[k]}</option>`).join('')}
      </select></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <div class="form-group"><label>Από *</label><input type="datetime-local" id="af-at" value="${dateVal}" required></div>
        <div class="form-group"><label>Έως</label><input type="datetime-local" id="af-end" value="${endVal}"></div>
      </div>
      <div class="form-group"><label>Γιατρός</label><select id="af-doctor"><option value="">— Κανένας —</option>${doctorOpts}</select></div>
      <div class="form-group"><label>Τοποθεσία</label><input id="af-location" value="${es(editAppt?.location||'')}"></div>
      <div class="form-group"><label>Σημειώσεις</label><textarea id="af-notes" rows="2">${es(editAppt?.notes||'')}</textarea></div>
      <div id="af-conflicts" class="hidden" style="background:var(--c-danger-light);padding:8px;border-radius:var(--radius);margin-bottom:8px;"></div>
      <button type="submit" class="btn-primary full-width">💾 Αποθήκευση</button>
      <p id="af-error" class="error-msg hidden mt-2"></p>
    </form>
  `);

  // Ψαχτήρι γιατρού (όταν είναι πολλοί)
  import('../components/searchSelect.js').then(m => {
    m.makeSearchable(document.getElementById('af-doctor'), '🔍 Ψάξε γιατρό (όνομα, ειδικότητα, περιοχή)...');
  });

  document.getElementById('appt-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('af-error');
    errEl.classList.add('hidden');

    const payload = {
      title: document.getElementById('af-title').value.trim(),
      kind: document.getElementById('af-kind').value,
      at: new Date(document.getElementById('af-at').value).toISOString(),
      end_at: document.getElementById('af-end').value ? new Date(document.getElementById('af-end').value).toISOString() : null,
      doctor_id: document.getElementById('af-doctor').value || null,
      location: document.getElementById('af-location').value.trim(),
      notes: document.getElementById('af-notes').value.trim(),
    };

    if (!payload.title) { errEl.textContent = 'Βάλε τίτλο'; errEl.classList.remove('hidden'); return; }

    try {
      let result;
      if (editAppt) {
        result = await patch(`/appointments/${editAppt._id}`, payload);
      } else {
        result = await post('/appointments', payload);
      }

      if (result.has_conflict) {
        const conflictDiv = document.getElementById('af-conflicts');
        conflictDiv.innerHTML = `<strong>⚠️ Προσοχή — conflicts:</strong><br>${
          (result.conflicts||[]).map(c => `· ${c.title} (${new Date(c.at).toLocaleTimeString('el-GR',{hour:'2-digit',minute:'2-digit'})})`).join('<br>')
        }`;
        conflictDiv.classList.remove('hidden');
        return;
      }

      closeModal();
      // Πήγαινε την εβδομάδα στην εβδομάδα του ραντεβού (δεμένα ημερολόγιο-εβδομάδα)
      if (!editAppt && payload.at) {
        const _mondayOf = (d) => { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); x.setHours(0, 0, 0, 0); return x; };
        weekOffset = Math.round((_mondayOf(new Date(payload.at)) - _mondayOf(new Date())) / (7 * 86400000));
      }
      // Συγχρονισμός: και ημερολόγιο ΚΑΙ εβδομάδα (είναι δεμένα)
      await Promise.all([renderMonth(), loadProposal()]);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

// ── Week planner ────────────────────────────────────────────────────────────

async function loadProposal() {
  try {
    // Ημερομηνία βάσης = τρέχουσα εβδομάδα ± offset
    const base = new Date();
    base.setDate(base.getDate() + weekOffset * 7);
    const baseIso = base.toISOString().slice(0, 10);

    // Load appointments first (always needed)
    const apptData = await get(`/appointments/week?date=${baseIso}`);
    const byDay = apptData.by_day || {};
    _weekAppts = apptData.items || [];  // Cache for click handlers
    const apptCount = _weekAppts.length;

    // Πρόταση μόνο για την τρέχουσα εβδομάδα (το proposal είναι βάσει "σήμερα")
    let p = {mon:{label:'Δευτέρα',items:[],locked:false},tue:{label:'Τρίτη',items:[],locked:false},wed:{label:'Τετάρτη',items:[],locked:false},thu:{label:'Πέμπτη',items:[],locked:false},fri:{label:'Παρασκευή',items:[],locked:false},sat:{label:'Σάββατο',items:[],locked:false},sun:{label:'Κυριακή',items:[],locked:false}};
    let totalItems = apptCount;
    if (weekOffset === 0) {
      try {
        const data = await get('/week/proposal');
        p = data.proposal || p;
        totalItems = data.total_items || apptCount;
      } catch (e) {
        // Week proposal not available yet — just show appointments
        console.warn('Week proposal unavailable:', e.message);
      }
    }

    // Εύρος εβδομάδας για το label
    const monday = new Date(base);
    const dow = (monday.getDay() + 6) % 7;
    monday.setDate(monday.getDate() - dow);
    const sunday = new Date(monday);
    sunday.setDate(sunday.getDate() + 6);
    const rangeEl = document.getElementById('wk-range');
    if (rangeEl) {
      rangeEl.textContent = `${monday.getDate()}/${monday.getMonth()+1} – ${sunday.getDate()}/${sunday.getMonth()+1}`;
      rangeEl.style.fontWeight = weekOffset === 0 ? '700' : 'normal';
    }

    document.getElementById('wk-status').textContent = `${totalItems} στάσεις · ${apptCount} ραντεβού`;

    for (const d of ['mon','tue','wed','thu','fri','sat','sun']) {
      const dayIdx = ['mon','tue','wed','thu','fri','sat','sun'].indexOf(d);
      const day = p[d]||{label:d,items:[],locked:false};
      document.getElementById(`wk-label-${d}`).textContent = `${day.label} ${day.locked?'🔒':''}`;
      const items = day.items||[];

      const appts = byDay[String(dayIdx)] || [];
      const apptCards = appts.map(a => `
        <div class="kanban-card" data-appt-id="${a._id||''}" style="border-left:3px solid var(--c-primary);cursor:pointer;position:relative;">
          <div style="position:absolute;top:4px;right:4px;display:flex;gap:2px;">
            <button class="appt-note-btn" data-appt-id="${a._id||''}" title="📝 Σημείωση — τι έμαθες (τη γνωρίζει και η ΤΝ)" style="background:none;border:none;font-size:14px;cursor:pointer;opacity:.7;">📝</button>
            <button class="appt-delete-btn" data-appt-id="${a._id||''}" title="Διαγραφή ραντεβού" style="background:none;border:none;font-size:14px;cursor:pointer;opacity:.6;">✕</button>
          </div>
          <h4 style="margin-right:40px;">📅 ${es(a.title)}</h4>
          <p class="text-sm">${new Date(a.at).toLocaleTimeString('el-GR',{hour:'2-digit',minute:'2-digit'})} · ${KIND_LABELS[a.kind]||a.kind}</p>
          ${a.location?`<p class="text-sm text-muted">📍 ${es(a.location)}</p>`:''}
          ${a.doctor_name?`<p class="text-sm">👨‍⚕️ ${es(a.doctor_name)}</p>`:''}
          ${a.case_alias?`<p class="text-sm">📋 ${es(a.case_alias)}</p>`:''}
          ${a.notes?`<p class="text-sm text-muted" style="white-space:pre-wrap;word-break:break-word;">📝 ${es(a.notes)}</p>`:''}
          ${(a.note_history||[]).length>1?`<p class="text-faint" style="font-size:10px;">🕑 ${a.note_history.length} σημειώσεις</p>`:''}
        </div>
      `).join('');

      const caseCards = items.map(i => `
        <div class="kanban-card" data-case-id="${i.case_id||''}" style="cursor:pointer;border-left:3px solid var(--st-atrisk);opacity:.92;" onclick="event.stopPropagation();">
          <h4>${es(i.alias||i.kind)}</h4>
          <p>${i.area||''} · ${i.kind||''}</p>
          ${i.appointment_at?`<p class="text-sm text-success">📅 ${new Date(i.appointment_at).toLocaleDateString('el-GR')}</p>`:''}
          <p class="text-sm text-faint" style="margin-top:4px;font-size:10.5px;">📋 πρόταση από περιστατικό — άνοιξέ το για ενέργειες</p>
        </div>
      `).join('');

      document.getElementById(`wk-items-${d}`).innerHTML =
        (apptCards + caseCards) || '<div class="text-sm text-muted" style="padding:10px;text-align:center;">—</div>';
    }

    isLocked = Object.values(p).some(day => day.locked);
    updateLockButton();

    // Click handlers for appointment cards
    document.querySelectorAll('.kanban-card[data-appt-id]').forEach(card => {
      card.addEventListener('click', () => {
        const apptId = card.dataset.apptId;
        if (apptId) openAppointmentDetail(apptId);
      });
    });

    // 📝 Σημείωση ραντεβού (τι έμαθες — επεισοδιακή μνήμη για την ΤΝ)
    document.querySelectorAll('.appt-note-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const apptId = btn.dataset.apptId;
        if (!apptId) return;
        await openAppointmentNote(apptId);
      });
    });

    // Delete buttons for appointments
    document.querySelectorAll('.appt-delete-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const apptId = btn.dataset.apptId;
        if (!apptId) return;
        if (!confirm('Διαγραφή αυτού του ραντεβού;')) return;
        try {
          await del(`/appointments/${apptId}`);
        } catch (err) {
          // 404 = ήδη σβησμένο — συνέχισε κανονικά με refresh
          if (!/404|δεν βρέθηκε|not found/i.test(err.message || '')) {
            alert('Σφάλμα διαγραφής: ' + err.message);
            return;
          }
        }
        // Refresh ΚΑΙ εβδομάδα ΚΑΙ ημερολόγιο (συγχρονισμός)
        await Promise.all([loadProposal(), renderMonth()]);
      });
    });

    // Click handlers for case cards
    document.querySelectorAll('.kanban-card[data-case-id]').forEach(card => {
      card.addEventListener('click', () => {
        const caseId = card.dataset.caseId;
        if (caseId) openCaseDetail(caseId);
      });
    });
  } catch(err) { document.getElementById('wk-status').textContent = 'Σφάλμα: '+err.message; }
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
  if (btn) btn.textContent = isLocked ? '🔓 Ξεκλείδωσε' : '🔒 Κλείδωσε';
}

async function openCaseDetail(caseId) {
  try {
    const c = await get(`/cases/${caseId}`);
    const { openModal: om, closeModal: cm } = await import('../app.js');

    const historyHtml = (c.status_history || []).slice(-10).map(h =>
      `<div class="text-sm"><strong>${h.to}</strong> <span class="text-muted">— ${new Date(h.at).toLocaleDateString('el-GR')}</span></div>`
    ).join('');

    const actionsHtml = (c.allowed_actions || []).map(a =>
      `<button class="btn-primary btn-sm transition-btn" data-action="${a}" style="margin:3px;">${al[a]||a}</button>`
    ).join('');

    om(`📋 ${c.alias || 'Περιστατικό'}`, `
      <span class="tag tag-${(c.status||'').toLowerCase()}">${c.status}</span>
      <div class="mt-2"><strong>Περιοχή:</strong> ${c.area||'—'} · <strong>Ανάγκες:</strong> ${(c.need_categories||[]).join(', ')||'—'}</div>
      <div class="mt-2"><strong>Εκτίμηση:</strong> ${c.est_monthly_value?c.est_monthly_value+'€':'—'}</div>
      ${c.doctor?`<div><strong>Γιατρός:</strong> ${c.doctor.name}</div>`:''}
      ${c.source?`<div><strong>Πηγή:</strong> ${c.source.name}</div>`:''}
      ${c.allowed_actions?.length?`<div class="mt-2" style="border-top:1px solid var(--c-border);padding-top:12px;"><strong>Ενέργειες:</strong><div class="mt-1">${actionsHtml}</div></div>`:''}
      <details class="mt-2"><summary class="text-sm text-muted">Ιστορικό</summary><div class="mt-1">${historyHtml||'—'}</div></details>
      <p id="transition-error" class="error-msg hidden mt-2"></p>
    `);

    document.querySelectorAll('.transition-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const errEl = document.getElementById('transition-error');
        errEl.classList.add('hidden');
        const { transitionWithForm } = await import('../components/transitionForm.js');
        await transitionWithForm(c._id, btn.dataset.action,
          () => { cm(); loadProposal(); },
          (err) => { errEl.textContent = err.message; errEl.classList.remove('hidden'); });
      });
    });
  } catch(err) { alert(err.message); }
}

const al = {assess:'✅ Αξιολόγηση',to_doctor:'👨‍⚕️ Σε γιατρό',prescribe:'📝 Γνωμάτευση',fulfil:'📦 Εκτέλεση',lose:'❌ Χάθηκε',close:'🏁 Έκλεισε',reactivate:'🔄 Επανενεργοποίηση'};

// Cache for week appointments so we can open them without extra API call
let _weekAppts = [];

async function openAppointmentDetail(apptId) {
  try {
    // Try cached data first
    let appt = _weekAppts.find(a => a._id === apptId);
    if (!appt) {
      // Fallback to API
      appt = await get(`/appointments/${apptId}`);
    }
    openAppointmentForm('', appt);
  } catch (err) {
    alert('Σφάλμα: ' + err.message);
  }
}

/**
 * 📝 Σημείωση ραντεβού: τι έμαθες σε αυτό το ραντεβού.
 * Αποθηκεύεται στο note_history (timestamped) + στην επεισοδιακή μνήμη της ΤΝ,
 * δεμένη με τον γιατρό — ώστε στο επόμενο ραντεβού να τα ξέρει ο Ασκληπιός.
 */
async function openAppointmentNote(apptId) {
  let appt = _weekAppts.find(a => a._id === apptId);
  if (!appt) {
    try { appt = await get(`/appointments/${apptId}`); } catch { /* ignore */ }
  }
  const title = appt ? `${es(appt.title||'Ραντεβού')}` : 'Ραντεβού';
  const history = (appt && appt.note_history) || [];

  openModal(`📝 Σημείωση — ${title}`, `
    <p class="text-sm text-muted mb-2">Τι έμαθες σε αυτό το ραντεβού; Θα το θυμάται ο Ασκληπιός για το επόμενο (π.χ. «είπε ότι έχει περιστατικό την επόμενη εβδομάδα, της άρεσαν τα επιθέματα X»).</p>
    ${history.length ? `
      <div class="mb-2" style="max-height:140px;overflow-y:auto;background:var(--c-bg);border-radius:8px;padding:8px;">
        ${history.slice().reverse().map(h => `
          <div class="text-sm" style="padding:4px 0;border-bottom:1px solid var(--c-border-light);">
            <span class="text-faint" style="font-size:10.5px;">${(h.note_date||h.at||'').toString().slice(0,16)}</span><br>${es(h.note)}
          </div>
        `).join('')}
      </div>` : ''}
    <div class="form-group"><label>Νέα σημείωση</label><textarea id="an-note" rows="4" placeholder="Τι έμαθες / τι συμφωνήθηκε / τι του αρέσει..."></textarea></div>
    <button id="an-save" class="btn-primary full-width">💾 Αποθήκευση σημείωσης</button>
    <p id="an-error" class="error-msg hidden mt-2"></p>
  `);

  document.getElementById('an-save').addEventListener('click', async () => {
    const errEl = document.getElementById('an-error');
    errEl.classList.add('hidden');
    const note = document.getElementById('an-note').value.trim();
    if (!note) { errEl.textContent = 'Γράψε κάτι πρώτα'; errEl.classList.remove('hidden'); return; }
    try {
      const { showToast } = await import('../app.js');
      const r = await post(`/appointments/${apptId}/note`, { note });
      closeModal();
      showToast('✅ ' + (r.message || 'Σημείωση αποθηκεύτηκε'), 'success');
      await Promise.all([loadProposal(), renderMonth()]);
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

function es(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// Format a Date as YYYY-MM-DDTHH:MM in LOCAL time (for datetime-local inputs)
function _toLocalDatetimeStr(d) {
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
}
