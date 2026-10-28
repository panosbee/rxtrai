/**
 * RxTrail — Κοινός Χώρος (Shared Space): cross-tenant folders & files with versioning.
 * Είσοδος: ☰ Μενού → 🗂️ Κοινός Χώρος
 */
import { get, post, patch, del, uploadFile } from '../api.js';
import { openModal, closeModal, showToast } from '../app.js';

let currentFolderId = null;
let breadcrumbs = [];
let searchMode = false;

const FILE_ICONS = { pdf: '📄', jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '🖼️', webp: '🖼️',
  xls: '📊', xlsx: '📊', doc: '📝', docx: '📝', ppt: '📽️', pptx: '📽️',
  md: '📋', json: '🧩', txt: '📄', csv: '📋' };

export async function renderShareView(container) {
  container.innerHTML = `
    <div class="flex-between mb-2">
      <h3 id="share-title">🗂️ Κοινός Χώρος</h3>
      <div style="display:flex;gap:6px;">
        <button id="share-new-folder" class="btn-secondary btn-sm" title="Νέος φάκελος">📁 +</button>
        <button id="share-upload-btn" class="btn-primary btn-sm" title="Ανέβασμα αρχείου">📤 Ανέβασμα</button>
      </div>
    </div>
    <div class="flex-between mb-2" style="gap:8px;">
      <div id="share-breadcrumb" style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;font-size:13px;"></div>
      <input type="text" id="share-search" placeholder="🔍 Αναζήτηση..." style="flex:0 0 180px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px;">
    </div>
    <input type="file" id="share-file-input" multiple style="display:none;">
    <div id="share-drop-zone" style="border:2px dashed var(--line);border-radius:12px;padding:18px;text-align:center;color:var(--ink-soft);font-size:14px;margin-bottom:12px;cursor:pointer;">
      Σύρε αρχεία εδώ ή πάτα «📤 Ανέβασμα» (μέχρι 50MB το καθένα)
    </div>
    <div id="share-list"></div>

    <div class="card mt-3" style="padding:0;overflow:hidden;">
      <button id="share-chat-toggle" type="button" style="width:100%;display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border:none;background:var(--sea-soft);cursor:pointer;font-family:var(--font-ui);">
        <span style="font-weight:750;font-size:15px;">💬 Συζήτηση ομάδας</span>
        <span id="share-chat-chevron" style="font-size:13px;color:var(--ink-soft);transition:transform var(--t-fast) var(--ease);">▼</span>
      </button>
      <div id="share-chat-body" style="display:none;">
        <div id="share-chat-messages" style="height:300px;overflow-y:auto;padding:var(--sp-3) var(--sp-4);display:flex;flex-direction:column;gap:10px;background:var(--surface-wash);"></div>
        <form id="share-chat-form" style="display:flex;gap:8px;padding:var(--sp-3) var(--sp-4);border-top:1px solid var(--line);">
          <input type="text" id="share-chat-input" placeholder="Γράψε κάτι στην ομάδα..." autocomplete="off" style="flex:1;padding:10px 14px;border:1.5px solid var(--line);border-radius:var(--r-btn);font-size:14px;font-family:var(--font-ui);">
          <button type="submit" class="btn-primary btn-sm">➤</button>
        </form>
        <div style="padding:0 var(--sp-4) var(--sp-3);font-size:11.5px;color:var(--ink-faint);">Κοινό για όλους · μένει στο ιστορικό (τελευταία 500 μηνύματα)</div>
      </div>
    </div>
  `;

  document.getElementById('share-new-folder').addEventListener('click', () => newFolderModal());
  document.getElementById('share-upload-btn').addEventListener('click', () => document.getElementById('share-file-input').click());
  document.getElementById('share-drop-zone').addEventListener('click', () => document.getElementById('share-file-input').click());
  document.getElementById('share-file-input').addEventListener('change', (e) => handleUpload(e.target.files));

  // Drag & drop
  const dz = document.getElementById('share-drop-zone');
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.style.borderColor = 'var(--c-primary)'; });
  dz.addEventListener('dragleave', () => dz.style.borderColor = 'var(--line)');
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.style.borderColor = 'var(--line)'; handleUpload(e.dataTransfer.files); });

  // Search
  const searchEl = document.getElementById('share-search');
  searchEl.addEventListener('input', debounce(() => {
    const q = searchEl.value.trim();
    if (q.length >= 2) { searchMode = true; loadSearch(q); }
    else { searchMode = false; loadFolder(); }
  }, 400));

  initChat();

  await loadFolder();
}

