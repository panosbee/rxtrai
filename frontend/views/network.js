/**
 * RxTrail — Network view: tabs for SKUs, Doctors, Structures, Contacts, Sources.
 */
import { get, post, patch, del } from '../api.js';
import { openModal, closeModal, showToast } from '../app.js';

const ENTITIES = {
  skus: { label: 'SKU', icon: '📦', fields: ['product_id', 'eopyy_code', 'commercial_name', 'description', 'category', 'icd10_code', 'reimb_price', 'patient_copay_pct', 'max_qty_month', 'duration_days_default'] },
  doctors: { label: 'Υγειονομικό Προσωπικό', icon: '🏥', fields: ['name', 'specialty', 'workplace_type', 'area', 'address', 'phone', 'email', 'notes', 'product_categories'], metrics: ['rx_count_90d', 'avg_pickup_days', 'open_cases_count', 'last_visit_at'] },
  structures: { label: 'Δομές', icon: '🏥', fields: ['name', 'type', 'city', 'prefecture', 'address', 'phone', 'notes'] },
  sources: { label: 'Πηγές', icon: '🔗', fields: ['name', 'type', 'phone', 'notes'] },
};

let activeEntity = 'skus';
let items = [];
let total = 0;
let page = 0;
let _dirty = false; // agent changed data → force reload on next view
const PAGE_SIZE = 30;
let _structureCache = null; // { id: name } map for displaying structure names

/**
 * Deep-link: άνοιξε το Δίκτυο σε συγκεκριμένη καρτέλα
 * (καλείται από το desktop sidebar submenu).
 */
export function setNetworkEntity(entity) {
  if (ENTITIES[entity]) {
    activeEntity = entity;
    page = 0;
  }
}

/** Εμφανίζει το κουμπί Excel μόνο στην καρτέλα υγειονομικού προσωπικού. */
function updateExportButton() {
  const btn = document.getElementById('network-export-btn');
  if (btn) btn.classList.toggle('hidden', activeEntity !== 'doctors');
}

export async function renderNetworkView(container) {  container.innerHTML = `
    <div class="network-tabs" id="network-tabs">
      ${Object.entries(ENTITIES).map(([key, e]) =>
        `<button data-entity="${key}" class="tab-btn ${key === activeEntity ? 'active' : ''}">${e.icon} ${e.label}</button>`
      ).join('')}
    </div>
    <div class="flex-between mb-2 mt-2">
      <input type="search" id="network-search" placeholder="Αναζήτηση ή ρώτα το AI..." class="form-group" style="flex:1;margin-right:8px;">
      <button id="network-ai-btn" class="btn-primary btn-sm" title="AI Search">🤖</button>
      <button id="network-export-btn" class="btn-secondary btn-sm hidden" title="Εξαγωγή σε Excel" style="margin-left:4px;">📥 Excel</button>
      <button id="network-add-btn" class="btn-primary btn-sm" style="margin-left:4px;">+ Προσθήκη</button>
    </div>
    <div id="network-ai-answer" class="hidden" style="background:var(--sea-mist);border-radius:var(--r-card);padding:var(--sp-4);margin-bottom:var(--sp-3);font-size:14px;line-height:1.7;max-height:300px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;"></div>
    <!-- Category filter chips (SKU only) -->
    <div id="sku-cat-filters" class="hidden" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:var(--sp-3);"></div>
    <!-- Stats bar -->
    <div id="sku-stats" class="hidden flex-between mb-2" style="font-size:12px;color:var(--ink-soft);background:var(--sea-mist);padding:var(--sp-2) var(--sp-3);border-radius:var(--r-btn);"></div>
    <div id="network-bulk-bar" class="hidden flex-between mb-2" style="background:var(--sea-mist);padding:var(--sp-2) var(--sp-3);border-radius:var(--r-btn);">
      <label class="text-sm"><input type="checkbox" id="network-select-all"> Επιλογή όλων <span id="network-bulk-count"></span></label>
      <div style="display:flex;gap:8px;">
        <button id="network-bulk-delete" class="btn-danger btn-sm">🗑️ Διαγραφή επιλεγμένων</button>
        <button id="network-bulk-delete-all" class="btn-danger btn-sm" title="Διαγραφή ΟΛΩΝ ανεξαρτήτως σελίδας">🗑️💣 Όλα</button>
      </div>
    </div>
    <div id="network-list"></div>
    <div id="network-pagination" class="flex-between mt-2">
      <button id="network-prev" class="btn-text btn-sm" disabled>← Προηγούμενα</button>
      <span class="text-sm text-muted" id="network-count"></span>
      <button id="network-next" class="btn-text btn-sm">Επόμενα →</button>
    </div>
  `;

  bindNetworkEvents(container);
  updateExportButton();
  await loadItems();
  _dirty = false;
  if (_structureCache === null) await _loadStructureCache();

  // Listen for agent changes — mark dirty for next navigation
  window.addEventListener('agent-network-changed', () => { _dirty = true; _structureCache = null; });
}

