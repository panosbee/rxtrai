/**
 * RxTrail — Onboarding Wizard: first-run guided setup.
 * Beautiful step-by-step flow. Auto-detects what's missing.
 */
import { get, post, patch } from '../api.js';
import { openModal, closeModal } from '../app.js';

let onboardingData = null;
let currentStepIdx = 0;

/**
 * Check if onboarding is needed and show wizard if so.
 * Only auto-shows ONCE ever (tracks via localStorage).
 * Can always be opened manually from ☰ menu → 🎓 Οδηγός εκκίνησης.
 * @param {boolean} force — if true, shows wizard even if already shown before
 * @returns {boolean} true if wizard was shown
 */
export async function maybeShowOnboarding(force = false) {
  // Check localStorage — only auto-show once
  const wizardShown = localStorage.getItem('rxtrail_wizard_shown');
  if (wizardShown && !force) return false;

  try {
    onboardingData = await get('/onboarding/status');
    if (onboardingData.all_done) {
      localStorage.setItem('rxtrail_wizard_shown', '1');
      return false;
    }

    // Mark as shown (even if user skips)
    localStorage.setItem('rxtrail_wizard_shown', '1');

    currentStepIdx = 0;
    renderWizard();
    return true;
  } catch (err) {
    console.warn('Onboarding check failed:', err);
    return false;
  }
}

function renderWizard() {
  const steps = onboardingData.steps;
  const step = steps[currentStepIdx];
  const progress = Math.round((currentStepIdx / steps.length) * 100);

  openModal('🎓 Καλώς ήρθες!', `
    <div id="onboarding-wizard" style="text-align:center;">
      <!-- Progress bar -->
      <div style="background:var(--c-border-light);border-radius:var(--radius-full);height:6px;margin-bottom:8px;overflow:hidden;">
        <div style="background:var(--c-primary);height:100%;width:${progress}%;border-radius:var(--radius-full);transition:width .4s var(--ease-out);"></div>
      </div>
      <p class="text-sm text-muted" style="margin-bottom:20px;">Βήμα ${currentStepIdx + 1} από ${steps.length}</p>

      <!-- Step content -->
      <div class="onboarding-step" style="animation: fadeIn .3s var(--ease-out);">
        <span style="font-size:56px;display:block;margin-bottom:12px;">${step.icon}</span>
        <h3 style="margin-bottom:8px;">${step.label}</h3>
        <p style="color:var(--c-text-secondary);margin-bottom:20px;">${step.description}</p>
        <div id="onboarding-action-area"></div>
      </div>

      <!-- Navigation -->
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:24px;border-top:1px solid var(--c-border-light);padding-top:16px;">
        <button id="ob-skip-all" class="btn-text">Παράλειψη όλων →</button>
        <div style="display:flex;gap:8px;">
          <button id="ob-skip" class="btn-secondary btn-sm" ${currentStepIdx === 0 ? 'disabled' : ''}>← Πίσω</button>
          <button id="ob-next" class="btn-primary btn-sm">
            ${step.done ? '✅ Έγινε' : currentStepIdx === steps.length - 1 ? '🏁 Τέλος' : 'Επόμενο →'}
          </button>
        </div>
      </div>
    </div>
  `);

  // Render the action for this step
  renderStepAction(step);

  // Bind navigation
  document.getElementById('ob-skip-all').addEventListener('click', () => {
    closeModal();
  });

  document.getElementById('ob-skip').addEventListener('click', () => {
    if (currentStepIdx > 0) {
      currentStepIdx--;
      renderWizard();
    }
  });

  document.getElementById('ob-next').addEventListener('click', async () => {
    // Mark current step as done
    onboardingData.steps[currentStepIdx].done = true;

    if (currentStepIdx < steps.length - 1) {
      currentStepIdx++;
      renderWizard();
    } else {
      // All done!
      closeModal();
    }
  });
}

