/**
 * RxTrail — Visits view: log quick visits + history.
 * M3 — Κανάλι 2: κλασικές επισκέψεις σε γιατρούς/επαφές/δομές.
 */
import { get, post } from '../api.js';
import { dictationFill } from '../components/voice.js';

export async function renderVisitsView(container) {
  container.innerHTML = `
    <div class="flex-between mb-2"><h3>📋 Επισκέψεις</h3><button id="nv-btn" class="btn-primary btn-sm">+ Καταγραφή</button></div>
    <div id="visits-list"></div>
  `;
  document.getElementById('nv-btn').addEventListener('click', showVisitForm);
  await loadVisits();
}

async function loadVisits() {
  const el = document.getElementById('visits-list');
  try {
    const data = await get('/visits?limit=50');
    const items = data.items || [];
    if (!items.length) { el.innerHTML = '<div class="list-empty">Δεν υπάρχουν επισκέψεις</div>'; return; }
    el.innerHTML = items.map(v => `
      <div class="card"><div class="card-header">
        <span>${ti[v.target_type]||''} <strong>${v.target_type}</strong></span>
        <span class="tag tag-${ot(v.outcome)}">${ol[v.outcome]||v.outcome}</span>
      </div>
      <p class="card-subtitle">${new Date(v.at).toLocaleDateString('el-GR')} · ${v.channel||'—'}</p>
      ${v.voice_note_text?`<p class="text-sm">📝 ${es(v.voice_note_text.substring(0,100))}</p>`:''}
    </div>`).join('');
  } catch (err) { el.innerHTML = `<div class="list-empty text-danger">${err.message}</div>`; }
}

function showVisitForm() {
  Promise.all([get('/doctors?limit=200'),get('/contacts?limit=100'),get('/structures?limit=100')]).then(([docs,conts,structs])=>{
    const dOpts=(docs.items||[]).map(d=>`<option value="DOCTOR|${d._id}">👨‍⚕️ ${es(d.name)}</option>`).join('');
    const cOpts=(conts.items||[]).map(c=>`<option value="CONTACT|${c._id}">👤 ${es(c.name)}</option>`).join('');
    const sOpts=(structs.items||[]).map(s=>`<option value="STRUCTURE|${s._id}">🏥 ${es(s.name)}</option>`).join('');
    showModal('📋 Καταγραφή επίσκεψης',`
      <form id="vf"><div class="form-group"><label>Ποιον είδες</label><select id="v-target" required><option value="">—</option>${dOpts}${cOpts}${sOpts}</select></div>
      <div class="form-group"><label>Κανάλι</label><select id="v-channel"><option value="IN_PERSON">Δια ζώσης</option><option value="PHONE">Τηλέφωνο</option><option value="CONGRESS">Συνέδριο</option></select></div>
      <div class="form-group"><label>Έκβαση</label><div style="display:flex;gap:6px;flex-wrap:wrap;" id="v-outcomes">
        <button type="button" class="btn-primary btn-sm ob" data-o="POSITIVE">✅ Θετικό</button>
        <button type="button" class="btn-primary btn-sm ob" data-o="NEUTRAL">😐 Ουδέτερο</button>
        <button type="button" class="btn-primary btn-sm ob" data-o="FRIDGE">🧊 Ψυγείο</button>
        <button type="button" class="btn-primary btn-sm ob" data-o="PRESCRIBED">📝 Έγραψε</button>
      </div></div>
      <div class="form-group"><label>Σημείωση <button type="button" id="v-mic-btn" class="icon-btn" title="Φωνητική σημείωση" style="font-size:16px;">🎤</button></label><textarea id="v-note" rows="2"></textarea></div>
      <p id="v-err" class="error-msg hidden"></p>
      <button type="submit" class="btn-primary mt-2">💾 Αποθήκευση</button></form>
    `);
    let outcome='NEUTRAL';
    document.querySelectorAll('.ob').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('.ob').forEach(bb=>bb.style.opacity='0.5');b.style.opacity='1';outcome=b.dataset.o;}));
    document.querySelector('.ob[data-o="NEUTRAL"]')?.click();

    // Mic button for voice note
    const micBtn = document.getElementById('v-mic-btn');
    const noteEl = document.getElementById('v-note');
    if (micBtn && noteEl) {
      micBtn.addEventListener('click', () => dictationFill(noteEl, micBtn));
    }

    document.getElementById('vf').addEventListener('submit',async e=>{
      e.preventDefault();const err=document.getElementById('v-err');err.classList.add('hidden');
      const tv=document.getElementById('v-target').value;if(!tv){err.textContent='Επίλεξε στόχο';err.classList.remove('hidden');return;}
      const [tt,tid]=tv.split('|');
      try{await post('/visits',{target_type:tt,target_id:tid,channel:document.getElementById('v-channel').value,outcome,voice_note_text:document.getElementById('v-note').value.trim()||null});hideModal();await loadVisits();}
      catch(x){err.textContent=x.message;err.classList.remove('hidden');}
    });
  });
}

// Modal helpers (use global DOM elements)
function showModal(title,bodyHtml) {
  document.getElementById('modal-title').textContent=title;
  document.getElementById('modal-body').innerHTML=bodyHtml;
  document.getElementById('modal-overlay').classList.remove('hidden');
  document.getElementById('modal-container').classList.remove('hidden');
}
function hideModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
  document.getElementById('modal-container').classList.add('hidden');
}

const ti={DOCTOR:'👨‍⚕️',CONTACT:'👤',STRUCTURE:'🏥'};
const ol={POSITIVE:'Θετικό',NEUTRAL:'Ουδέτερο',FRIDGE:'Ψυγείο',PRESCRIBED:'Έγραψε'};
function ot(o){return o==='POSITIVE'?'active':o==='PRESCRIBED'?'active':o==='FRIDGE'?'risk':'new';}
function es(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