function bindNetworkEvents(container) {
  container.querySelectorAll('#network-tabs .tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      activeEntity = btn.dataset.entity;
      page = 0;
      renderNetworkView(document.getElementById('view-network'));
    });
  });

  // 📥 Εξαγωγή Excel (μόνο στο υγειονομικό προσωπικό)
  document.getElementById('network-export-btn').addEventListener('click', () => {
    const token = localStorage.getItem('rxtrail_token');
    const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
    const base = isLocal ? 'http://127.0.0.1:8000' : window.location.origin;
    const prefix = isLocal ? '' : '/api';
    window.open(`${base}${prefix}/doctors/export.xlsx?token=${encodeURIComponent(token)}`, '_blank');
  });

  document.getElementById('network-search').addEventListener('input', debounce(() => {
    page = 0;
    loadItems();
  }, 300));

  document.getElementById('network-add-btn').addEventListener('click', () => openEditForm(null));
  document.getElementById('network-prev').addEventListener('click', () => { page--; loadItems(); });
  document.getElementById('network-next').addEventListener('click', () => { page++; loadItems(); });

  // Bulk actions
  document.getElementById('network-select-all').addEventListener('change', (e) => {
    document.querySelectorAll('.network-checkbox').forEach(cb => cb.checked = e.target.checked);
  });
  document.getElementById('network-bulk-delete').addEventListener('click', bulkDelete);
  document.getElementById('network-bulk-delete-all').addEventListener('click', bulkDeleteAll);

  // AI Search button
  document.getElementById('network-ai-btn').addEventListener('click', aiSearch);

  // Enter key triggers AI search if input has text, else regular search
  document.getElementById('network-search').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const q = document.getElementById('network-search').value.trim();
      if (q && q.length > 2) {
        e.preventDefault();
        aiSearch();
      }
    }
  });

  // Listen for import-complete to auto-refresh the relevant tab
  window.addEventListener('import-complete', (e) => {
    const kindMap = { SKU: 'skus', STRUCTURES: 'structures', DOCTORS: 'doctors' };
    const entityKey = kindMap[e.detail?.kind];
    if (entityKey) {
      activeEntity = entityKey;
      page = 0;
      renderNetworkView(document.getElementById('view-network'));
    }
  });
}

async function loadItems() {
  const search = document.getElementById('network-search')?.value || '';
  const params = new URLSearchParams({ skip: page * PAGE_SIZE, limit: PAGE_SIZE });
  if (search) params.set('search', search);

  try {
    const data = await get(`/${activeEntity}?${params}`);
    items = data.items;
    total = data.total;

    renderList();
    updatePagination();
  } catch (err) {
    document.getElementById('network-list').innerHTML = `<div class="list-empty">Σφάλμα: ${err.message}</div>`;
  }
}

