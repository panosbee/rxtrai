/**
 * RxTrail — Bottom Sheet component
 * Spec §3.5: slide-up from bottom, handle, backdrop, desktop=side panel.
 */
let activeSheet = null;

/**
 * Open a bottom sheet.
 * @param {string} title - Sheet title
 * @param {string} bodyHtml - Inner HTML for the sheet body
 * @param {object} options - { onClose, height: 'auto'|'peek'|'full' }
 */
export function openSheet(title, bodyHtml, options = {}) {
  closeSheet();

  const overlay = document.createElement('div');
  overlay.className = 'sheet-overlay';
  overlay.addEventListener('click', () => {
    if (options.onClose) options.onClose();
    closeSheet();
  });

  const sheet = document.createElement('div');
  sheet.className = 'sheet';
  sheet.style.maxHeight = options.height === 'peek' ? '40vh' :
                         options.height === 'full' ? '92vh' : '85vh';

  sheet.innerHTML = `
    <div class="sheet-handle"></div>
    <div class="sheet-header">
      <h2>${title}</h2>
      <button class="icon-btn" id="sheet-close-btn" aria-label="Κλείσιμο">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>
    <div class="sheet-body">${bodyHtml}</div>
  `;

  document.body.appendChild(overlay);
  document.body.appendChild(sheet);
  activeSheet = { overlay, sheet };

  document.getElementById('sheet-close-btn').addEventListener('click', () => {
    if (options.onClose) options.onClose();
    closeSheet();
  });

  // Touch drag to close
  let startY = 0;
  const handle = sheet.querySelector('.sheet-handle');
  handle.addEventListener('touchstart', (e) => {
    startY = e.touches[0].clientY;
  });
  handle.addEventListener('touchmove', (e) => {
    const dy = e.touches[0].clientY - startY;
    if (dy > 0) {
      sheet.style.transform = `translateY(${dy}px)`;
      sheet.style.transition = 'none';
    }
  });
  handle.addEventListener('touchend', (e) => {
    const dy = e.changedTouches[0].clientY - startY;
    sheet.style.transition = 'transform var(--t-slow) var(--ease)';
    if (dy > 100) {
      if (options.onClose) options.onClose();
      closeSheet();
    } else {
      sheet.style.transform = '';
    }
  });

  return sheet;
}

export function closeSheet() {
  if (activeSheet) {
    activeSheet.overlay.remove();
    activeSheet.sheet.remove();
    activeSheet = null;
  }
}

/**
 * Confirm sheet — LLM confirm card (spec §3.6)
 */
export function openConfirmSheet(title, fields, onConfirm, onEdit) {
  const fieldsHtml = fields.map(f => `
    <div class="form-group">
      <label>${f.label}</label>
      <input type="text" id="cf-${f.key}" value="${es(f.value || '')}" 
             class="${f.uncertain ? 'uncertain-field' : ''}"
             placeholder="${f.label}">
      ${f.uncertain ? '<span class="text-sm text-sea" style="display:block;margin-top:2px;">⚠️ Αβέβαιο — τάπαρε για διόρθωση</span>' : ''}
    </div>
  `).join('');

  openSheet(title || 'Να το κατάλαβα σωστά;', `
    <div style="display:flex;flex-direction:column;gap:var(--sp-3);height:100%;">
      <div style="flex:1;overflow-y:auto;">
        ${fieldsHtml}
      </div>
      <div style="display:flex;flex-direction:column;gap:var(--sp-2);padding-top:var(--sp-3);border-top:1px solid var(--line);">
        <button id="cf-confirm-btn" class="btn-primary full-width">✓ Σωστό — Καταχώρησε</button>
        <button id="cf-edit-btn" class="btn-text full-width" style="justify-content:center;">✎ Διόρθωσε χειροκίνητα</button>
      </div>
    </div>
  `, { height: 'peek' });

  document.getElementById('cf-confirm-btn').addEventListener('click', () => {
    const data = {};
    fields.forEach(f => {
      data[f.key] = document.getElementById(`cf-${f.key}`).value.trim();
    });
    closeSheet();
    if (onConfirm) onConfirm(data);
  });

  document.getElementById('cf-edit-btn').addEventListener('click', () => {
    closeSheet();
    if (onEdit) onEdit();
  });

  // Add uncertain field styling
  const style = document.createElement('style');
  style.textContent = '.uncertain-field { border-style: dashed !important; border-color: var(--st-new) !important; }';
  document.head.appendChild(style);
}

function es(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
