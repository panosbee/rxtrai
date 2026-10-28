/**
 * RxTrail — Αλλαγή κωδικού (modal).
 * Ο admin-created χρήστης αναγκάζεται να αλλάξει κωδικό στην πρώτη είσοδο
 * (must_change_password). Μπορεί να το ανοίξει και μόνος του από το μενού.
 */
import { openModal, closeModal, showToast, setModalLocked } from '../app.js';
import { post } from '../api.js';

/**
 * Άνοιξε το modal αλλαγής κωδικού.
 * @param {boolean} forced — αν true, δεν κλείνει (must_change_password)
 */
export function openChangePassword(forced = false) {
  setModalLocked(forced);
  openModal('🔑 Αλλαγή κωδικού', `
    <form id="cp-form" autocomplete="off">
      <p class="text-sm text-muted mb-3" id="cp-hint">
        ${forced
          ? '⚠️ Πρέπει να ορίσεις δικό σου κωδικό πριν συνεχίσεις (ο κωδικός που σου δόθηκε είναι προσωρινός).'
          : 'Όρισε έναν νέο κωδικό (τουλάχιστον 8 χαρακτήρες).'}
      </p>
      <div class="form-group"><label>Τρέχων κωδικός</label><input type="password" id="cp-current" autocomplete="current-password"></div>
      <div class="form-group"><label>Νέος κωδικός</label><input type="password" id="cp-new" autocomplete="new-password" minlength="8"></div>
      <div class="form-group"><label>Επιβεβαίωση νέου</label><input type="password" id="cp-new2" autocomplete="new-password" minlength="8"></div>
      <button type="submit" class="btn-primary full-width">💾 Αλλαγή κωδικού</button>
      ${forced ? '' : '<button type="button" id="cp-cancel" class="btn-secondary full-width mt-2">Ακύρωση</button>'}
      <p id="cp-error" class="error-msg hidden mt-2"></p>
    </form>
  `);

  const cancelBtn = document.getElementById('cp-cancel');
  if (cancelBtn) cancelBtn.addEventListener('click', () => closeModal());

  document.getElementById('cp-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('cp-error');
    errEl.classList.add('hidden');

    const current = document.getElementById('cp-current').value;
    const newPw = document.getElementById('cp-new').value;
    const newPw2 = document.getElementById('cp-new2').value;

    if (newPw.length < 8) {
      errEl.textContent = 'Ο νέος κωδικός πρέπει να έχει τουλάχιστον 8 χαρακτήρες.';
      errEl.classList.remove('hidden');
      return;
    }
    if (newPw !== newPw2) {
      errEl.textContent = 'Οι δύο νέοι κωδικοί δεν ταιριάζουν.';
      errEl.classList.remove('hidden');
      return;
    }
    if (newPw === current) {
      errEl.textContent = 'Ο νέος κωδικός πρέπει να διαφέρει από τον τρέχοντα.';
      errEl.classList.remove('hidden');
      return;
    }

    try {
      await post('/auth/change-password', { current_password: current, new_password: newPw });
      setModalLocked(false);
      closeModal();
      showToast('✅ Ο κωδικός άλλαξε επιτυχώς!', 'success');
      const lp = document.getElementById('login-password');
      if (lp) lp.value = '';
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });
}