function renderList() {
  const el = document.getElementById('network-list');
  const bulkBar = document.getElementById('network-bulk-bar');
  const catFilters = document.getElementById('sku-cat-filters');
  const statsBar = document.getElementById('sku-stats');
  
  if (!items.length) {
    el.innerHTML = '<div class="list-empty">Δεν βρέθηκαν εγγραφές</div>';
    bulkBar.classList.add('hidden');
    catFilters.classList.add('hidden');
    statsBar.classList.add('hidden');
    return;
  }

  bulkBar.classList.remove('hidden');
  document.getElementById('network-select-all').checked = false;

  const entity = ENTITIES[activeEntity];
  
  // ── SKU-specific: category filters & stats ─────────────────────────
  if (activeEntity === 'skus') {
    catFilters.classList.remove('hidden');
    statsBar.classList.remove('hidden');
    
    // Category filter chips
    const cats = [...new Set(items.map(i => i.category).filter(Boolean))].sort();
    catFilters.innerHTML = `<button class="chip active" data-cat="" style="cursor:pointer;">Όλα (${total})</button>` +
      cats.map(c => {
        const count = items.filter(i => i.category === c).length;
        return `<button class="chip" data-cat="${escapeHtml(c)}" style="cursor:pointer;">${escapeHtml(c)} (${count})</button>`;
      }).join('');
    
    catFilters.querySelectorAll('.chip').forEach(chip => {
      chip.addEventListener('click', () => {
        page = 0;
        const cat = chip.dataset.cat;
        document.getElementById('network-search').value = '';
        if (cat) {
          document.getElementById('network-search').value = cat;
        }
        loadItems();
      });
    });
    
    // Stats
    const withPrice = items.filter(i => i.reimb_price > 0);
    const totalVal = withPrice.reduce((s, i) => s + (i.reimb_price || 0), 0);
    const withEopyy = items.filter(i => i.eopyy_code).length;
    const withIcd = items.filter(i => i.icd10_code).length;
    statsBar.innerHTML = `<span>Σύνολο: <strong>${total}</strong> προϊόντα</span><span>Με κωδ. ΕΟΠΥΥ: <strong>${withEopyy}</strong></span><span>Με ICD-10: <strong>${withIcd}</strong></span><span>Συνολική αξία: <strong>${totalVal.toFixed(0)}€</strong></span>`;
  } else {
    catFilters.classList.add('hidden');
    statsBar.classList.add('hidden');
  }

  // ── Render list items ──────────────────────────────────────────────
  el.innerHTML = items.map(item => {
    if (activeEntity === 'skus') {
      const price = item.reimb_price ? `${item.reimb_price}€` : '';
      const code = item.product_id || item.eopyy_code || '';
      const eopyy = item.eopyy_code ? `<span style="background:var(--st-atdoctor);color:#fff;font-size:10px;padding:1px 5px;border-radius:3px;">ΕΟΠΥΥ</span>` : '';
      const icd = item.icd10_code ? `<span style="background:var(--st-assessed);color:#fff;font-size:10px;padding:1px 5px;border-radius:3px;margin-left:4px;">ICD-10: ${escapeHtml(item.icd10_code)}</span>` : '';
      const cat = item.category ? `<span style="color:var(--ink-faint);font-size:11px;">${escapeHtml(item.category)}</span>` : '';
      
      return `
      <div class="list-item" data-id="${item._id}" style="display:flex;align-items:flex-start;gap:10px;padding:12px 10px;">
        <input type="checkbox" class="network-checkbox" data-id="${item._id}" onclick="event.stopPropagation();" style="margin-top:3px;">
        <div style="flex:1;cursor:pointer;min-width:0;" data-id="${item._id}" class="list-item-content">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
            <strong style="font-size:14px;">${escapeHtml(item.commercial_name || item.description || 'Χωρίς όνομα')}</strong>
            ${eopyy}${icd}
          </div>
          <div style="display:flex;align-items:center;gap:8px;margin-top:3px;flex-wrap:wrap;">
            ${code ? `<span style="font-family:monospace;font-size:12px;color:var(--sea);">${escapeHtml(code)}</span>` : ''}
            ${cat}
            ${price ? `<span style="font-weight:600;font-size:13px;">${price}</span>` : ''}
          </div>
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0;">
          <button class="icon-btn edit-item" data-id="${item._id}" title="Επεξεργασία">✏️</button>
          <button class="icon-btn delete-item" data-id="${item._id}" title="Διαγραφή">🗑️</button>
        </div>
      </div>`;
    }
    
    // Default for other entities
    return `
    <div class="list-item" data-id="${item._id}" style="display:flex;align-items:center;gap:8px;">
      <input type="checkbox" class="network-checkbox" data-id="${item._id}" onclick="event.stopPropagation();">
      <div style="flex:1;cursor:pointer;" data-id="${item._id}" class="list-item-content">
        <strong>${formatItemTitle(item, entity)}</strong>
        <div class="text-sm text-muted">${formatItemSubtitle(item, entity)}</div>
      </div>
      <div>
        <button class="icon-btn edit-item" data-id="${item._id}" title="Επεξεργασία">✏️</button>
        <button class="icon-btn delete-item" data-id="${item._id}" title="Διαγραφή">🗑️</button>
      </div>
    </div>`;
  }).join('');

  // Bind edit/delete
  el.querySelectorAll('.edit-item').forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); openEditForm(btn.dataset.id); }));
  el.querySelectorAll('.delete-item').forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); deleteItem(btn.dataset.id); }));
  
  // Bind click-to-view on content area
  el.querySelectorAll('.list-item-content').forEach(div => {
    div.addEventListener('click', () => openDetailView(div.dataset.id));
  });
}

