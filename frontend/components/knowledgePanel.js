/**
 * RxTrail — Knowledge Panel: Upload Excel, manage RAG knowledge base.
 * 
 * Provides:
 *   - Drag & drop Excel upload (ALL tabs parsed)
 *   - File list with tab count, chunk count, date
 *   - Delete button
 *   - Quick search for testing retrieval quality
 */
import { api, getToken } from '../api.js';

let refreshCallback = null;

/**
 * Render the knowledge panel into a container element.
 */
export async function renderKnowledgePanel(container) {
  container.innerHTML = `
    <div class="knowledge-panel">
      <div class="kp-header">
        <h2>📚 Βάση Γνώσης (RAG)</h2>
        <p class="kp-sub">Ανέβασε Excel με οδηγίες συνταγογράφησης. Όλα τα tabs διαβάζονται. 
        Το AI θα συμβουλεύεται αυτή τη γνώση πριν απαντήσει.</p>
      </div>
      
      <div class="kp-upload-zone" id="kp-upload-zone">
        <div class="kp-upload-icon">📤</div>
        <div class="kp-upload-text">
           <strong>Σύρε ένα αρχείο εδώ</strong> ή κάνε κλικ για επιλογή<br>
            <small>.xlsx, .xls, .json, .md — μέχρι 10MB</small>
         </div>
          <input type="file" id="kp-file-input" accept=".xlsx,.xls,.json,.md" style="display:none">
        <div id="kp-upload-status" class="kp-status"></div>
      </div>
      
      <div class="kp-files-section">
        <h3>📋 Ανέβασμένα Αρχεία</h3>
        <div id="kp-files-list" class="kp-files-list">
          <div class="kp-empty">Φόρτωση...</div>
        </div>
      </div>
      
      <div class="kp-search-section">
        <h3>🔍 Δοκιμή Αναζήτησης</h3>
        <div class="kp-search-row">
          <input type="text" id="kp-search-input" placeholder="π.χ. Actibutir, K58, τραύμα..." 
                 style="flex:1;padding:var(--sp-3);border:1px solid var(--line);border-radius:var(--r-btn);font-family:var(--font-ui);">
          <button id="kp-search-btn" style="padding:var(--sp-3) var(--sp-5);background:var(--sea);color:#fff;border:none;border-radius:var(--r-btn);cursor:pointer;font-weight:600;">Αναζήτηση</button>
        </div>
        <div id="kp-search-results" class="kp-search-results"></div>
      </div>
    </div>
  `;

  // Bind events
  setupUploadZone(container);
  await refreshFileList(container);
  setupSearch(container);
  setupStyles();
}

function setupUploadZone(container) {
  const zone = container.querySelector('#kp-upload-zone');
  const input = container.querySelector('#kp-file-input');
  const status = container.querySelector('#kp-upload-status');
  
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('kp-dragging'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('kp-dragging'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('kp-dragging');
    const file = e.dataTransfer.files[0];
    if (file) handleUpload(file, status, container);
  });
  input.addEventListener('change', () => {
    const file = input.files[0];
    if (file) handleUpload(file, status, container);
  });
}

