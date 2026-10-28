/**
 * RxTrail — AI Agent Chat: floating chat widget.
 * Natural Language → Agent → Actions with full system access.
 * Proactive badge, auto-refresh UI, continuous voice listening.
 */
import { get, post } from '../api.js';
import { speakGreek, isSpeaking, stopSpeaking, listenGreek } from './voice.js';

let chatVisible = false;
let messages = [];
let proactiveCount = 0;
let continuousListening = false;

/**
 * Initialize the chat widget. Call once on app load.
 */
export function initAgentChat() {
  // Create floating button
  const fab = document.createElement('button');
  fab.id = 'agent-fab';
  fab.innerHTML = '⚕️';
  fab.title = 'Ασκληπιός — AI Βοηθός';
  fab.style.cssText = 'position:fixed;bottom:24px;left:24px;z-index:400;width:48px;height:48px;border-radius:50%;background:linear-gradient(135deg,#059669,#10B981);color:#fff;border:none;font-size:22px;cursor:pointer;box-shadow:0 4px 16px rgba(5,150,105,.4);transition:all .25s cubic-bezier(.34,1.56,.64,1);';
  fab.addEventListener('click', toggleChat);
  document.body.appendChild(fab);

  // Proactive badge
  const badge = document.createElement('span');
  badge.id = 'agent-badge';
  badge.style.cssText = 'position:fixed;bottom:60px;left:16px;z-index:401;background:#EF4444;color:#fff;font-size:11px;font-weight:700;min-width:18px;height:18px;border-radius:9px;display:none;align-items:center;justify-content:center;padding:0 5px;line-height:1;';
  badge.textContent = '0';
  document.body.appendChild(badge);

  // Create chat panel
  const panel = document.createElement('div');
  panel.id = 'agent-chat';
  panel.style.cssText = 'position:fixed;bottom:80px;left:24px;z-index:399;width:400px;max-width:90vw;max-height:580px;background:var(--surface);border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,.15);display:none;flex-direction:column;overflow:hidden;animation:agentIn .4s cubic-bezier(.34,1.56,.64,1);transform-origin:bottom left;';
  panel.innerHTML = `
    <div style="padding:12px 16px;background:linear-gradient(135deg,#059669,#10B981);color:#fff;display:flex;justify-content:space-between;align-items:center;">
      <strong>⚕️ Ασκληπιός</strong>
      <div style="display:flex;gap:6px;align-items:center;">
        <button id="agent-continuous-mic" style="background:rgba(255,255,255,.2);border:none;color:#fff;font-size:13px;cursor:pointer;padding:4px 8px;border-radius:4px;transition:background .2s;" title="Συνεχής ακρόαση">🎤</button>
        <button id="agent-stop-speak" style="background:rgba(255,255,255,.2);border:none;color:#fff;font-size:14px;cursor:pointer;padding:4px 8px;border-radius:4px;display:none;" title="Σταμάτημα ομιλίας">🔇</button>
        <button id="agent-close" style="background:none;border:none;color:#fff;font-size:18px;cursor:pointer;">✕</button>
      </div>
    </div>
    <div id="agent-messages" style="flex:1;overflow-y:auto;padding:12px;min-height:280px;max-height:440px;font-size:14px;display:flex;flex-direction:column;">
      <div class="text-sm text-muted" style="text-align:center;margin-top:auto;">Είμαι ο Ασκληπιός, ο βοηθός σου. Μπορώ να: δημιουργώ υγειονομικό προσωπικό, δομές, SKU, περιστατικά, επισκέψεις, ραντεβού — ό,τι κάνεις και εσύ χειροκίνητα.</div>
    </div>
    <div style="padding:8px 12px;border-top:1px solid var(--line);display:flex;gap:8px;">
      <input id="agent-input" placeholder="Ρώτα με κάτι..." style="flex:1;padding:10px;border:1px solid var(--line);border-radius:8px;font-size:14px;font-family:var(--font-ui);">
      <button id="agent-send" class="btn-primary btn-sm" style="flex-shrink:0;">📤</button>
      <button id="agent-mic" class="icon-btn" style="flex-shrink:0;font-size:16px;" title="Μικρόφωνο (μία φορά)">🎤</button>
    </div>
  `;
  document.body.appendChild(panel);

  // Bind events
  document.getElementById('agent-close').addEventListener('click', toggleChat);
  document.getElementById('agent-stop-speak').addEventListener('click', () => {
    stopSpeaking();
    document.getElementById('agent-stop-speak').style.display = 'none';
  });
  document.getElementById('agent-send').addEventListener('click', sendAgentMessage);
  document.getElementById('agent-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendAgentMessage();
  });

  // Single-use mic
  document.getElementById('agent-mic').addEventListener('click', async () => {
    const btn = document.getElementById('agent-mic');
    btn.textContent = '🔴';
    try {
    const result = await listenGreek(45000);
    const { promise } = result;
      const spoken = await promise;
      document.getElementById('agent-input').value = spoken;
      sendAgentMessage();
    } catch (e) {
      document.getElementById('agent-input').placeholder = '❌ ' + (e.message || 'Σφάλμα');
    }
    btn.textContent = '🎤';
  });

  // Continuous listening toggle
  document.getElementById('agent-continuous-mic').addEventListener('click', toggleContinuousListening);

  // Load proactive alerts every 30 min
  loadProactiveAlerts();
  setInterval(loadProactiveAlerts, 30 * 60 * 1000);
}