function formatItemTitle(item, entity) {
  if (activeEntity === 'skus') return `${item.product_id || item.eopyy_code || '—'} — ${item.commercial_name || item.description || 'Χωρίς όνομα'}`;
  if (activeEntity === 'doctors') return `${item.name} · ${item.specialty || ''}`;
  if (activeEntity === 'structures') return `${item.name} · ${item.type || ''}`;
  if (activeEntity === 'contacts') return `${item.name} · ${item.role_title || ''}`;
  if (activeEntity === 'sources') return `${item.name} · ${item.type || ''}`;
  return item.name || item.commercial_name || item._id;
}

function formatItemSubtitle(item, entity) {
  if (activeEntity === 'skus') return `${item.category || ''} · ${item.reimb_price ? item.reimb_price + '€' : ''}`;
  if (activeEntity === 'structures') return `${item.city || ''} ${item.prefecture || ''}`;
  if (activeEntity === 'doctors') {
    const structNames = (item.structure_ids || []).map(id => _structureName(id)).filter(Boolean);
    return `${item.area || ''} · ${item.workplace_type || ''} · ${item.phone || ''}${structNames.length ? ' · ' + structNames.join(', ') : ''}`;
  }
  return '';
}

function updatePagination() {
  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);
  document.getElementById('network-prev').disabled = page <= 0;
  document.getElementById('network-next').disabled = page >= maxPage;
  document.getElementById('network-count').textContent = `${page * PAGE_SIZE + 1}-${Math.min((page + 1) * PAGE_SIZE, total)} από ${total}`;
}

// ── Detail view (read-only card) ──────────────────────────────────────────────

function openDetailView(id) {
  const entity = ENTITIES[activeEntity];
  const item = items.find(i => i._id === id);
  if (!item) return;

  const title = `${entity.icon} ${formatItemTitle(item, entity)}`;

  // Build fields table
  const fieldsHtml = entity.fields.map(f => {
    const val = item[f];
    const label = fieldLabels[f] || f;
    const display = formatFieldValue(f, val);
    return `
      <div class="flex-between" style="padding:10px 0;border-bottom:1px solid var(--c-border-light);">
        <span class="text-sm" style="color:var(--c-text-secondary);font-weight:600;">${label}</span>
        <span class="text-sm" style="text-align:right;max-width:60%;word-break:break-word;">${display || '<span class="text-muted">—</span>'}</span>
      </div>
    `;
  }).join('');

  // Metrics (for doctors)
  let metricsHtml = '';
  if (entity.metrics) {
    const metricRows = entity.metrics.map(m => {
      const val = item[m];
      const label = fieldLabels[m] || m;
      const display = formatFieldValue(m, val);
      return `
        <div class="flex-between" style="padding:10px 0;border-bottom:1px solid var(--c-border-light);">
          <span class="text-sm" style="color:var(--c-primary);font-weight:600;">${label}</span>
          <span class="text-sm" style="text-align:right;">${display || '<span class="text-muted">—</span>'}</span>
        </div>
      `;
    }).join('');
    if (metricRows) {
      metricsHtml = `<div style="margin-top:8px;"><h4 class="text-sm" style="color:var(--c-primary);margin-bottom:4px;">📊 Μετρικές</h4>${metricRows}</div>`;
    }
  }

  // Structures section (doctor only)
  let structuresHtml = '';
  if (activeEntity === 'doctors') {
    const sids = item.structure_ids || [];
    const structNames = sids.map(id => _structureName(id)).filter(Boolean);
    if (structNames.length) {
      structuresHtml = `<div style="margin-top:8px;padding:10px;background:var(--sea-mist);border-radius:8px;"><h4 class="text-sm" style="color:var(--c-primary);margin-bottom:4px;">🏥 Συσχετισμένες δομές</h4>${structNames.map(n => `<div class="text-sm" style="padding:3px 0;">• ${escapeHtml(n)}</div>`).join('')}</div>`;
    } else {
      structuresHtml = '<div class="text-sm text-muted" style="padding:8px 0;">Καμία συσχετισμένη δομή</div>';
    }
  }

  openModal(title, `
    <div style="max-height:60vh;overflow-y:auto;">
      ${fieldsHtml}
      ${structuresHtml}
      ${metricsHtml}
      <div class="flex-between mt-3" style="border-top:1px solid var(--c-border);padding-top:12px;">
        <button id="detail-delete-btn" class="btn-danger btn-sm">🗑️ Διαγραφή</button>
        <button id="detail-edit-btn" class="btn-primary btn-sm">✏️ Επεξεργασία</button>
      </div>
      <p id="detail-error" class="error-msg hidden mt-2"></p>
    </div>
  `);

  document.getElementById('detail-edit-btn').addEventListener('click', () => {
    closeModal();
    setTimeout(() => openEditForm(id), 200);
  });

  document.getElementById('detail-delete-btn').addEventListener('click', async () => {
    const entity = ENTITIES[activeEntity];
    const itemName = formatItemTitle(item, entity);
    if (!confirm(`Σίγουρα θες να διαγράψεις «${itemName}»;`)) return;
    const backup = { ...item };

    try {
      await del(`/${activeEntity}/${id}`);
      closeModal();
      await loadItems();
      showToast(`🗑️ «${itemName}» διαγράφηκε`, 'warning', 5000, async () => {
        if (backup) {
          const fields = {};
          entity.fields.forEach(f => { if (backup[f] !== undefined && backup[f] !== null) fields[f] = backup[f]; });
          delete fields._id; delete fields.org_id; delete fields.created_at; delete fields.updated_at;
          await post(`/${activeEntity}`, fields);
          await loadItems();
          showToast('✅ Επαναφέρθηκε!', 'success');
        }
      });
    } catch (err) {
      document.getElementById('detail-error').textContent = err.message;
      document.getElementById('detail-error').classList.remove('hidden');
    }
  });
}