async function handleUpload(file, statusEl, container) {
  if (!file.name.match(/\.(xlsx|xls|json|md)$/i)) {
    statusEl.innerHTML = '<span style="color:var(--danger)">❌ Μόνο Excel, JSON ή Markdown</span>';
    return;
  }
  
  statusEl.innerHTML = '<span style="color:var(--sea)">⏳ Ανέβασμα & επεξεργασία όλων των tabs...</span>';
  
  try {
    const formData = new FormData();
    formData.append('file', file);
    
    const resp = await fetch('/api/knowledge/upload', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${getToken()}` },
      body: formData,
    });
    
    if (!resp.ok) {
      const raw = await resp.text();
      let errData = {};
      try { errData = JSON.parse(raw); } catch { throw new Error('Server error: ' + raw.slice(0,200)); }
      throw new Error(errData.error || errData.detail || 'Upload failed');
    }
    
    const raw2 = await resp.text();
    let data = {};
    try { data = JSON.parse(raw2); } catch { throw new Error('Invalid response: ' + raw2.slice(0,200)); }

    const tabNames = Array.isArray(data.tab_names) ? data.tab_names.join(', ') : (data.tab_names || '');
    statusEl.innerHTML = '<span style="color:var(--success)">OK ' + (data.filename || file.name) + ' uploaded! ' + (data.count || data.total_chunks || 0) + ' entries.' + (tabNames ? ' Tabs: ' + tabNames : '') + '</span>';
    
    await refreshFileList(container);
  } catch (e) {
    statusEl.innerHTML = `<span style="color:var(--danger)">❌ Σφάλμα: ${e.message}</span>`;
  }
}

async function refreshFileList(container) {
  const listEl = container.querySelector('#kp-files-list');
  if (!listEl) return;
  
  try {
    const resp = await fetch('/api/knowledge/files', {
      headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    if (!resp.ok) throw new Error('Failed to fetch files');
    const files = await resp.json();
    
    if (!files.length) {
      listEl.innerHTML = '<div class="kp-empty">Δεν έχουν ανέβει αρχεία ακόμα. Ανέβασε το πρώτο σου Excel.</div>';
      return;
    }
    
    listEl.innerHTML = files.map(f => `
      <div class="kp-file-card">
        <div class="kp-file-info">
          <div class="kp-file-name">📄 ${escapeHtml(f.filename)}</div>
          <div class="kp-file-meta">
            ${f.chunk_count || 0} entries - ${(Array.isArray(f.tab_names) ? f.tab_names.length : 0)} tabs - 
            ${Array.isArray(f.tab_names) ? f.tab_names.slice(0,5).join(', ') : ''}
            <br><small>${f.created_at || ''}</small>
          </div>
        </div>
        <button class="kp-delete-btn" data-file-id="${f.file_id}" title="Διαγραφή">🗑️</button>
      </div>
    `).join('');
    
    // Bind delete buttons
    listEl.querySelectorAll('.kp-delete-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm('Διαγραφή αυτού του αρχείου και όλων των εγγραφών του;')) return;
        const fileId = btn.dataset.fileId;
        try {
          const resp = await fetch(`/api/knowledge/${fileId}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${getToken()}` },
          });
          if (resp.ok) await refreshFileList(container);
        } catch (e) {
          alert('Σφάλμα διαγραφής: ' + e.message);
        }
      });
    });
  } catch (e) {
    listEl.innerHTML = `<div class="kp-empty" style="color:var(--danger)">Σφάλμα φόρτωσης: ${e.message}</div>`;
  }
}

function setupSearch(container) {
  const input = container.querySelector('#kp-search-input');
  const btn = container.querySelector('#kp-search-btn');
  const results = container.querySelector('#kp-search-results');
  
  async function doSearch() {
    const q = input.value.trim();
    if (!q) return;
    results.innerHTML = '<div style="color:var(--ink-soft)">⏳ Αναζήτηση...</div>';
    
    try {
      const resp = await fetch(`/api/knowledge/search?q=${encodeURIComponent(q)}&top_k=5`, {
        headers: { 'Authorization': `Bearer ${getToken()}` },
      });
      const data = await resp.json();
      
      if (!data.results?.length) {
        results.innerHTML = '<div style="color:var(--ink-soft)">Δεν βρέθηκαν αποτελέσματα.</div>';
        return;
      }
      
      results.innerHTML = data.results.map((r, i) => `
        <div class="kp-result-item">
          <div class="kp-result-header">
            <span class="kp-result-rank">#${i+1}</span>
            <span class="kp-result-tab">[${escapeHtml(r.tab_name)}]</span>
            <span class="kp-result-score">συνάφεια: ${r.score}</span>
          </div>
          <div class="kp-result-text">${escapeHtml(r.chunk_text)}</div>
        </div>
      `).join('');
    } catch (e) {
      results.innerHTML = `<div style="color:var(--danger)">Σφάλμα: ${e.message}</div>`;
    }
  }
  
  btn.addEventListener('click', doSearch);
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function setupStyles() {
  if (document.getElementById('kp-styles')) return;
  const style = document.createElement('style');
  style.id = 'kp-styles';
  style.textContent = `
    .knowledge-panel { padding: var(--sp-5); max-width: 800px; margin: 0 auto; }
    .kp-header { margin-bottom: var(--sp-5); }
    .kp-header h2 { font-size: 22px; color: var(--ink); margin: 0 0 var(--sp-2) 0; }
    .kp-sub { color: var(--ink-soft); font-size: 14px; line-height: 1.5; margin: 0; }
    
    .kp-upload-zone {
      border: 2px dashed var(--line); border-radius: 12px; padding: var(--sp-6);
      text-align: center; cursor: pointer; transition: all var(--t-fast);
      margin-bottom: var(--sp-5); background: var(--bg-card);
    }
    .kp-upload-zone:hover, .kp-upload-zone.kp-dragging {
      border-color: var(--sea); background: var(--sea-mist);
    }
    .kp-upload-icon { font-size: 40px; margin-bottom: var(--sp-2); }
    .kp-upload-text strong { color: var(--sea); }
    .kp-upload-text small { color: var(--ink-faint); }
    .kp-status { margin-top: var(--sp-3); font-size: 14px; }
    
    .kp-files-section { margin-bottom: var(--sp-5); }
    .kp-files-section h3, .kp-search-section h3 { 
      font-size: 16px; color: var(--ink); margin: 0 0 var(--sp-3) 0; 
    }
    .kp-files-list { display: flex; flex-direction: column; gap: var(--sp-2); }
    .kp-empty { color: var(--ink-soft); font-size: 14px; padding: var(--sp-4); text-align: center; }
    .kp-file-card {
      display: flex; align-items: center; justify-content: space-between;
      padding: var(--sp-3) var(--sp-4); background: var(--bg-card);
      border: 1px solid var(--line); border-radius: var(--r-btn);
    }
    .kp-file-name { font-weight: 600; color: var(--ink); font-size: 14px; }
    .kp-file-meta { font-size: 12px; color: var(--ink-soft); margin-top: 2px; }
    .kp-delete-btn { 
      background: none; border: none; font-size: 18px; cursor: pointer; 
      padding: var(--sp-2); border-radius: var(--r-btn); 
    }
    .kp-delete-btn:hover { background: var(--bg-warm); }
    
    .kp-search-row { display: flex; gap: var(--sp-2); margin-bottom: var(--sp-3); }
    .kp-search-results { display: flex; flex-direction: column; gap: var(--sp-2); }
    .kp-result-item {
      padding: var(--sp-3) var(--sp-4); background: var(--bg-card);
      border: 1px solid var(--line); border-radius: var(--r-btn);
    }
    .kp-result-header { display: flex; gap: var(--sp-3); align-items: center; margin-bottom: var(--sp-1); }
    .kp-result-rank { font-weight: 700; color: var(--sea); font-size: 13px; }
    .kp-result-tab { font-size: 12px; color: var(--ink-soft); background: var(--sea-mist); padding: 1px 6px; border-radius: 4px; }
    .kp-result-score { font-size: 11px; color: var(--ink-faint); margin-left: auto; }
    .kp-result-text { font-size: 13px; color: var(--ink); line-height: 1.5; word-break: break-all; }
  `;
  document.head.appendChild(style);
}
