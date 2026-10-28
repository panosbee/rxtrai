/**
 * RxTrail — Document component: upload, list, preview, delete per case.
 * Reusable — embed in case detail modal.
 */
import { get, post, del, patch, uploadFile } from '../api.js';

/**
 * Render document section inside a case detail.
 * @param {string} caseId
 * @returns {string} HTML
 */
export function renderDocSection(caseId) {
  return `
    <div class="mt-2" style="border-top:1px solid var(--c-border);padding-top:12px;" id="docs-section-${caseId}">
      <div class="flex-between mb-1">
        <strong>📎 Αρχεία</strong>
        <button class="btn-text btn-sm" id="add-doc-btn-${caseId}">+ Προσθήκη</button>
      </div>
      <div id="docs-list-${caseId}"><span class="text-sm text-muted">Φόρτωση...</span></div>
      <input type="file" id="doc-file-input-${caseId}" accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,image/*" style="display:none;">
    </div>
  `;
}

/**
 * Load and display documents for a case.
 * @param {string} caseId
 */
export async function loadDocs(caseId) {
  const listEl = document.getElementById(`docs-list-${caseId}`);
  if (!listEl) return;

  try {
    const data = await get(`/documents/by-case/${caseId}`);
    const docs = data.items || [];

    if (!docs.length) {
      listEl.innerHTML = '<div class="text-sm text-muted">Κανένα αρχείο</div>';
    } else {
      listEl.innerHTML = docs.map(d => `
        <div class="card" style="padding:8px 12px;margin-bottom:6px;">
          <div class="flex-between">
            <div style="flex:1;min-width:0;">
              <strong style="font-size:13px;">${docIcon(d.mime_type)} ${es(d.name)}</strong>
              <div class="text-sm text-muted">${formatSize(d.file_size)} · ${new Date(d.created_at).toLocaleDateString('el-GR')}${d.knowledge_status === 'indexed' ? ' · 🤖 στη Γνώση' : ''}</div>
            </div>
            <div style="display:flex;gap:4px;flex-shrink:0;">
              <button class="icon-btn preview-doc-btn" data-url="${d.url}" data-mime="${d.mime_type}" title="Προεπισκόπηση" style="font-size:16px;">👁️</button>
              <button class="icon-btn rename-doc-btn" data-id="${d._id}" data-name="${es(d.name)}" title="Μετονομασία" style="font-size:16px;">✏️</button>
              <button class="icon-btn delete-doc-btn" data-id="${d._id}" title="Διαγραφή" style="font-size:16px;">🗑️</button>
            </div>
          </div>
        </div>
      `).join('');
    }

    // Bind buttons
    bindDocButtons(caseId);
  } catch (err) {
    listEl.innerHTML = `<div class="text-sm text-danger">Σφάλμα: ${err.message}</div>`;
  }
}

function bindDocButtons(caseId) {
  // Add document button
  const addBtn = document.getElementById(`add-doc-btn-${caseId}`);
  const fileInput = document.getElementById(`doc-file-input-${caseId}`);
  if (addBtn && fileInput) {
    addBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => handleDocUpload(caseId, fileInput));
  }

  // Preview buttons
  document.querySelectorAll(`.preview-doc-btn`).forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      previewDoc(btn.dataset.url, btn.dataset.mime, btn.dataset.name);
    });
  });

  // Rename buttons
  document.querySelectorAll(`.rename-doc-btn`).forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      renameDoc(btn.dataset.id, btn.dataset.name, caseId);
    });
  });

  // Delete buttons
  document.querySelectorAll(`.delete-doc-btn`).forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Διαγραφή αρχείου;')) return;
      try {
        await del(`/documents/${btn.dataset.id}`);
        await loadDocs(caseId);
      } catch (err) {
        alert(err.message);
      }
    });
  });
}