function formatFieldValue(field, val) {
  if (val === null || val === undefined || val === '') return null;
  if (field === 'reimb_price') return parseFloat(val).toFixed(2) + ' €';
  if (field === 'patient_copay_pct') return parseFloat(val).toFixed(0) + '%';
  if (field === 'avg_pickup_days' && val !== null && val !== undefined) return parseFloat(val).toFixed(1) + ' ημέρες';
  if (field === 'last_visit_at' && val) {
    try { return new Date(val).toLocaleDateString('el-GR'); } catch(e) { return val; }
  }
  return String(val);
}
function openEditForm(id) {
  const entity = ENTITIES[activeEntity];
  const item = id ? items.find(i => i._id === id) : null;
  const title = id ? `Επεξεργασία ${entity.label}` : `Νέο ${entity.label}`;

  // For doctors: add structure_ids as a separate section (not in fields loop)
  const isDoctor = activeEntity === 'doctors';
  const doctorStructureIds = (item?.structure_ids || []).filter(Boolean);

  const fieldsHtml = entity.fields.filter(f => f !== 'structure_ids').map(f => {
    const val = item ? (item[f] || '') : '';
    const label = fieldLabels[f] || f;

    // Specialty: use dropdown with valid enum values
    if (f === 'specialty') {
      const options = ['SURGEON','INTERNIST','PHYSIATRIST','ORTHOPEDIC','NEUROLOGIST','DERMATOLOGIST','UROLOGIST','ONCOLOGIST','PULMONOLOGIST','GASTROENTEROLOGIST','ENDOCRINOLOGIST','CARDIOLOGIST','NEPHROLOGIST','GERIATRICIAN','GP','HEAD_NURSE','WOUND_NURSE','STOMA_NURSE','DIETITIAN','PHYSIO','SECRETARY','OTHER']
        .map(s => `<option value="${s}" ${val === s ? 'selected' : ''}>${s}</option>`).join('');
      return `<div class="form-group"><label>${label}</label><select id="edit-${f}" class="form-group">${options}</select></div>`;
    }

    if (f === 'workplace_type') {
      const options = ['HOSPITAL','PRIVATE_CLINIC','FREELANCER','HEALTH_CENTER','NURSING_HOME','OTHER']
        .map(s => `<option value="${s}" ${val === s ? 'selected' : ''}>${s}</option>`).join('');
      return `<div class="form-group"><label>${label}</label><select id="edit-${f}" class="form-group">${options}</select></div>`;
    }

    if (f === 'type' && activeEntity === 'structures') {
      const options = ['PUBLIC_HOSP','PRIVATE_CLINIC','REHAB','ELDERLY_HOME','DIALYSIS','PHARMACY','HOMECARE_ORG','PRACTICE','OTHER']
        .map(s => `<option value="${s}" ${val === s ? 'selected' : ''}>${s}</option>`).join('');
      return `<div class="form-group"><label>${label}</label><select id="edit-${f}" class="form-group">${options}</select></div>`;
    }

    if (f === 'type' && activeEntity === 'sources') {
      const options = ['PHYSIO','HOMECARE_CREW','NURSE','PATIENT_ASSOC','DOCTOR','WORD_OF_MOUTH','OTHER']
        .map(s => `<option value="${s}" ${val === s ? 'selected' : ''}>${s}</option>`).join('');
      return `<div class="form-group"><label>${label}</label><select id="edit-${f}" class="form-group">${options}</select></div>`;
    }

    if (f === 'notes' && isDoctor) {
      return `<div class="form-group">
        <label>${label}</label>
        <textarea id="edit-${f}" class="form-group" rows="5" style="resize:vertical;font-family:var(--font-ui);" placeholder="Γράψε λεπτομέρειες για τον γιατρό ώστε η AI να προτείνει στρατηγική. Π.χ.:

• Χαρακτήρας / προσωπικότητα
• Τι τον ενδιαφέρει (ποιότητα, τιμή, καινοτομία, κλινικές μελέτες;)
• Προτιμώμενα προϊόντα ή κατηγορίες
• Tips για την επικοινωνία μαζί του
• Ιστορικό συνεργασίας">${escapeHtml(String(val))}</textarea>
      </div>`;
    }

    if (f === 'notes') {
      return `<div class="form-group">
        <label>${label}</label>
        <textarea id="edit-${f}" class="form-group" rows="3" style="resize:vertical;font-family:var(--font-ui);">${escapeHtml(String(val))}</textarea>
      </div>`;
    }

    return `<div class="form-group">
      <label>${label}</label>
      <input type="text" id="edit-${f}" value="${escapeHtml(String(val))}" placeholder="${label}">
    </div>`;
  }).join('');

  // Structure association section (doctor only)
  const structureSection = isDoctor ? `
    <div class="form-group" style="margin-top:12px;padding:12px;background:var(--sea-mist);border-radius:8px;">
      <label style="font-weight:700;">🏥 Δομές που εργάζεται</label>
      <p class="text-sm text-muted" style="margin:4px 0 8px;">Επίλεξε τις δομές υγείας όπου εργάζεται ο γιατρός. Αν δεν υπάρχει η δομή, δημιούργησέ την πρώτα από την καρτέλα «Δομές».</p>
      <div id="doctor-structures-list" style="max-height:180px;overflow-y:auto;margin-bottom:8px;">
        <div class="text-sm text-muted">Φόρτωση δομών...</div>
      </div>
    </div>
  ` : '';

  const bodyHtml = `
    <form id="network-edit-form">
      ${fieldsHtml}
      ${structureSection}
      <div class="flex-between mt-2">
        ${id ? `<button type="button" id="edit-delete-btn" class="btn-danger btn-sm">Διαγραφή</button>` : ''}
        <button type="submit" class="btn-primary btn-sm">Αποθήκευση</button>
      </div>
      <p id="edit-error" class="error-msg hidden"></p>
    </form>
  `;

  openModal(title, bodyHtml);
  const form = document.getElementById('network-edit-form');

  // Load structures for doctor association
  if (isDoctor) {
    loadDoctorStructures(doctorStructureIds);
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('edit-error');
    errEl.classList.add('hidden');

    const data = {};
    const displayFields = entity.fields.filter(f => f !== 'structure_ids');
    displayFields.forEach(f => {
      const el = document.getElementById(`edit-${f}`);
      if (!el) return;
      const val = el.value.trim();
      if (val) data[f] = f === 'reimb_price' || f === 'max_qty_month' || f === 'patient_copay_pct' ? parseFloat(val) : val;
    });

    // Collect selected structure IDs for doctors
    if (isDoctor) {
      const checkedBoxes = document.querySelectorAll('.doctor-structure-cb:checked');
      data['structure_ids'] = Array.from(checkedBoxes).map(cb => cb.value);
    }

    try {
      if (id) {
        await patch(`/${activeEntity}/${id}`, data);
      } else {
        await post(`/${activeEntity}`, data);
      }
      closeModal();
      await loadItems();
    } catch (err) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  });

  const delBtn = document.getElementById('edit-delete-btn');
  if (delBtn) {
    delBtn.addEventListener('click', async () => {
      if (!confirm('Σίγουρα θέλεις να διαγράψεις αυτή την εγγραφή;')) return;
      try {
        await del(`/${activeEntity}/${id}`);
        closeModal();
        await loadItems();
      } catch (err) {
        alert(err.message);
      }
    });
  }
}

