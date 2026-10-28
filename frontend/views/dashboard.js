/**
 * RxTrail — Dashboard: monthly stats + weekly truth + export + TTS.
 * M6 — full implementation.
 */
import { get } from '../api.js';
import { speakGreek, isSpeaking, stopSpeaking } from '../components/voice.js';

export async function renderDashboardView(container) {
  container.innerHTML = `
    <h3>📈 Ταμπλό</h3>
    <div id="dash-content"><div class="spinner" style="margin:40px auto;"></div></div>
  `;
  await loadDashboard();
}

async function loadDashboard() {
  const el = document.getElementById('dash-content');
  try {
    const [monthData, truth] = await Promise.all([
      get('/dashboard/month'),
      get('/llm/weekly-truth'),
    ]);

    el.innerHTML = `
      <div class="card mt-2">
        <div class="flex-between">
          <h4>💡 ${es(truth.headline || 'Εβδομαδιαία σύνοψη')}</h4>
          <button class="icon-btn speak-insight-btn" title="Ανάγνωση insight">🔊</button>
        </div>
        <p class="text-sm mt-1">${es(truth.insight || '')}</p>
        ${truth.fallback ? '<p class="text-sm text-muted mt-1">(fallback — χωρίς LLM)</p>' : ''}
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;">
        <div class="card" style="text-align:center;"><h2 style="color:var(--c-success);">${monthData.active_cases}</h2><p class="text-sm text-muted">Ενεργά</p></div>
        <div class="card" style="text-align:center;"><h2 style="color:var(--c-danger);">${monthData.at_risk}</h2><p class="text-sm text-muted">Σε κίνδυνο</p></div>
        <div class="card" style="text-align:center;"><h2>${monthData.new_this_month}</h2><p class="text-sm text-muted">Νέα (μήνας)</p></div>
        <div class="card" style="text-align:center;"><h2>${monthData.monthly_revenue}€</h2><p class="text-sm text-muted">Τζίρος μήνα</p></div>
        <div class="card" style="text-align:center;"><h2>${monthData.prescriptions_this_month}</h2><p class="text-sm text-muted">Γνωματεύσεις</p></div>
        <div class="card" style="text-align:center;"><h2>${monthData.visits_this_month}</h2><p class="text-sm text-muted">Επισκέψεις</p></div>
      </div>

      <div class="mt-2">
        <a href="/dashboard/export/xlsx" class="btn-primary btn-sm" style="text-decoration:none;display:inline-block;">📥 Εξαγωγή Excel</a>
      </div>
    `;

    // Bind speaker button for weekly insight
    const speakBtn = document.querySelector('.speak-insight-btn');
    if (speakBtn) {
      const insightText = `${truth.headline || ''}. ${truth.insight || ''}`;
      speakBtn.addEventListener('click', async () => {
        if (isSpeaking()) { stopSpeaking(); speakBtn.textContent = '🔊'; return; }
        speakBtn.textContent = '⏹';
        await speakGreek(insightText, 1.0);
        speakBtn.textContent = '🔊';
      });
    }
  } catch (err) {
    el.innerHTML = `<div class="list-empty text-danger">${err.message}</div>`;
  }
}

function es(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
