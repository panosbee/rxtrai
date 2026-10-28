/**
 * RxTrail — LLM intake component: voice/text → structured case card.
 * M4 — mic button + confirm UI.
 */
import { get, post } from '../api.js';
import { listenGreek, createMicButton, createSpeakerButton } from './voice.js';

/**
 * Open the LLM intake form in the modal.
 * Called from FAB or the Today view.
 */
export function openIntakeForm() {
  const modal = document.getElementById('modal-container');
  const overlay = document.getElementById('modal-overlay');
  const title = document.getElementById('modal-title');
  const body = document.getElementById('modal-body');

  if (!modal || !body) return;

  title.textContent = '🎤 Νέο περιστατικό (φωνή)';
  body.innerHTML = `
    <div id="intake-step-input">
      <p class="text-sm text-muted mb-2">Μίλησε ή γράψε τι έμαθες. Το σύστημα θα δομήσει την πληροφορία.</p>
      <div class="form-group">
        <textarea id="intake-text" rows="4" placeholder="π.χ. Με πήρε η Μαρία απ' το Βοήθεια στο Σπίτι, παππούς στο Γάζι, κατάκλιση ιεροκοκκυγική, έχει και σακχάρο..."></textarea>
      </div>
      <div class="flex-between">
        <button id="intake-mic-btn" class="btn-primary btn-sm">🎤 Μικρόφωνο</button>
        <button id="intake-submit-btn" class="btn-primary">Ανάλυση →</button>
      </div>
      <p id="intake-error" class="error-msg hidden mt-2"></p>
    </div>
    <div id="intake-step-result" class="hidden"></div>
  `;

  overlay.classList.remove('hidden');
  modal.classList.remove('hidden');

  document.getElementById('intake-submit-btn').addEventListener('click', submitIntake);

  // Mic button: use shared voice module
  const micBtn = document.getElementById('intake-mic-btn');
  const textArea = document.getElementById('intake-text');
  micBtn.addEventListener('click', async () => {
    micBtn.disabled = true;
    micBtn.textContent = '🔴 Ακούω...';
    try {
      const spoken = await listenGreek(15000);
      textArea.value = (textArea.value ? textArea.value + ' ' : '') + spoken;
      micBtn.textContent = '🎤 Μικρόφωνο';
    } catch (err) {
      document.getElementById('intake-error').textContent = err.message;
      document.getElementById('intake-error').classList.remove('hidden');
      micBtn.textContent = '🎤 Μικρόφωνο';
    }
    micBtn.disabled = false;
  });
}

async function submitIntake() {
  const text = document.getElementById('intake-text').value.trim();
  const errEl = document.getElementById('intake-error');
  if (!text) { errEl.textContent = 'Γράψε κάτι πρώτα'; errEl.classList.remove('hidden'); return; }

  errEl.classList.add('hidden');
  document.getElementById('intake-submit-btn').disabled = true;
  document.getElementById('intake-submit-btn').textContent = 'Ανάλυση...';

  try {
    const result = await post('/llm/intake', { text });
    showIntakeResult(result, text);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.classList.remove('hidden');
    document.getElementById('intake-submit-btn').disabled = false;
    document.getElementById('intake-submit-btn').textContent = 'Ανάλυση →';
  }
}

function showIntakeResult(result, originalText) {
  document.getElementById('intake-step-input').classList.add('hidden');
  const step2 = document.getElementById('intake-step-result');
  step2.classList.remove('hidden');

  const sources = result.source_suggestions || [];
  const sourceOptions = sources.length
    ? sources.map(s => `<option value="${s._id}">${es(s.name)} (${s._score})</option>`).join('')
    : '<option value="">— Δεν βρέθηκε —</option>';

  step2.innerHTML = `
    <h3>✅ Ανάλυση ολοκληρώθηκε</h3>
    <div class="card mt-2">
      <div class="form-group"><label>Πηγή (προαιρετικό)</label><select id="cf-source"><option value="">— Χωρίς πηγή —</option>${sourceOptions}</select></div>
      <div class="form-group"><label>Περιοχή</label><input id="cf-area" value="${es(result.area||'')}"></div>
      <div class="form-group"><label>Ψευδώνυμο</label><input id="cf-alias" value="${es(result.alias_suggestion||'')}"></div>
      <div class="form-group"><label>Κατηγορίες</label><input id="cf-cat" value="${(result.need_categories||[]).join(', ')}"></div>
      <div class="form-group"><label>Αβέβαιες</label><input id="cf-uncertain" value="${(result.uncertain_categories||[]).join(', ')}" disabled></div>
      <div class="form-group"><label>Σημαίες</label><input id="cf-flags" value="${(result.flags||[]).join(', ')}"></div>
      <div class="form-group"><label>Περίληψη</label><textarea id="cf-desc" rows="2">${es(result.freehand_summary||'')}</textarea></div>
      ${result.fallback ? '<p class="text-warning">⚠️ Το LLM δεν ανταποκρίθηκε σωστά — έλεγξε τα πεδία.</p>' : ''}
      <button id="cf-save-btn" class="btn-primary mt-2">💾 Δημιουργία περιστατικού</button>
      <p id="cf-error" class="error-msg hidden mt-2"></p>
    </div>
  `;

  import('./searchSelect.js').then(m => {
    m.makeSearchable(document.getElementById('cf-source'), '🔍 Ψάξε πηγή...');
  });

  document.getElementById('cf-save-btn').addEventListener('click', async () => {
    const errEl = document.getElementById('cf-error');
    errEl.classList.add('hidden');
    const sourceId = document.getElementById('cf-source').value;
    try {
      await post('/cases', {
        source_id: sourceId,
        alias: document.getElementById('cf-alias').value.trim() || result.alias_suggestion || 'Νέο περιστατικό',
        area: document.getElementById('cf-area').value.trim() || result.area || '',
        need_categories: document.getElementById('cf-cat').value.split(',').map(s=>s.trim()).filter(Boolean),
        needs_freehand: document.getElementById('cf-desc').value.trim() || result.freehand_summary || '',
        flags: document.getElementById('cf-flags').value.split(',').map(s=>s.trim()).filter(Boolean),
      });
      // Close modal and refresh
      document.getElementById('modal-overlay').classList.add('hidden');
      document.getElementById('modal-container').classList.add('hidden');
      // Navigate to cases
      document.querySelector('[data-view="cases"]')?.click();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}

function es(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
