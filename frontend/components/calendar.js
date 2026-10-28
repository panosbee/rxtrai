/**
 * RxTrail — Calendar component: monthly view + week view + CRUD.
 * Syncs with Week planner.
 */
import { get, post, patch, del } from '../api.js';

const DAY_NAMES = ['Δευ', 'Τρί', 'Τετ', 'Πέμ', 'Παρ', 'Σάβ', 'Κυρ'];
const MONTH_NAMES = ['Ιαν', 'Φεβ', 'Μάρ', 'Απρ', 'Μάι', 'Ιούν', 'Ιούλ', 'Αύγ', 'Σεπ', 'Οκτ', 'Νοέ', 'Δεκ'];
const KIND_ICONS = { DOCTOR: '👨‍⚕️', CASE: '📋', PERSONAL: '👤', OTHER: '📌' };
const KIND_LABELS = { DOCTOR: 'Γιατρός', CASE: 'Περιστατικό', PERSONAL: 'Προσωπικό', OTHER: 'Άλλο' };

let currentYear, currentMonth;
let appointmentsCache = {};

/**
 * Render calendar inside a container element.
 */
export async function renderCalendarView(container) {
  const now = new Date();
  currentYear = now.getFullYear();
  currentMonth = now.getMonth() + 1;

  container.innerHTML = `
    <div class="flex-between mb-2">
      <div style="display:flex;align-items:center;gap:8px;">
        <button id="cal-prev" class="icon-btn">◀</button>
        <h3 id="cal-title" style="font-size:16px;"></h3>
        <button id="cal-next" class="icon-btn">▶</button>
      </div>
      <button id="cal-add-btn" class="btn-primary btn-sm">+ Ραντεβού</button>
    </div>
    <div id="cal-grid" style="display:grid;grid-template-columns:repeat(7,1fr);gap:2px;"></div>
    <div id="cal-day-detail" class="mt-2" style="display:none;"></div>
  `;

  document.getElementById('cal-prev').addEventListener('click', () => { currentMonth--; if(currentMonth<1){currentMonth=12;currentYear--;} renderMonth(); });
  document.getElementById('cal-next').addEventListener('click', () => { currentMonth++; if(currentMonth>12){currentMonth=1;currentYear++;} renderMonth(); });
  document.getElementById('cal-add-btn').addEventListener('click', () => openAppointmentForm());
  await renderMonth();
}

async function renderMonth() {
  document.getElementById('cal-title').textContent = `${MONTH_NAMES[currentMonth-1]} ${currentYear}`;

  // Load appointments for this month
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
  const startDow = (firstDay.getDay() + 6) % 7; // Monday=0
  const totalDays = lastDay.getDate();
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${today.getMonth()+1}-${today.getDate()}`;

  let html = '';
  // Day headers
  for (const d of DAY_NAMES) {
    html += `<div style="text-align:center;font-weight:600;font-size:11px;padding:4px;color:var(--c-text-secondary);">${d}</div>`;
  }
  // Empty cells before month start
  for (let i = 0; i < startDow; i++) {
    html += '<div style="min-height:60px;background:var(--c-bg);border-radius:4px;"></div>';
  }
  // Day cells
  for (let day = 1; day <= totalDays; day++) {
    const dateStr = `${currentYear}-${String(currentMonth).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
    const isToday = dateStr === todayStr;
    const apps = appointmentsCache[day] || [];
    const dots = apps.length ? apps.slice(0,3).map(a => 
      `<span style="font-size:9px;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--c-primary);">${KIND_ICONS[a.kind]||''} ${es(a.title||'').substring(0,10)}</span>`
    ).join('') : '';

    html += `<div class="cal-day ${isToday?'cal-today':''}" data-date="${dateStr}" 
      style="min-height:60px;padding:4px;border-radius:6px;cursor:pointer;background:${isToday?'#dbeafe':'var(--c-surface)'};border:1px solid var(--c-border);font-size:12px;">
      <strong style="font-size:13px;">${day}</strong>
      ${dots}
      ${apps.length > 3 ? `<span style="font-size:9px;color:var(--c-text-muted);">+${apps.length-3}</span>` : ''}
    </div>`;
  }
  grid.innerHTML = html;

  // Click handlers
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
      <button class="btn-primary btn-sm mt-1" onclick="document.getElementById('cal-add-btn').click()">+ Ραντεβού</button></div>`;
    return;
  }

  detail.innerHTML = `
    <h4>📅 ${new Date(dateStr).toLocaleDateString('el-GR', {weekday:'long', day:'numeric', month:'long'})}</h4>
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
  detail.querySelectorAll('.delete-appt-btn').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Διαγραφή ραντεβού;')) return;
    try {
      await del(`/appointments/${b.dataset.id}`);
    } catch (err) {
      if (!/404|δεν βρέθηκε|not found/i.test(err.message || '')) {
        alert('Σφάλμα διαγραφής: ' + err.message);
        return;
      }
    }
    await renderMonth();
    showDayDetail(dateStr);
    const weekRefresh = document.getElementById('wk-refresh');
    if (weekRefresh) weekRefresh.click();
  }));
}

// ── Appointment form modal ──────────────────────────────────────────────────

async function openAppointmentForm(prefillDate = '', editAppt = null) {
  const { openModal, closeModal } = await import('../app.js');

  // Load doctors for dropdown
  let doctors = [];
  try { const d = await get('/doctors?limit=200'); doctors = d.items || []; } catch(e){}
  const doctorOpts = doctors.map(d => `<option value="${d._id}" ${editAppt?.doctor_id===d._id?'selected':''}>${es(d.name)} (${d.specialty||''})</option>`).join('');

  const title = editAppt ? '✏️ Επεξεργασία Ραντεβού' : '🆕 Νέο Ραντεβού';
  const dateVal = editAppt ? new Date(editAppt.at).toISOString().slice(0,16) : (prefillDate ? prefillDate + 'T09:00' : '');
  const endVal = editAppt?.end_at ? new Date(editAppt.end_at).toISOString().slice(0,16) : '';

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
      <div id="af-conflicts" class="hidden" style="background:#fee2e2;padding:8px;border-radius:6px;margin-bottom:8px;"></div>
      <button type="submit" class="btn-primary full-width">💾 Αποθήκευση</button>
      <p id="af-error" class="error-msg hidden mt-2"></p>
    </form>
  `);

  // Ψαχτήρι γιατρού
  import('./searchSelect.js').then(m => {
    m.makeSearchable(document.getElementById('af-doctor'), '🔍 Ψάξε γιατρό...');
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
        return; // Don't close — let user decide
      }

      closeModal();
      await renderMonth();
      // Συγχρονισμός με την εβδομάδα (Πλάνο) — αν είναι ορατή
      const weekRefresh = document.getElementById('wk-refresh');
      if (weekRefresh) weekRefresh.click();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

function es(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