// ── Ομαδικό chat (accordion — κλειστό από default) ───────────────────────

let chatInterval = null;
let chatLastTs = '';
let chatMyId = localStorage.getItem('rxtrail_user_id') || '';
let chatIsAtBottom = true;
let chatLoaded = false;

async function initChat() {
  if (chatInterval) { clearInterval(chatInterval); chatInterval = null; }
  chatLoaded = false;
  const toggle = document.getElementById('share-chat-toggle');
  const body = document.getElementById('share-chat-body');
  const chevron = document.getElementById('share-chat-chevron');
  if (!toggle || !body) return;

  toggle.addEventListener('click', () => {
    const opening = body.style.display === 'none';
    body.style.display = opening ? '' : 'none';
    chevron.style.transform = opening ? 'rotate(180deg)' : '';
    if (opening) {
      startChat();
    } else {
      stopChat();
    }
  });

  document.getElementById('share-chat-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = document.getElementById('share-chat-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    try {
      const m = await post('/share/chat/messages', { text });
      chatLastTs = m.created_at;
      appendChatMessage(m);
    } catch (err) {
      showToast('❌ ' + err.message, 'error');
    }
  });
}

async function startChat() {
  const box = document.getElementById('share-chat-messages');
  if (!box) return;
  if (!chatLoaded) {
    await loadChatInitial();
    chatLoaded = true;
  }
  box.addEventListener('scroll', onChatScroll);
  if (!chatInterval) {
    chatInterval = setInterval(async () => {
      const view = document.getElementById('view-share');
      const body = document.getElementById('share-chat-body');
      if (!view || !view.classList.contains('active') || !body || body.style.display === 'none') {
        stopChat();
        return;
      }
      await pollChat();
    }, 3000);
  }
}

function stopChat() {
  if (chatInterval) { clearInterval(chatInterval); chatInterval = null; }
  const box = document.getElementById('share-chat-messages');
  if (box) box.removeEventListener('scroll', onChatScroll);
}

function onChatScroll() {
  const box = document.getElementById('share-chat-messages');
  if (box) chatIsAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
}

async function loadChatInitial() {
  const box = document.getElementById('share-chat-messages');
  if (!box) return;
  box.innerHTML = '<div class="text-faint" style="font-size:13px;text-align:center;">Φόρτωση συζήτησης...</div>';
  try {
    const data = await get('/share/chat/messages?limit=100');
    box.innerHTML = '';
    (data.items || []).forEach(appendChatMessage);
    if (data.items?.length) chatLastTs = data.items[data.items.length - 1].created_at;
    scrollChatToBottom(true);
    if (!data.items?.length) {
      box.innerHTML = '<div class="text-faint" style="font-size:13px;text-align:center;">Κανένα μήνυμα ακόμα — ξεκίνα τη συζήτηση 👇</div>';
    }
  } catch (err) {
    box.innerHTML = `<div class="text-danger" style="font-size:13px;text-align:center;">Σφάλμα: ${es(err.message)}</div>`;
  }
}

async function pollChat() {
  const box = document.getElementById('share-chat-messages');
  if (!box) return;
  try {
    const data = await get(`/share/chat/messages?after=${encodeURIComponent(chatLastTs)}&limit=100`);
    (data.items || []).forEach(appendChatMessage);
    if (data.items?.length) {
      chatLastTs = data.items[data.items.length - 1].created_at;
      scrollChatToBottom(false);
    }
  } catch { /* σιωπηλά — θα ξαναδοκιμάσει σε 3s */ }
}