async function loadDoctorStructures(selectedIds) {
  const container = document.getElementById('doctor-structures-list');
  if (!container) return;
  try {
    // Use cache if available, otherwise fetch
    if (_structureCache === null) await _loadStructureCache();
    
    const structures = _structureCache || {};
    const ids = Object.keys(structures);
    if (!ids.length) {
      container.innerHTML = '<div class="text-sm text-muted">Δεν υπάρχουν δομές. Δημιούργησε πρώτα από την καρτέλα «Δομές».</div>';
      return;
    }
    container.innerHTML = ids.map(id => {
      const name = structures[id];
      const checked = selectedIds.includes(id) ? 'checked' : '';
      return `<label style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--c-border-light);cursor:pointer;font-size:13px;">
        <input type="checkbox" class="doctor-structure-cb" value="${id}" ${checked}>
        <span>${escapeHtml(name)}</span>
      </label>`;
    }).join('');
  } catch (e) {
    container.innerHTML = '<div class="text-sm text-danger">Σφάλμα φόρτωσης δομών: ' + (e.message || e) + '</div>';
  }
}

async function deleteItem(id) {
  const item = items.find(i => i._id === id);
  const entity = ENTITIES[activeEntity];
  const itemName = item ? formatItemTitle(item, entity) : 'εγγραφή';

  if (!confirm(`Διαγραφή «${itemName}»;`)) return;

  // Store for undo
  const backup = item ? { ...item } : null;

  try {
    await del(`/${activeEntity}/${id}`);
    await loadItems();
    showToast(`🗑️ «${itemName}» διαγράφηκε`, 'warning', 5000, async () => {
      // Undo: re-create
      if (backup) {
        const fields = {};
        entity.fields.forEach(f => { if (backup[f] !== undefined && backup[f] !== null) fields[f] = backup[f]; });
        delete fields._id; delete fields.org_id; delete fields.created_at; delete fields.updated_at;
        await post(`/${activeEntity}`, fields);
        await loadItems();
        showToast('✅ Επαναφέρθηκε!', 'success');
      }
    });
  } catch (err) {
    showToast('❌ Σφάλμα διαγραφής', 'error');
  }
}

