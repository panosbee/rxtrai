/**
 * RxTrail — SearchableSelect: μετατρέπει κάθε <select> σε ψαχτήρι.
 *
 * Χρήση (μετά το render του modal):
 *   import { makeSearchable } from './searchSelect.js';
 *   makeSearchable(document.getElementById('af-doctor'), 'Ψάξε γιατρό...');
 *
 * - Προσθέτει ΟΡΑΤΟ input πάνω από το select, φιλτράρει καθώς γράφεις
 * - Κρατάει το select (κρυφό) — ΔΕΝ αλλάζει τη λογική της φόρμας
 * - Συγχρονίζει value + change events (ό,τι δουλεύει σήμερα συνεχίζει να δουλεύει)
 */
export function makeSearchable(selectEl, placeholder = 'Ψάξε...') {
  if (!selectEl || selectEl.dataset.searchable === '1') return;
  selectEl.dataset.searchable = '1';

  // BUG FIX: το HTML5 `required` σε ΚΡΥΦΟ select μπλοκάρει το submit της φόρμας
  // (ο browser δεν βλέπει το ψαχτήρι — νομίζει ότι δεν επέλεξες τίποτα).
  // Η επικύρωση γίνεται στον κώδικα της φόρμας.
  if (selectEl.required) selectEl.removeAttribute('required');

  const options = Array.from(selectEl.options).filter(o => o.value);

  const wrapper = document.createElement('div');
  wrapper.style.cssText = 'display:flex;flex-direction:column;gap:4px;width:100%;';

  const input = document.createElement('input');
  input.type = 'text';
  input.autocomplete = 'off';
  input.placeholder = '🔍 ' + placeholder;
  input.setAttribute('aria-label', placeholder);
  // Στυλ ΟΡΑΤΟ πάντα — δεν εξαρτάται από κληρονομικά CSS
  input.style.cssText = [
    'display:block', 'width:100%', 'min-height:46px',
    'padding:10px 14px',
    'border:1.5px solid var(--sea, #0A7E8C)', 'border-radius:var(--r-btn, 12px)',
    'font-size:15px', 'font-family:inherit',
    'background:#FFFFFF', 'color:#10212B',
    'box-sizing:border-box', 'outline:none',
  ].join(';');
  input.addEventListener('focus', () => { input.style.borderColor = '#0A7E8C'; });

  const list = document.createElement('div');
  list.style.cssText = 'max-height:200px;overflow-y:auto;border:1px solid #DFE8EC;border-radius:12px;display:none;background:#FFFFFF;';

  const esc = (s) => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  function render(query) {
    const q = (query || '').trim().toLowerCase();
    const matched = options.filter(o => o.textContent.toLowerCase().includes(q));
    if (!matched.length) {
      list.innerHTML = '<div style="padding:8px 12px;color:#7C909C;font-size:13px;">Κανένα αποτέλεσμα</div>';
      list.style.display = 'block';
      return;
    }
    list.innerHTML = matched.slice(0, 60).map(o => `
      <div class="ss-option" data-value="${esc(o.value)}"
           style="padding:9px 12px;cursor:pointer;font-size:14px;border-bottom:1px solid #DFE8EC;color:#10212B;">
        ${esc(o.textContent)}
      </div>
    `).join('');
    list.style.display = 'block';
    list.querySelectorAll('.ss-option').forEach(el => {
      el.addEventListener('mousedown', (e) => {
        e.preventDefault();
        selectEl.value = el.dataset.value;
        selectEl.dispatchEvent(new Event('change', { bubbles: true }));
        input.value = el.textContent.trim();
        list.style.display = 'none';
        input.blur();
      });
    });
  }

  input.addEventListener('input', () => render(input.value));
  input.addEventListener('focus', () => render(input.value));
  input.addEventListener('blur', () => setTimeout(() => { list.style.display = 'none'; }, 250));

  // Αν αλλάξει το select απ' έξω (π.χ. AI συμπλήρωση), συγχρόνισε το input
  selectEl.addEventListener('change', () => {
    const opt = options.find(o => o.value === selectEl.value);
    if (opt) input.value = opt.textContent.trim();
  });

  selectEl.insertAdjacentElement('beforebegin', wrapper);
  wrapper.appendChild(input);
  wrapper.appendChild(list);
  wrapper.appendChild(selectEl);
  selectEl.style.display = 'none';
}