function appendChatMessage(m) {
  const box = document.getElementById('share-chat-messages');
  if (!box) return;
  const empty = box.querySelector('.text-faint, .text-danger');
  if (empty && box.children.length === 1) box.innerHTML = '';
  const mine = m.user_id === chatMyId;
  const time = m.created_at ? new Date(m.created_at).toLocaleTimeString('el-GR', { hour: '2-digit', minute: '2-digit' }) : '';
  const div = document.createElement('div');
  div.style.cssText = `align-self:${mine ? 'flex-end' : 'flex-start'};max-width:82%;`;
  div.innerHTML = `
    <div style="font-size:11px;color:var(--ink-faint);margin-bottom:2px;${mine ? 'text-align:right;' : ''}">${es(m.user_name || '?')} · ${time}</div>
    <div style="padding:9px 14px;border-radius:16px;font-size:14px;line-height:1.45;word-break:break-word;${mine
      ? 'background:var(--grad-sea);color:#fff;border-bottom-right-radius:4px;'
      : 'background:var(--surface);border:1px solid var(--line);border-bottom-left-radius:4px;'}">${es(m.text)}</div>
  `;
  box.appendChild(div);
}

function scrollChatToBottom(force) {
  const box = document.getElementById('share-chat-messages');
  if (!box) return;
  if (force || chatIsAtBottom) box.scrollTop = box.scrollHeight;
}

async function loadFolder() {
  currentFolderId = currentFolderId || null;
  const [folders, files] = await Promise.all([
    get(`/share/folders${currentFolderId ? '?parent_id=' + currentFolderId : ''}`),
    get(`/share/files${currentFolderId ? '?folder_id=' + currentFolderId : ''}`),
  ]);
  renderBreadcrumb();
  renderList(folders.items || [], files.items || []);
}

async function loadSearch(q) {
  const data = await get(`/share/search?q=${encodeURIComponent(q)}`);
  renderBreadcrumb();
  renderSearchResults(data.items || []);
}

function renderBreadcrumb() {
  const el = document.getElementById('share-breadcrumb');
  let html = '<span style="cursor:pointer;color:var(--c-primary);font-weight:600;" id="bc-root">🏠 Αρχική</span>';
  for (const bc of breadcrumbs) {
    html += ` <span style="color:var(--ink-soft);">›</span> <span style="cursor:pointer;color:var(--c-primary);" data-bc-id="${bc._id}">${es(bc.name)}</span>`;
  }
  el.innerHTML = html;
  document.getElementById('bc-root')?.addEventListener('click', () => { currentFolderId = null; breadcrumbs = []; loadFolder(); });
  el.querySelectorAll('[data-bc-id]').forEach(s => s.addEventListener('click', () => {
    const id = s.dataset.bcId;
    const idx = breadcrumbs.findIndex(b => b._id === id);
    breadcrumbs = breadcrumbs.slice(0, idx + 1);
    currentFolderId = id;
    loadFolder();
  }));
}