// ── Helpers ─────────────────────────────────────────────────────────────────

const fieldLabels = {
  product_id: 'Κωδικός Προϊόντος (Product ID)',
  eopyy_code: 'Κωδικός ΕΟΠΥΥ',
  commercial_name: 'Εμπορική ονομασία',
  description: 'Περιγραφή',
  category: 'Κατηγορία',
  icd10_code: 'Κωδικός ICD-10',
  reimb_price: 'Τιμή αποζημίωσης',
  patient_copay_pct: 'Συμμετοχή ασθενή',
  max_qty_month: 'Μέγιστη ποσότητα/μήνα',
  duration_days_default: 'Διάρκεια (ημέρες)',
  name: 'Όνομα',
  specialty: 'Ειδικότητα / Ρόλος',
  type: 'Τύπος',
  role_title: 'Ρόλος',
  structure_id: 'Δομή (ID)',
  city: 'Πόλη',
  prefecture: 'Νομός',
  address: 'Διεύθυνση',
  phone: 'Τηλέφωνο',
  notes: 'Σημειώσεις',
  rx_count_90d: 'Γνωματεύσεις (90 ημ.)',
  avg_pickup_days: 'Μ.Ο. χρόνος σηκώματος',
  open_cases_count: 'Ανοιχτά περιστατικά',
  last_visit_at: 'Τελευταία επίσκεψη',
  workplace_type: 'Χώρος εργασίας',
  email: 'Email',
  area: 'Περιοχή',
  product_categories: 'Κατηγορίες προϊόντων',
};

function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function _loadStructureCache() {
  try {
    // Fetch ALL structures (paginated)
    let allStructures = [];
    let page = 0;
    while (true) {
      const data = await get(`/structures?skip=${page * 200}&limit=200`);
      allStructures = allStructures.concat(data.items || []);
      if (!data.items || data.items.length < 200) break;
      page++;
    }
    _structureCache = {};
    for (const s of allStructures) {
      _structureCache[s._id] = s.name;
    }
  } catch (e) {
    _structureCache = {};
    console.warn('Structure cache load failed:', e);
  }
}