async function loadProactiveAlerts() {
  try {
    const data = await get('/llm/agent/proactive');
    const alerts = data.alerts || [];
    proactiveCount = alerts.length;
    updateBadge();
    // Always show alerts as pending messages — not just a number
    // Store alerts globally so they show when chat opens
    window._proactiveAlerts = alerts;
    if (alerts.length > 0) {
      // Remove old proactive messages and re-add
      document.querySelectorAll('.proactive-msg').forEach(el => el.remove());
      addSystemMessage('🔔 ' + alerts.join('\n🔔 '));
    }
  } catch (e) { /* silent */ }
}

function updateBadge() {
  const badge = document.getElementById('agent-badge');
  if (!badge) return;
  if (proactiveCount > 0) {
    badge.textContent = proactiveCount > 9 ? '9+' : String(proactiveCount);
    badge.style.display = 'flex';
  } else {
    badge.style.display = 'none';
  }
}

async function toggleContinuousListening() {
  continuousListening = !continuousListening;
  const btn = document.getElementById('agent-continuous-mic');
  if (continuousListening) {
    btn.style.background = '#EF4444';
    btn.textContent = '⏺';
    btn.title = 'Διακοπή συνεχούς ακρόασης';
    _continuousListenLoop();
  } else {
    btn.style.background = 'rgba(255,255,255,.2)';
    btn.textContent = '🎤';
    btn.title = 'Συνεχής ακρόαση';
  }
}

async function _continuousListenLoop() {
  let first = true;
  while (continuousListening) {
    try {
      // Don't listen while the agent is still speaking (unless first activation)
      if (!first && isSpeaking()) {
        await new Promise(r => setTimeout(r, 1500));
        continue;
      }
      first = false;
      const { promise, abort } = listenGreek(60000);
      const spoken = await promise;
      if (spoken && spoken.trim()) {
        // Skip wake-word-ish filler (beep phrases the user says before a real command)
        const clean = spoken.trim();
        document.getElementById('agent-input').value = clean;
        await sendAgentMessage();
      }
      await new Promise(r => setTimeout(r, 800));
    } catch (e) {
      await new Promise(r => setTimeout(r, 2000));
    }
  }
}

function toggleChat() {
  chatVisible = !chatVisible;
  document.getElementById('agent-chat').style.display = chatVisible ? 'flex' : 'none';
  if (chatVisible) {
    document.getElementById('agent-input').focus();
    proactiveCount = 0;
    updateBadge();
    // Show proactive alerts when chat opens
    if (window._proactiveAlerts?.length > 0) {
      document.querySelectorAll('.proactive-msg').forEach(el => el.remove());
      addSystemMessage('🔔 ' + window._proactiveAlerts.join('\n🔔 '));
    }
  }
}