function renderList(folders, files) {
  const el = document.getElementById('share-list');
  if (!folders.length && !files.length) {
    el.innerHTML = '<div class="list-empty">Ο κοινός χώρος είναι άδειος. Δημιούργησε φάκελο ή ανέβασε αρχείο.</div>';
    return;
  }
  const isAdmin = ['skouras@infosphereco.com'].includes((localStorage.getItem('rxtrail_user_email') || '').toLowerCase());
  const myId = localStorage.getItem('rxtrail_user_id') || '';

  const folderHtml = folders.map(f => `
    <div class="list-item" data-folder-id="${f._id}" style="cursor:pointer;">
      <div style="flex:1;" class="folder-open">
        <strong>📁 ${es(f.name)}</strong>
      </div>
      <div style="display:flex;gap:4px;">
        <button class="icon-btn rename-folder-btn" data-id="${f._id}" title="Μετονομασία">✏️</button>
        <button class="icon-btn delete-folder-btn" data-id="${f._id}" title="Διαγραφή">🗑️</button>
      </div>
    </div>
  `).join('');

  const fileHtml = files.map(f => {
    const ext = (f.name || '').split('.').pop().toLowerCase();
    const icon = FILE_ICONS[ext] || '📎';
    const canModify = isAdmin || f.uploaded_by === myId;
    const kb = f.size > 1048576 ? (f.size / 1048576).toFixed(1) + ' MB' : (f.size / 1024).toFixed(0) + ' KB';
    return `
    <div class="list-item" data-file-id="${f._id}">
      <div style="flex:1;min-width:0;cursor:pointer;" class="file-preview">
        <strong>${icon} ${es(f.name)}</strong>
        <div class="text-sm text-muted">${kb} · ${f.uploaded_by_name || '?'} · ${f.version_count > 1 ? '🕑 ' + f.version_count + ' εκδ.' : ''} ${f.knowledge_added ? '· 📚' : ''}</div>
      </div>
      <div style="display:flex;gap:4px;flex-shrink:0;">
        <button class="icon-btn preview-file-btn" data-id="${f._id}" title="Προεπισκόπηση">👁️</button>
        ${f.version_count > 1 ? `<button class="icon-btn versions-file-btn" data-id="${f._id}" title="Ιστορικό εκδόσεων">🕑</button>` : ''}
        <button class="icon-btn knowledge-file-btn" data-id="${f._id}" title="➕ Πρόσθεσε στη Γνώση">📚</button>
        ${canModify ? `
        <button class="icon-btn rename-file-btn" data-id="${f._id}" title="Μετονομασία">✏️</button>
        <button class="icon-btn delete-file-btn" data-id="${f._id}" title="Διαγραφή">🗑️</button>` : ''}
      </div>
    </div>`;
  }).join('');

  el.innerHTML = folderHtml + fileHtml;

  // Folder open
  el.querySelectorAll('.folder-open').forEach(div => div.addEventListener('click', () => {
    const fid = div.parentElement.dataset.folderId;
    breadcrumbs.push({ _id: fid, name: div.querySelector('strong').textContent.replace('📁 ', '') });
    currentFolderId = fid;
    loadFolder();
  }));

  // File preview
  el.querySelectorAll('.preview-file-btn').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation(); previewFile(b.dataset.id);
  }));
  el.querySelectorAll('.file-preview').forEach(div => div.addEventListener('click', () => {
    previewFile(div.parentElement.dataset.fileId);
  }));

  // Versions
  el.querySelectorAll('.versions-file-btn').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation(); showVersions(b.dataset.id);
  }));

  // To knowledge
  el.querySelectorAll('.knowledge-file-btn').forEach(b => b.addEventListener('click', (e) => {
    e.stopPropagation(); addToKnowledge(b.dataset.id, b);
  }));

  // Rename/delete folder
  el.querySelectorAll('.rename-folder-btn').forEach(b => b.addEventListener('click', (e) => { e.stopPropagation(); renameFolder(b.dataset.id); }));
  el.querySelectorAll('.delete-folder-btn').forEach(b => b.addEventListener('click', (e) => { e.stopPropagation(); deleteFolder(b.dataset.id); }));

  // Rename/delete file
  el.querySelectorAll('.rename-file-btn').forEach(b => b.addEventListener('click', (e) => { e.stopPropagation(); renameFile(b.dataset.id); }));
  el.querySelectorAll('.delete-file-btn').forEach(b => b.addEventListener('click', (e) => { e.stopPropagation(); deleteFile(b.dataset.id); }));
}

function renderSearchResults(items) {
  const el = document.getElementById('share-list');
  if (!items.length) {
    el.innerHTML = '<div class="list-empty">Δεν βρέθηκαν αποτελέσματα</div>';
    return;
  }
  el.innerHTML = items.map(it => {
    if (it.kind === 'folder') {
      return `<div class="list-item"><div style="flex:1;cursor:pointer;" class="folder-open">
        <strong>📁 ${es(it.name)}</strong></div></div>`;
    }
    const ext = (it.name || '').split('.').pop().toLowerCase();
    const icon = FILE_ICONS[ext] || '📎';
    return `<div class="list-item"><div style="flex:1;min-width:0;">
      <strong>${icon} ${es(it.name)}</strong>
      <div class="text-sm text-muted">${it.uploaded_by_name || ''}</div>
      <div style="display:flex;gap:4px;margin-top:4px;">
        <button class="icon-btn preview-file-btn" data-id="${it._id}" title="Προεπισκόπηση">👁️</button>
        <button class="icon-btn knowledge-file-btn" data-id="${it._id}" title="➕ Γνώση">📚</button>
      </div></div></div>`;
  }).join('');
  el.querySelectorAll('.preview-file-btn').forEach(b => b.addEventListener('click', (e) => { e.stopPropagation(); previewFile(b.dataset.id); }));
  el.querySelectorAll('.knowledge-file-btn').forEach(b => b.addEventListener('click', (e) => { e.stopPropagation(); addToKnowledge(b.dataset.id, b); }));
  el.querySelectorAll('.folder-open').forEach(div => div.addEventListener('click', () => {
    const name = div.querySelector('strong').textContent.replace('📁 ', '');
    const fid = div.parentElement.dataset.folderId;
    if (fid) { breadcrumbs.push({ _id: fid, name }); currentFolderId = fid; loadFolder(); }
  }));
}