function _structureName(id) {
  if (!_structureCache || !id) return '';
  return _structureCache[id] || _structureCache[String(id)] || '';
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

// ── AI Search ───────────────────────────────────────────────────────────────

async function aiSearch() {
  const input = document.getElementById('network-search');
  const q = input.value.trim();
  if (!q || q.length < 2) return;

  const listEl = document.getElementById('network-list');
  const answerEl = document.getElementById('network-ai-answer');
  const btn = document.getElementById('network-ai-btn');

  listEl.innerHTML = '<div class="flex-center" style="padding:40px;"><div class="spinner"></div></div>';
  btn.textContent = '⏳';

  try {
    // Use the AI agent (Askliπιος) instead of broken /search/ai
    const { post } = await import('../api.js');
    const msg = activeEntity === 'staff' ? `Ψάξε στο υγειονομικό προσωπικό: ${q}` :
                activeEntity === 'structures' ? `Ψάξε στις δομές: ${q}` :
                activeEntity === 'sources' ? `Ψάξε στις πηγές: ${q}` :
                activeEntity === 'skus' ? `Ψάξε στα προϊόντα: ${q}` : q;
    
    const result = await post('/llm/agent', { message: msg });

    // Show AI answer
    if (result.response) {
      answerEl.innerHTML = '<strong>🤖 AI:</strong> ' + escapeHtml(result.response);
      answerEl.classList.remove('hidden');
    } else {
      answerEl.classList.add('hidden');
    }

    // Also do regular search for the list
    const search = q;
    const params = new URLSearchParams({ skip: 0, limit: 30 });
    if (search) params.set('search', search);
    const data = await get(`/${activeEntity}?${params}`);
    items = data.items;
    total = data.total;
    renderList();
    updatePagination();
  } catch (err) {
    listEl.innerHTML = '<div class="list-empty text-danger">Σφάλμα: ' + (err.message || 'κάτι πήγε στραβά') + '</div>';
    answerEl.classList.add('hidden');
  } finally {
    btn.textContent = '🤖';
  }
}

// ── Bulk delete ─────────────────────────────────────────────────────────────

async function bulkDelete() {
  const checked = document.querySelectorAll('.network-checkbox:checked');
  if (!checked.length) return;
  
  const ids = Array.from(checked).map(cb => cb.dataset.id);
  const entityName = ENTITIES[activeEntity].label;
  
  if (!confirm(`Διαγραφή ${ids.length} ${entityName.toLowerCase()}; Αυτή η ενέργεια ΔΕΝ αναιρείται.`)) return;

  const btn = document.getElementById('network-bulk-delete');
  btn.disabled = true;
  btn.textContent = '⏳ Διαγραφή...';
  
  let deleted = 0;
  let failed = 0;
  
  for (const id of ids) {
    try {
      await del(`/${activeEntity}/${id}`);
      deleted++;
    } catch (e) {
      failed++;
    }
  }
  
  btn.disabled = false;
  btn.textContent = '🗑️ Διαγραφή επιλεγμένων';
  
  const { showToast } = await import('../app.js');
  if (failed) {
    showToast(`Διαγράφηκαν ${deleted}, απέτυχαν ${failed}`, 'warning');
  } else {
    showToast(`✅ Διαγράφηκαν ${deleted} ${entityName.toLowerCase()}`, 'success');
  }
  
  await loadItems();
}

// ── Delete ALL (across all pages) ─────────────────────────────────────────

async function bulkDeleteAll() {
  const entityName = ENTITIES[activeEntity].label;
  if (!confirm(`⚠️ ΟΡΙΣΤΙΚΗ ΔΙΑΓΡΑΦΗ ΟΛΩΝ των ${entityName.toLowerCase()}; (${total} εγγραφές)\n\nΑυτή η ενέργεια ΔΕΝ αναιρείται.`)) return;

  const btn = document.getElementById('network-bulk-delete-all');
  btn.disabled = true;
  btn.textContent = '⏳ Διαγραφή όλων...';
  
  let allDeleted = 0;
  let pageNum = 0;
  const pageSize = 100;
  
  while (true) {
    try {
      const data = await get(`/${activeEntity}?skip=${pageNum * pageSize}&limit=${pageSize}`);
      if (!data.items.length) break;
      
      for (const item of data.items) {
        try { await del(`/${activeEntity}/${item._id}`); allDeleted++; } catch (e) { /* skip */ }
      }
      
      if (data.items.length < pageSize) break;
      pageNum++;
    } catch (e) { break; }
  }
  
  btn.disabled = false;
  btn.textContent = '🗑️💣 Όλα';
  
  const { showToast } = await import('../app.js');
  showToast(`✅ Διαγράφηκαν ΟΛΑ: ${allDeleted} ${entityName.toLowerCase()}`, 'success');
  
  page = 0;
  await loadItems();
}