async function sendAgentMessage() {
  const input = document.getElementById('agent-input');
  const text = input.value.trim();
  if (!text) return;

  input.value = '';
  addMessage('user', text);

  const msgDiv = addMessage('agent', '⏳ Σκέφτομαι...');

  try {
    const result = await post('/llm/agent', { message: text });
    const responseText = result.response || result.message || 'Δεν κατάλαβα. Μπορείς να το επαναλάβεις;';
    msgDiv.textContent = responseText;

    if (responseText) {
      const stopBtn = document.getElementById('agent-stop-speak');
      if (stopBtn) stopBtn.style.display = 'inline-block';
      const speaker = document.createElement('span');
      speaker.id = 'agent-speaking-indicator';
      speaker.style.cssText = 'display:inline-block;margin-left:8px;animation:pulse .8s infinite;';
      speaker.textContent = '🔊';
      msgDiv.appendChild(speaker);
      speakGreek(responseText, 1.1).then(() => {
        const ind = document.getElementById('agent-speaking-indicator');
        if (ind) ind.remove();
        if (stopBtn) stopBtn.style.display = 'none';
      });
    }

    // Auto-refresh ALL views AND show toast confirmations
    if (result.actions?.length) {
      // Show what was done as system messages in chat
      for (const action of result.actions) {
        const fnName = action.function;
        const fnResult = action.result || {};
        if (fnResult.error) {
          addSystemMessage(`⚠️ ${fnName}: ${fnResult.error}`);
        } else if (fnName === 'create_doctor') {
          addSystemMessage(`✅ ${fnResult.name || 'Υγειονομικό'} δημιουργήθηκε`);
        } else if (fnName === 'create_structure') {
          addSystemMessage(`✅ Η δομή "${fnResult.name}" δημιουργήθηκε`);
        } else if (fnName === 'create_sku') {
          addSystemMessage(`✅ Το προϊόν "${fnResult.name}" δημιουργήθηκε`);
        } else if (fnName === 'create_case') {
          addSystemMessage(`✅ Το περιστατικό "${fnResult.alias}" δημιουργήθηκε (${fnResult.status})`);
        } else if (fnName === 'create_visit') {
          addSystemMessage(`✅ Η επίσκεψη καταγράφηκε`);
        } else if (fnName === 'create_appointment') {
          addSystemMessage(`✅ Το ραντεβού δημιουργήθηκε${fnResult.has_conflict ? ' (⚠️ με conflict)' : ''}`);
        } else if (fnName === 'transition_case') {
          addSystemMessage(`✅ Το περιστατικό μεταφέρθηκε σε "${fnResult.new_status}"`);
        } else if (fnName === 'repeat_fulfilment') {
          addSystemMessage(`✅ Επανάληψη #${fnResult.cycle_number} ολοκληρώθηκε`);
        } else if (fnName === 'write_week_plan') {
          addSystemMessage(`✅ Το εβδομαδιαίο πλάνο κλειδώθηκε (${fnResult.total_items} στοιχεία)`);
        } else if (fnName === 'associate_doctor_structure') {
          addSystemMessage(`✅ Συσχέτιση: ${fnResult.doctor} ↔ ${fnResult.structure}`);
        }
      }
      setTimeout(() => refreshAllViews(result.actions), 800);
    }
  } catch (err) {
    const errMsg = typeof err === 'object' ? (err.message || err.detail || JSON.stringify(err)) : String(err);
    msgDiv.textContent = 'Σφάλμα: ' + (errMsg || 'κάτι πήγε στραβά');
    msgDiv.style.color = 'var(--danger)';
  }

  const container = document.getElementById('agent-messages');
  container.scrollTop = container.scrollHeight;
}