// ── Actions ──────────────────────────────────────────────────────────────

async function handleUpload(files) {
  if (!files || !files.length) return;
  const file = files[0];
  if (file.size > 50 * 1024 * 1024) { showToast('❌ Το αρχείο ξεπερνά τα 50MB', 'error'); return; }
  const name = prompt('Όνομα αρχείου (ή Enter για το κανονικό):', file.name) || file.name;
  try {
    const form = new FormData();
    form.append('file', file);
    form.append('name', name);
    if (currentFolderId) form.append('folder_id', currentFolderId);
    const token = localStorage.getItem('rxtrail_token');
    const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
    const base = isLocal ? '' : '/api';
    const res = await fetch(`${base}/share/upload`, {
      method: 'POST', headers: { 'Authorization': `Bearer ${token}` }, body: form,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Σφάλμα ${res.status}`);
    showToast(data.version_count > 1 ? `🕑 Νέα έκδοση του «${name}» (v${data.version_count})` : `✅ «${name}» ανέβηκε`, 'success');
    await loadFolder();
  } catch (err) {
    showToast('❌ ' + err.message, 'error');
  }
  document.getElementById('share-file-input').value = '';
}

function newFolderModal() {
  openModal('📁 Νέος Φάκελος', `
    <form id="new-folder-form">
      <div class="form-group"><label>Όνομα φακέλου</label><input id="nf-name" placeholder="π.χ. Οδηγίες ΕΟΠΥΥ 2026" required></div>
      <button type="submit" class="btn-primary full-width mt-2">Δημιουργία</button>
      <p id="nf-error" class="error-msg hidden mt-2"></p>
    </form>
  `);
  document.getElementById('new-folder-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const errEl = document.getElementById('nf-error');
    errEl.classList.add('hidden');
    try {
      await post('/share/folders', { name: document.getElementById('nf-name').value.trim(), parent_id: currentFolderId });
      closeModal();
      await loadFolder();
    } catch (err) { errEl.textContent = err.message; errEl.classList.remove('hidden'); }
  });
}

async function previewFile(fileId) {
  try {
    const token = localStorage.getItem('rxtrail_token');
    const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
    const base = isLocal ? '' : '/api';
    const res = await fetch(`${base}/share/files/${fileId}/file`, { headers: { 'Authorization': `Bearer ${token}` } });
    if (!res.ok) throw new Error('Σφάλμα φόρτωσης: ' + res.status);
    const blob = await res.blob();
    const objUrl = URL.createObjectURL(blob);
    const mime = blob.type || '';
    const isImage = mime.startsWith('image/');
    const isPdf = mime === 'application/pdf';
    const title = document.getElementById('modal-title');
    const body = document.getElementById('modal-body');
    if (isImage) {
      title.textContent = '🖼️ Προεπισκόπηση';
      body.innerHTML = `<img src="${objUrl}" style="max-width:100%;max-height:70vh;border-radius:8px;">`;
    } else if (isPdf) {
      title.textContent = '📄 Προεπισκόπηση';
      body.innerHTML = `<iframe src="${objUrl}" style="width:100%;height:70vh;border:none;border-radius:8px;"></iframe>`;
    } else {
      title.textContent = '📎 Λήψη';
      body.innerHTML = `<p>Προεπισκόπηση δεν είναι διαθέσιμη για αυτόν τον τύπο.</p><a href="${objUrl}" download class="btn-primary btn-sm">📥 Λήψη</a>`;
    }
    document.getElementById('modal-overlay').classList.remove('hidden');
    document.getElementById('modal-container').classList.remove('hidden');
  } catch (err) { showToast('❌ ' + err.message, 'error'); }
}

async function showVersions(fileId) {
  try {
    const f = (await get(`/share/files?folder_id=__none__`)).items.find(x => x._id === fileId);
    // We need a direct versions fetch — reuse search endpoint fallback
    const token = localStorage.getItem('rxtrail_token');
    const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost';
    const base = isLocal ? '' : '/api';
    const list = await get(`/share/files`);
    const file = (list.items || []).find(x => x._id === fileId) || (await get(`/share/search?q=`)).items.find(x => x._id === fileId);
    if (!file) { showToast('❌ Δεν βρήκα το αρχείο', 'error'); return; }
    const versions = file.versions || [];
    openModal(`🕑 Εκδόσεις: ${file.name}`, `
      <div style="max-height:60vh;overflow-y:auto;">
        ${versions.slice().reverse().map((v, ri) => {
          const idx = versions.length - 1 - ri;
          const kb = v.size > 1048576 ? (v.size / 1048576).toFixed(1) + ' MB' : (v.size / 1024).toFixed(0) + ' KB';
          return `<div class="flex-between" style="padding:10px 0;border-bottom:1px solid var(--c-border-light);">
            <div><strong>Έκδοση ${idx + 1}</strong> · ${v.uploaded_by_name || '?'}<br><span class="text-sm text-muted">${kb} · ${(v.created_at || '').slice(0, 16).replace('T', ' ')}</span></div>
            <button class="btn-text btn-sm dl-version" data-id="${fileId}" data-idx="${idx}">⬇️</button>
          </div>`;
        }).join('')}
      </div>
    `);
    document.querySelectorAll('.dl-version').forEach(b => b.addEventListener('click', async () => {
      const res = await fetch(`${base}/share/files/${b.dataset.id}/versions/${b.dataset.idx}/file?download=1`, { headers: { 'Authorization': `Bearer ${token}` } });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = file.name; a.click();
    }));
  } catch (err) { showToast('❌ ' + err.message, 'error'); }
}

async function addToKnowledge(fileId, btn) {
  if (!confirm('➕ Να προστεθεί αυτό το αρχείο στη Βάση Γνώσης; Θα είναι διαθέσιμο σε ΟΛΟΥΣ τους χρήστες.')) return;
  btn.disabled = true; btn.textContent = '⏳';
  try {
    const res = await post(`/share/files/${fileId}/to-knowledge`);
    showToast('✅ ' + (res.message || 'Προστέθηκε στη Γνώση'), 'success');
    await loadFolder();
  } catch (err) { showToast('❌ ' + err.message, 'error'); }
  if (btn) { btn.disabled = false; btn.textContent = '📚'; }
}

function renameFolder(folderId) {
  const newName = prompt('Νέο όνομα φακέλου:');
  if (!newName || !newName.trim()) return;
  patch(`/share/folders/${folderId}`, { name: newName.trim() })
    .then(() => loadFolder()).catch(err => showToast('❌ ' + err.message, 'error'));
}

async function deleteFolder(folderId) {
  if (!confirm('⚠️ Διαγραφή φακέλου ΚΑΙ ΟΛΩΝ των περιεχομένων του;')) return;
  try { await del(`/share/folders/${folderId}`); await loadFolder(); }
  catch (err) { showToast('❌ ' + err.message, 'error'); }
}

function renameFile(fileId) {
  const newName = prompt('Νέο όνομα αρχείου:');
  if (!newName || !newName.trim()) return;
  patch(`/share/files/${fileId}`, { name: newName.trim() })
    .then(() => loadFolder()).catch(err => showToast('❌ ' + err.message, 'error'));
}

async function deleteFile(fileId) {
  if (!confirm('⚠️ Διαγραφή αρχείου (και όλων των εκδόσεων του);')) return;
  try { await del(`/share/files/${fileId}`); await loadFolder(); }
  catch (err) { showToast('❌ ' + err.message, 'error'); }
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function es(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