function renderStepAction(step) {
  const area = document.getElementById('onboarding-action-area');
  if (!area) return;

  switch (step.id) {
    case 'profile':
      area.innerHTML = `
        <form id="ob-profile-form" style="text-align:left;">
          <div class="form-group">
            <label>Όνομα Οργανισμού</label>
            <input id="ob-org-name" placeholder="π.χ. Panos Homecare" style="font-size:16px;">
          </div>
          <button type="submit" class="btn-primary btn-sm full-width">💾 Αποθήκευση</button>
        </form>
      `;
      document.getElementById('ob-profile-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('ob-org-name').value.trim();
        if (!name) return;
        await patch('/settings', { name });
        const nextBtn = document.getElementById('ob-next');
        nextBtn.textContent = 'Επόμενο →';
        onboardingData.steps[currentStepIdx].done = true;
      });
      break;

    case 'sources':
      area.innerHTML = `
        <form id="ob-source-form" style="text-align:left;">
          <div class="form-group"><label>Όνομα πηγής *</label><input id="ob-src-name" placeholder="π.χ. Βοήθεια στο Σπίτι" required></div>
          <div class="form-group"><label>Τύπος</label><select id="ob-src-type">
            <option value="HOMECARE_CREW">Βοήθεια στο Σπίτι</option>
            <option value="HOSPITAL">Νοσοκομείο</option>
            <option value="PRIVATE_DOCTOR">Ιδιώτης Γιατρός</option>
            <option value="PHYSIO">Φυσικοθεραπευτής</option>
            <option value="PHARMACY">Φαρμακείο</option>
            <option value="PATIENT_FAMILY">Οικογένεια Ασθενή</option>
            <option value="OTHER">Άλλο</option>
          </select></div>
          <div class="form-group"><label>Τηλέφωνο</label><input id="ob-src-phone" placeholder="69XXXXXXXX"></div>
          <button type="submit" class="btn-primary btn-sm full-width">💾 Αποθήκευση</button>
        </form>
      `;
      document.getElementById('ob-source-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
          name: document.getElementById('ob-src-name').value.trim(),
          type: document.getElementById('ob-src-type').value,
          phone: document.getElementById('ob-src-phone').value.trim() || null,
        };
        if (!payload.name) return;
        await post('/sources', payload);
        const nextBtn = document.getElementById('ob-next');
        nextBtn.textContent = 'Επόμενο →';
        onboardingData.steps[currentStepIdx].done = true;
      });
      break;

    case 'doctors':
      area.innerHTML = `
        <form id="ob-doctor-form" style="text-align:left;">
          <div class="form-group"><label>Ονοματεπώνυμο *</label><input id="ob-doc-name" placeholder="π.χ. Δρ. Παπαδάκης" required></div>
          <div class="form-group"><label>Ειδικότητα</label><input id="ob-doc-spec" placeholder="π.χ. Χειρουργός"></div>
          <div class="form-group"><label>Τηλέφωνο</label><input id="ob-doc-phone" placeholder="69XXXXXXXX"></div>
          <button type="submit" class="btn-primary btn-sm full-width">💾 Αποθήκευση & προσθήκη άλλου</button>
        </form>
        <div id="ob-doctors-list" class="mt-2"></div>
      `;
      document.getElementById('ob-doctor-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
          name: document.getElementById('ob-doc-name').value.trim(),
          specialty: document.getElementById('ob-doc-spec').value.trim() || null,
          phone: document.getElementById('ob-doc-phone').value.trim() || null,
        };
        if (!payload.name) return;
        await post('/doctors', payload);
        // Clear form for next
        document.getElementById('ob-doc-name').value = '';
        document.getElementById('ob-doc-spec').value = '';
        document.getElementById('ob-doc-phone').value = '';
        document.getElementById('ob-doc-name').focus();
        // Show added
        const list = document.getElementById('ob-doctors-list');
        list.innerHTML += `<div class="text-sm text-success">✅ ${payload.name} προστέθηκε</div>`;
        const nextBtn3 = document.getElementById('ob-next');
        nextBtn3.textContent = 'Επόμενο →';
        onboardingData.steps[currentStepIdx].done = true;
      });
      break;

    case 'structures':
      area.innerHTML = `
        <form id="ob-struct-form" style="text-align:left;">
          <div class="form-group"><label>Όνομα Δομής *</label><input id="ob-struct-name" placeholder="π.χ. Πανεπιστημιακό Νοσοκομείο" required></div>
          <div class="form-group"><label>Τύπος</label><select id="ob-struct-type">
            <option value="HOSPITAL">Νοσοκομείο</option>
            <option value="CLINIC">Κλινική</option>
            <option value="HEALTH_CENTER">Κέντρο Υγείας</option>
            <option value="NURSING_HOME">Γηροκομείο</option>
            <option value="OTHER">Άλλο</option>
          </select></div>
          <div class="form-group"><label>Πόλη</label><input id="ob-struct-city" placeholder="π.χ. Ηράκλειο"></div>
          <button type="submit" class="btn-primary btn-sm full-width">💾 Αποθήκευση</button>
        </form>
      `;
      document.getElementById('ob-struct-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const payload = {
          name: document.getElementById('ob-struct-name').value.trim(),
          type: document.getElementById('ob-struct-type').value,
          city: document.getElementById('ob-struct-city').value.trim() || null,
        };
        if (!payload.name) return;
        await post('/structures', payload);
        const nextBtn2 = document.getElementById('ob-next');
        nextBtn2.textContent = 'Επόμενο →';
        onboardingData.steps[currentStepIdx].done = true;
      });
      break;

    case 'skus':
      area.innerHTML = `
        <div style="text-align:center;">
          <p class="text-sm text-muted mb-2">Μπορείς να εισάγεις πολλούς κωδικούς μαζί μέσω Excel</p>
          <button id="ob-open-import" class="btn-primary btn-sm">📥 Άνοιγμα Import Wizard</button>
          <p class="text-xs text-muted mt-2">Ή πάτα "Επόμενο" για να το κάνεις αργότερα</p>
        </div>
      `;
      document.getElementById('ob-open-import').addEventListener('click', async () => {
        closeModal();
        const { openImportModal } = await import('../views/settings.js');
        openImportModal();
      });
      // Always allow skipping this step
      const nextBtn4 = document.getElementById('ob-next');
      nextBtn4.textContent = 'Επόμενο →';
      onboardingData.steps[currentStepIdx].done = true;
      break;

    case 'first_case':
      area.innerHTML = `
        <div style="text-align:center;">
          <p class="text-sm text-muted mb-2">Δημιούργησε το πρώτο σου περιστατικό — με AI voice ή χειροκίνητα</p>
          <button id="ob-new-case" class="btn-primary btn-sm">🎤 Νέο Περιστατικό</button>
          <p class="text-xs text-muted mt-2">Ή πάτα "Τέλος" για να ξεκινήσεις!</p>
        </div>
      `;
      document.getElementById('ob-new-case').addEventListener('click', async () => {
        closeModal();
        const { openNewCaseForm } = await import('../components/newCaseForm.js');
        openNewCaseForm();
      });
      const nextBtn5 = document.getElementById('ob-next');
      nextBtn5.textContent = '🏁 Τέλος';
      onboardingData.steps[currentStepIdx].done = true;
      break;
  }
}