function addMessage(role, text) {
  const container = document.getElementById('agent-messages');
  const div = document.createElement('div');
  div.style.cssText = `margin-bottom:8px;padding:8px 12px;border-radius:10px;max-width:88%;word-break:break-word;font-size:14px;line-height:1.5;${
    role === 'user'
      ? 'background:var(--sea);color:#fff;margin-left:auto;text-align:right;'
      : 'background:var(--sea-mist);color:var(--ink);margin-right:auto;'
  }`;
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
  messages.push({ role, text, el: div });
  
  // Add feedback buttons on agent messages
  if (role === 'agent' && text !== '...' && text !== '\u23f3 \u03a3\u03ba\u03ad\u03c6\u03c4\u03bf\u03bc\u03b1\u03b9...') {
    const fb = document.createElement('div');
    fb.style.cssText = 'display:flex;gap:6px;margin-top:4px;';
    fb.innerHTML = `
      <button class="agent-fb-btn" data-fb="up" title="\u03a7\u03c1\u03ae\u03c3\u03b9\u03bc\u03bf" style="background:none;border:1px solid var(--c-border);border-radius:4px;cursor:pointer;padding:2px 6px;font-size:12px;">\ud83d\udc4d</button>
      <button class="agent-fb-btn" data-fb="down" title="\u0394\u03b5\u03bd \u03b2\u03bf\u03ae\u03b8\u03b7\u03c3\u03b5" style="background:none;border:1px solid var(--c-border);border-radius:4px;cursor:pointer;padding:2px 6px;font-size:12px;">\ud83d\udc4e</button>
    `;
    fb.querySelectorAll('.agent-fb-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const fbType = btn.dataset.fb;
        try {
          await post('/llm/agent/feedback', { message: text, feedback: fbType });
          fb.innerHTML = fbType === 'up' ? '\u2705' : '\u274c';
        } catch (err) { /* silent */ }
      });
    });
    container.appendChild(fb);
  }
  
  return div;
}

function addSystemMessage(text) {
  const container = document.getElementById('agent-messages');
  const div = document.createElement('div');
  div.className = 'proactive-msg';
  div.style.cssText = 'margin-bottom:8px;padding:8px 12px;border-radius:10px;max-width:88%;word-break:break-word;font-size:13px;line-height:1.5;background:#FEF3C7;color:#92400E;margin-right:auto;';
  div.textContent = text;
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

async function refreshAllViews(actions) {
  const actionNames = actions.map(a => a.function);

  const needsCases = actionNames.some(a =>
    ['create_case', 'transition_case', 'repeat_fulfilment', 'create_visit', 'create_prescription'].includes(a));
  const needsNetwork = actionNames.some(a =>
    ['create_doctor', 'create_structure', 'create_sku', 'associate_doctor_structure'].includes(a));
  const needsPlan = actionNames.some(a =>
    ['write_week_plan', 'create_appointment'].includes(a));

  // Dispatch custom events — ALL views listen to these
  window.dispatchEvent(new CustomEvent('agent-action', { detail: { actions } }));
  if (needsNetwork) window.dispatchEvent(new CustomEvent('agent-network-changed'));
  if (needsCases || needsPlan) window.dispatchEvent(new CustomEvent('agent-cases-changed'));

  // Force-refresh visible views immediately
  try {
    if (needsCases) {
      const casesView = document.getElementById('view-cases');
      if (casesView?.classList.contains('active')) {
        const m = await import('../views/cases.js');
        await m.renderCasesView(casesView);
      }
    }
    if (needsNetwork) {
      const netView = document.getElementById('view-network');
      if (netView?.classList.contains('active')) {
        const m = await import('../views/network.js');
        await m.renderNetworkView(netView);
      }
    }
    if (needsPlan) {
      const planView = document.getElementById('view-plan');
      if (planView?.classList.contains('active')) {
        const m = await import('../views/plan.js');
        await m.renderPlanView(planView);
      }
    }
    // Always refresh today queue after any write action
    if (needsCases || needsPlan) {
      const todayView = document.getElementById('view-today');
      if (todayView?.classList.contains('active')) {
        const m = await import('../views/today.js');
        await m.renderTodayView(todayView);
      }
    }
  } catch (e) { /* silent */ }
}