async function handleDocUpload(caseId, fileInput) {
  const file = fileInput.files[0];
  if (!file) return;

  const name = prompt('Όνομα αρχείου (π.χ. Βεβαίωση Ιατρού):', file.name) || file.name;

  try {
    const form = new FormData();
    form.append('file', file);
    form.append('name', name);
    form.append('doc_type', file.type?.startsWith('image/') ? 'PHOTO' : 'DOCUMENT');

    const token = localStorage.getItem('rxtrail_token');
    const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
    const base = isLocal ? '' : '/api';
    const res = await fetch(`${base}/documents/upload/${caseId}`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}` },
      body: form,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Upload failed');
    }

    await loadDocs(caseId);
  } catch (err) {
    alert('Σφάλμα: ' + err.message);
  }

  fileInput.value = '';
}

function previewDoc(url, mime, name) {
  const isImage = mime && mime.startsWith('image/');
  const isPdf = mime === 'application/pdf';
  const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
  const base = isLocal ? '' : '/api';
  const token = localStorage.getItem('rxtrail_token') || '';
  const fullUrl = url.startsWith('http') ? url : `${base}${url}`;

  const title = document.getElementById('modal-title');
  const body = document.getElementById('modal-body');
  const overlay = document.getElementById('modal-overlay');
  const container = document.getElementById('modal-container');
  if (!body || !overlay) return;

  // Fetch with Authorization header → blob → object URL (works for img & iframe)
  fetch(fullUrl, { headers: { 'Authorization': `Bearer ${token}` } })
    .then(res => {
      if (!res.ok) throw new Error('Σφάλμα φόρτωσης: ' + res.status);
      return res.blob();
    })
    .then(blob => {
      const objUrl = URL.createObjectURL(blob);
      if (isImage) {
        title.textContent = '📷 ' + (name || 'Προεπισκόπηση');
        body.innerHTML = `<img src="${objUrl}" style="max-width:100%;max-height:70vh;border-radius:8px;" alt="Preview">`;
      } else if (isPdf) {
        title.textContent = '📄 ' + (name || 'PDF');
        // Use PDF.js for universal rendering (works on ALL mobiles, incl. iOS Safari)
        body.innerHTML = `
          <div style="width:100%;height:70vh;display:flex;flex-direction:column;">
            <div id="pdf-toolbar" style="display:flex;gap:8px;align-items:center;padding:6px 0;font-size:13px;">
              <button id="pdf-prev" class="btn-text btn-sm">◀</button>
              <span id="pdf-page">1 / 1</span>
              <button id="pdf-next" class="btn-text btn-sm">▶</button>
              <span style="flex:1"></span>
              <a id="pdf-download" href="${objUrl}" download="${es(name || 'αρχείο.pdf')}" class="btn-text btn-sm">📥</a>
            </div>
            <canvas id="pdf-canvas" style="flex:1;width:100%;border:1px solid var(--c-border);border-radius:8px;background:#fff;"></canvas>
          </div>`;
        renderPdf(objUrl);
      } else {
        title.textContent = '📎 ' + (name || 'Αρχείο');
        body.innerHTML = `<p>Δεν είναι δυνατή η προεπισκόπηση. <a href="${objUrl}" target="_blank" class="btn-primary btn-sm">📥 Λήψη</a></p>`;
      }
    })
    .catch(err => {
      title.textContent = '📎 ' + (name || 'Αρχείο');
      body.innerHTML = `<p class="text-danger">${err.message}</p>`;
    });

  overlay.classList.remove('hidden');
  container.classList.remove('hidden');
}

// ── PDF.js rendering (works on ALL mobile browsers) ──
let _pdfDoc = null;
let _pdfPage = 1;
let _pdfUrl = null;

async function renderPdf(url) {
  _pdfUrl = url;
  _pdfDoc = null;
  _pdfPage = 1;
  try {
    // Load PDF.js from CDN (works everywhere)
    if (!window.pdfjsLib) {
      await new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js';
        s.onload = resolve;
        s.onerror = () => reject(new Error('Δεν φόρτωσε το PDF viewer'));
        document.head.appendChild(s);
      });
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
    }
    const loadingTask = window.pdfjsLib.getDocument({ url });
    _pdfDoc = await loadingTask.promise;
    document.getElementById('pdf-page').textContent = `1 / ${_pdfDoc.numPages}`;
    renderPdfPage(1);

    document.getElementById('pdf-prev').addEventListener('click', () => { if (_pdfPage > 1) renderPdfPage(_pdfPage - 1); });
    document.getElementById('pdf-next').addEventListener('click', () => { if (_pdfPage < _pdfDoc.numPages) renderPdfPage(_pdfPage + 1); });
  } catch (e) {
    const body = document.getElementById('modal-body');
    if (body) body.innerHTML = `<p class="text-danger">Δεν μπόρεσε να ανοίξει το PDF: ${e.message}</p>`;
  }
}

async function renderPdfPage(n) {
  if (!_pdfDoc) return;
  _pdfPage = n;
  document.getElementById('pdf-page').textContent = `${n} / ${_pdfDoc.numPages}`;
  const page = await _pdfDoc.getPage(n);
  const canvas = document.getElementById('pdf-canvas');
  const ctx = canvas.getContext('2d');
  const viewport = page.getViewport({ scale: 1.5 });
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  canvas.style.maxHeight = '65vh';
  await page.render({ canvasContext: ctx, viewport }).promise;
}

function renameDoc(docId, currentName, caseId) {
  const newName = prompt('Νέο όνομα:', currentName);
  if (!newName || newName === currentName) return;

  patch(`/documents/${docId}`, { name: newName }).then(() => loadDocs(caseId)).catch(err => alert(err.message));
}

function docIcon(mime) {
  if (!mime) return '📎';
  if (mime.startsWith('image/')) return '🖼️';
  if (mime === 'application/pdf') return '📄';
  if (mime.includes('word')) return '📝';
  return '📎';
}

function formatSize(bytes) {
  if (!bytes) return '0 B';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

function es(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
