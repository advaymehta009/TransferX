from __future__ import annotations
"""
web_ui.py - Browser HTML/CSS/JS.

Dark glassmorphism card layout, mobile-first, upload + download.
Upload: drag-and-drop zone + file picker. Files sent as multipart/form-data
to POST /<token>/upload. Progress shown in JS; multiple files in parallel.
"""

import html
import os

# ---- CSS --------------------------------------------------------------------

_CSS = """
:root {
  --bg: #0f1117; --surface: rgba(255,255,255,0.05);
  --border: rgba(255,255,255,0.10); --accent: #6ee7f7;
  --accent2: #a78bfa; --green: #4ade80; --yellow: #fbbf24;
  --red: #f87171; --text: #e2e8f0; --muted: #64748b; --radius: 12px;
  --font: 'SF Pro Display','Segoe UI',system-ui,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font);
     min-height:100vh;padding:20px 16px 80px}
.header{display:flex;align-items:center;gap:12px;margin-bottom:20px}
.header-logo{font-size:1.3rem;font-weight:700;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.breadcrumb{font-size:.83rem;color:var(--muted);
  display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.breadcrumb a{color:var(--accent);text-decoration:none}
.breadcrumb a:hover{text-decoration:underline}

/* upload zone */
.upload-zone{
  border:2px dashed var(--border);border-radius:var(--radius);
  padding:28px 20px;text-align:center;margin-bottom:18px;
  transition:border-color .2s,background .2s;cursor:pointer;
  background:var(--surface);
}
.upload-zone.drag-over{border-color:var(--accent);background:rgba(110,231,247,.07)}
.upload-zone-icon{font-size:2rem;margin-bottom:8px}
.upload-zone p{color:var(--muted);font-size:.88rem}
.upload-zone strong{color:var(--text)}
#file-input{display:none}
.upload-list{margin-bottom:14px}
.upload-item{
  display:flex;align-items:center;gap:10px;
  background:var(--surface);border:1px solid var(--border);
  border-radius:8px;padding:10px 14px;margin-bottom:6px;font-size:.83rem;
}
.upload-item-name{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.upload-item-bar{flex:2;height:5px;border-radius:3px;
  background:rgba(255,255,255,.1);overflow:hidden}
.upload-item-fill{height:100%;border-radius:3px;
  background:var(--green);width:0%;transition:width .2s}
.upload-item-status{font-size:.75rem;color:var(--muted);white-space:nowrap;min-width:44px;text-align:right}
.upload-item-speed{font-size:.72rem;color:var(--accent);white-space:nowrap;margin-right:4px}
.upload-item-eta{font-size:.72rem;color:var(--muted);white-space:nowrap;margin-right:4px}

/* toolbar */
.toolbar{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:12px 16px;margin-bottom:16px;
  display:flex;flex-wrap:wrap;gap:8px;align-items:center;backdrop-filter:blur(12px)}
.select-all-label{display:flex;align-items:center;gap:8px;
  color:var(--text);font-size:.88rem;cursor:pointer;user-select:none}
.select-all-label input{width:16px;height:16px;accent-color:var(--accent);cursor:pointer}
.toolbar-sep{width:1px;height:22px;background:var(--border);margin:0 2px}
.btn{display:inline-flex;align-items:center;gap:5px;
  padding:7px 14px;border-radius:8px;border:none;font-size:.8rem;
  font-weight:600;cursor:pointer;transition:opacity .15s,transform .1s;
  text-decoration:none;white-space:nowrap}
.btn:hover{opacity:.85}.btn:active{transform:scale(.97)}
.btn-primary{background:var(--accent);color:#0f1117}
.btn-secondary{background:rgba(255,255,255,.10);color:var(--text);border:1px solid var(--border)}
.btn-green{background:var(--green);color:#0f1117}
.btn-red{background:var(--red);color:#0f1117}
.btn-sm{padding:4px 9px;font-size:.74rem;border-radius:6px}

/* file grid */
.file-grid{display:flex;flex-direction:column;gap:5px}
.file-row{display:flex;align-items:center;gap:10px;
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:10px 14px;
  transition:background .15s,border-color .15s;cursor:pointer}
.file-row:hover{background:rgba(255,255,255,.09);border-color:rgba(255,255,255,.18)}
.file-row.selected{border-color:var(--accent);background:rgba(110,231,247,.07)}
.file-row input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent);flex-shrink:0;cursor:pointer}
.file-icon{font-size:1.2rem;flex-shrink:0}
.file-info{flex:1;min-width:0}
.file-name{font-size:.92rem;font-weight:500;word-break:break-all}
.file-name a{color:inherit;text-decoration:none}
.file-name a:hover{color:var(--accent)}
.file-meta{font-size:.76rem;color:var(--muted);margin-top:2px}
.file-actions{display:flex;gap:5px;flex-shrink:0}
.parent-row{display:flex;align-items:center;gap:10px;padding:8px 14px;
  border-radius:var(--radius);color:var(--muted);font-size:.85rem;
  cursor:pointer;border:1px solid transparent;transition:background .15s}
.parent-row:hover{background:var(--surface)}
.parent-row a{color:var(--accent);text-decoration:none;font-weight:500}
.empty{text-align:center;padding:50px 20px;color:var(--muted)}
.empty-icon{font-size:2.5rem;margin-bottom:10px}
#toast{position:fixed;bottom:20px;right:20px;
  background:#1e293b;border:1px solid var(--border);border-radius:10px;
  padding:10px 18px;font-size:.83rem;color:var(--text);
  opacity:0;transition:opacity .3s;pointer-events:none;z-index:999}
#toast.show{opacity:1}
@media(max-width:600px){.file-actions .btn-sm{padding:5px 8px}}
"""

# ---- JS ---------------------------------------------------------------------

_JS = """
// ---- checkbox helpers ------------------------------------------------------
function toggleAll(master) {
  document.querySelectorAll('.item-cb').forEach(cb => {
    cb.checked = master.checked;
    cb.closest('.file-row').classList.toggle('selected', cb.checked);
  });
}
document.querySelectorAll('.item-cb').forEach(cb => {
  cb.addEventListener('change', () => {
    cb.closest('.file-row').classList.toggle('selected', cb.checked);
    const all = document.querySelectorAll('.item-cb');
    const chk = document.querySelectorAll('.item-cb:checked');
    const master = document.getElementById('select-all-cb');
    master.indeterminate = chk.length > 0 && chk.length < all.length;
    master.checked = chk.length === all.length;
  });
});
document.querySelectorAll('.file-row').forEach(row => {
  row.addEventListener('click', e => {
    if (e.target.tagName === 'A' || e.target.tagName === 'BUTTON' ||
        e.target.closest('.file-actions') || e.target.tagName === 'INPUT') return;
    const cb = row.querySelector('.item-cb');
    if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }
  });
});

// ---- raw parallel download -------------------------------------------------
function downloadRawSelected() {
  const chk = document.querySelectorAll('.item-cb:checked');
  if (!chk.length) { showToast('Nothing selected'); return; }
  chk.forEach(cb => {
    const url = cb.dataset.url; if (!url) return;
    const a = document.createElement('a');
    a.href = url; a.download = '';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  });
  showToast(`Starting ${chk.length} download(s)...`);
}

// ---- upload engine --------------------------------------------------------
//
// Design:
//   • File split into CHUNK_SIZE blobs via File.slice() (zero-copy in browser)
//   • Up to MAX_PARALLEL chunks in-flight simultaneously per file
//   • Each chunk is a raw binary POST (no multipart) - maximum throughput
//   • Server ACKs each chunk immediately so WAN latency doesn't block progress
//   • EWMA speed (alpha=0.2) gives accurate real-time MB/s
//   • Retry: failed chunks re-queued up to MAX_RETRIES times
//   • Pause: stops dispatching new chunks; in-flight ones complete normally
//   • Resume: refills the worker pool

const CHUNK_SIZE   = 4 * 1024 * 1024;  // 4 MB - safe under Cloudflare 100MB limit
const MAX_PARALLEL = Math.min(navigator.hardwareConcurrency || 4, 6);
const MAX_RETRIES  = 3;
const EWMA_ALPHA   = 0.2;              // smoothing factor for speed display

const zone       = document.getElementById('upload-zone');
const fileInput  = document.getElementById('file-input');
const uploadList = document.getElementById('upload-list');
const UPLOAD_URL = document.getElementById('upload-url').value;

zone.addEventListener('click', () => fileInput.click());
zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('drag-over'); });
zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
zone.addEventListener('drop', e => {
  e.preventDefault(); zone.classList.remove('drag-over');
  [...e.dataTransfer.files].forEach(startUpload);
});
fileInput.addEventListener('change', () => {
  [...fileInput.files].forEach(startUpload);
  fileInput.value = '';
});

function genId() {
  try { return crypto.randomUUID(); } catch(_) {
    return Date.now().toString(36) + Math.random().toString(36).slice(2);
  }
}

function fmtSize(b) {
  if (b < 1024) return b + ' B';
  if (b < 1024**2) return (b/1024).toFixed(1) + ' KB';
  if (b < 1024**3) return (b/1024**2).toFixed(1) + ' MB';
  return (b/1024**3).toFixed(2) + ' GB';
}

function fmtETA(secs) {
  if (!isFinite(secs) || secs <= 0) return '--';
  if (secs < 60) return Math.ceil(secs) + 's';
  if (secs < 3600) return Math.floor(secs/60) + 'm ' + (Math.ceil(secs)%60) + 's';
  return Math.floor(secs/3600) + 'h ' + (Math.floor(secs/60)%60) + 'm';
}

function startUpload(file) {
  const fileId     = genId();
  const totalParts = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
  const encName    = encodeURIComponent(file.name);

  // State for this upload
  const state = {
    paused:        false,
    cancelled:     false,
    partsOK:       0,            // successfully acknowledged parts
    bytesACKed:    0,            // bytes whose chunk was ACKed by server
    ewmaSpeed:     0,            // MB/s EWMA
    lastSpeedTime: Date.now(),
    lastSpeedBytes:0,
    startTime:     Date.now(),
    retries:       {},           // part -> retry count
    queue:         Array.from({length: totalParts}, (_, i) => i),
    activeXHRs:    new Set(),
  };

  // Build UI row
  const safeId = fileId.replace(/-/g,'');
  const row = document.createElement('div');
  row.className = 'upload-item';
  row.id = 'row-' + safeId;
  row.innerHTML = `
    <span class="upload-item-name" title="${escHtml(file.name)}">${escHtml(file.name)}</span>
    <div class="upload-item-bar"><div class="upload-item-fill" id="fill-${safeId}"></div></div>
    <span class="upload-item-speed" id="spd-${safeId}"></span>
    <span class="upload-item-eta"   id="eta-${safeId}" style="font-size:.72rem;color:var(--muted);margin-right:4px"></span>
    <span class="upload-item-status" id="st-${safeId}">0%</span>
    <button class="btn btn-secondary btn-sm" id="pause-${safeId}" style="margin-left:6px;padding:3px 8px">⏸</button>
  `;
  uploadList.prepend(row);

  const btnPause = document.getElementById('pause-' + safeId);
  btnPause.addEventListener('click', () => {
    state.paused = !state.paused;
    btnPause.textContent = state.paused ? '▶' : '⏸';
    btnPause.title = state.paused ? 'Resume' : 'Pause';
    if (!state.paused) fillWorkers(); // resume: start queued chunks
  });

  function updateUI() {
    const pct  = Math.round(state.partsOK / totalParts * 100);
    const now  = Date.now();
    const dtMs = now - state.lastSpeedTime;

    // Update EWMA speed every ~500ms with new bytes ACKed
    if (dtMs >= 500) {
      const newBytes = state.bytesACKed - state.lastSpeedBytes;
      const instMbps = (newBytes / (1024*1024)) / (dtMs / 1000);
      state.ewmaSpeed = state.ewmaSpeed === 0
        ? instMbps
        : EWMA_ALPHA * instMbps + (1 - EWMA_ALPHA) * state.ewmaSpeed;
      state.lastSpeedTime  = now;
      state.lastSpeedBytes = state.bytesACKed;
    }

    const spd = state.ewmaSpeed;
    const remaining = file.size - state.bytesACKed;
    const eta = spd > 0 ? remaining / (spd * 1024 * 1024) : Infinity;

    const fill = document.getElementById('fill-' + safeId);
    const st   = document.getElementById('st-'   + safeId);
    const spdEl= document.getElementById('spd-'  + safeId);
    const etaEl= document.getElementById('eta-'  + safeId);
    if (fill)  fill.style.width = pct + '%';
    if (st)    st.textContent   = pct + '%';
    if (spdEl) spdEl.textContent = spd > 0 ? spd.toFixed(1) + ' MB/s' : '';
    if (etaEl) etaEl.textContent = pct < 100 ? fmtETA(eta) : '';
  }

  function onPartDone(part, chunkSize) {
    state.partsOK++;
    state.bytesACKed += chunkSize;
    updateUI();

    if (state.partsOK === totalParts) {
      // All done
      const elapsed = (Date.now() - state.startTime) / 1000;
      const avgMbps = (file.size / (1024*1024)) / elapsed;
      const fill = document.getElementById('fill-' + safeId);
      const st   = document.getElementById('st-'   + safeId);
      const spd  = document.getElementById('spd-'  + safeId);
      const eta  = document.getElementById('eta-'  + safeId);
      const btn  = document.getElementById('pause-'+ safeId);
      if (fill) fill.style.background = 'var(--green)';
      if (st)   { st.textContent = 'Done ✓'; st.style.color = 'var(--green)'; }
      if (spd)  spd.textContent = avgMbps.toFixed(1) + ' MB/s avg';
      if (eta)  eta.textContent = '';
      if (btn)  btn.remove();
      showToast(`${file.name} — ${fmtSize(file.size)} at ${avgMbps.toFixed(1)} MB/s`);
      setTimeout(() => location.reload(), 1600);
    } else {
      fillWorkers();  // start next queued chunk
    }
  }

  function onPartFail(part, chunkSize, reason) {
    state.retries[part] = (state.retries[part] || 0) + 1;
    if (state.retries[part] <= MAX_RETRIES) {
      // Re-queue for retry
      state.queue.unshift(part);
      fillWorkers();
    } else {
      state.cancelled = true;
      const st  = document.getElementById('st-'  + safeId);
      const btn = document.getElementById('pause-'+ safeId);
      if (st)  { st.textContent = 'Failed'; st.style.color = 'var(--red)'; }
      if (btn) btn.remove();
      showToast(`Upload failed: ${file.name} (part ${part}, ${MAX_RETRIES} retries)`, true);
    }
  }

  function uploadPart(part) {
    if (state.cancelled) return;
    const start     = part * CHUNK_SIZE;
    const blob      = file.slice(start, Math.min(start + CHUNK_SIZE, file.size));
    const chunkSize = blob.size;

    const xhr = new XMLHttpRequest();
    state.activeXHRs.add(xhr);
    xhr.open('POST', UPLOAD_URL);
    xhr.setRequestHeader('X-File-Id',     fileId);
    xhr.setRequestHeader('X-Filename',    encName);
    xhr.setRequestHeader('X-Part',        part);
    xhr.setRequestHeader('X-Total-Parts', totalParts);
    xhr.setRequestHeader('X-File-Size',   file.size);
    xhr.setRequestHeader('Content-Type',  'application/octet-stream');
    xhr.timeout = 120000;  // 2 min per chunk

    xhr.onload = () => {
      state.activeXHRs.delete(xhr);
      if (xhr.status === 200) {
        onPartDone(part, chunkSize);
      } else {
        onPartFail(part, chunkSize, 'HTTP ' + xhr.status);
      }
    };
    xhr.onerror   = () => { state.activeXHRs.delete(xhr); onPartFail(part, chunkSize, 'network'); };
    xhr.ontimeout = () => { state.activeXHRs.delete(xhr); onPartFail(part, chunkSize, 'timeout'); };

    xhr.send(blob);
  }

  function fillWorkers() {
    if (state.paused || state.cancelled) return;
    while (state.activeXHRs.size < MAX_PARALLEL && state.queue.length > 0) {
      const part = state.queue.shift();
      uploadPart(part);
    }
  }

  // Kick off initial parallel workers
  fillWorkers();
}

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ---- toast -----------------------------------------------------------------
function showToast(msg, isErr) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.borderColor = isErr ? 'var(--red)' : 'var(--border)';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}
"""


# ---- helpers ----------------------------------------------------------------

def _icon(is_dir: bool, name: str) -> str:
    if is_dir: return '📁'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return {
        'mp4':'🎬','mkv':'🎬','avi':'🎬','mov':'🎬','webm':'🎬',
        'mp3':'🎵','flac':'🎵','aac':'🎵','wav':'🎵',
        'jpg':'🖼️','jpeg':'🖼️','png':'🖼️','gif':'🖼️','webp':'🖼️','heic':'🖼️',
        'pdf':'📄','doc':'📝','docx':'📝','txt':'📝','md':'📝',
        'zip':'🗜️','tar':'🗜️','gz':'🗜️','rar':'🗜️','7z':'🗜️',
        'py':'🐍','js':'📜','ts':'📜','html':'🌐','css':'🎨',
        'dmg':'💿','iso':'💿','xlsx':'📊','csv':'📊',
    }.get(ext, '📃')


def _fmt_size(b: int) -> str:
    for u in ('B','KB','MB','GB','TB'):
        if b < 1024: return f'{b:.1f} {u}'
        b /= 1024
    return f'{b:.1f} PB'


# ---- page renderer ----------------------------------------------------------

def render_directory(url_path: str, fs_path: str, entries: list[str],
                     skip_names: set[str], token: str = '') -> bytes:
    root_url    = f'/{token}/'
    upload_url  = f'/{token}/upload-chunk'
    batch_url   = f'/{token}/batch'

    # breadcrumb
    parts = [p for p in url_path.split('/') if p]
    crumb = f'<a href="{html.escape(root_url)}">root</a>'
    for i, p in enumerate(parts):
        href  = root_url + '/'.join(parts[:i+1]) + '/'
        crumb += f' / <a href="{html.escape(href)}">{html.escape(p)}</a>'

    rows_html = ''
    if url_path not in ('/', ''):
        rows_html += ('<div class="parent-row"><span>⬆️</span>'
                      '<a href="..">Parent directory</a></div>')

    cur = url_path if url_path.endswith('/') else url_path + '/'
    dirs_html = files_html = ''
    has_any = False

    for name in entries:
        if name.startswith('.') or name in skip_names:
            continue
        fullname  = os.path.join(fs_path, name)
        safe_name = html.escape(name)
        is_dir    = os.path.isdir(fullname)
        icon      = _icon(is_dir, name)
        has_any   = True

        inner_item = f'{cur}{name}'
        tok_item   = f'/{token}{inner_item}'
        tok_zip    = f'/{token}/zip{inner_item}'
        file_url   = f'{tok_item}{"/" if is_dir else ""}'

        if is_dir:
            actions = (f'<a class="btn btn-secondary btn-sm" '
                       f'href="{html.escape(tok_zip)}">⬇ ZIP</a>')
            dirs_html += (
                f'<div class="file-row">'
                f'<input type="checkbox" class="item-cb" name="items"'
                f' value="{html.escape(tok_item)}" data-url="{html.escape(tok_zip)}">'
                f'<span class="file-icon">{icon}</span>'
                f'<div class="file-info">'
                f'<div class="file-name"><a href="{html.escape(file_url)}">{safe_name}/</a></div>'
                f'<div class="file-meta">Folder</div></div>'
                f'<div class="file-actions">{actions}</div></div>'
            )
        else:
            try:    meta = _fmt_size(os.path.getsize(fullname))
            except: meta = '—'
            files_html += (
                f'<div class="file-row">'
                f'<input type="checkbox" class="item-cb" name="items"'
                f' value="{html.escape(tok_item)}" data-url="{html.escape(tok_item)}">'
                f'<span class="file-icon">{icon}</span>'
                f'<div class="file-info">'
                f'<div class="file-name"><a href="{html.escape(file_url)}">{safe_name}</a></div>'
                f'<div class="file-meta">{meta}</div></div>'
                f'<div class="file-actions"></div></div>'
            )

    rows_html += dirs_html + files_html
    if not has_any:
        rows_html += ('<div class="empty"><div class="empty-icon">📭</div>'
                      '<div>This folder is empty</div></div>')

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>TransferX — {html.escape(url_path)}</title>
<style>{_CSS}</style>
</head><body>
<input type="hidden" id="upload-url" value="{html.escape(upload_url)}">
<div class="header">
  <div class="header-logo">TransferX</div>
  <div class="breadcrumb">{crumb}</div>
</div>

<div class="upload-zone" id="upload-zone">
  <div class="upload-zone-icon">📤</div>
  <p><strong>Drop files here</strong> or <strong>tap to choose</strong></p>
  <p style="margin-top:6px">Files saved to <code>_uploads/</code> on the host</p>
</div>
<input type="file" id="file-input" multiple>
<div class="upload-list" id="upload-list"></div>

<form action="{html.escape(batch_url)}" method="POST" id="batchForm">
<div class="toolbar">
  <label class="select-all-label">
    <input type="checkbox" id="select-all-cb" onclick="toggleAll(this)">
    <span>Select all</span>
  </label>
  <div class="toolbar-sep"></div>
  <button class="btn btn-primary" type="submit" name="action" value="zip">⬇ ZIP</button>
  <button class="btn btn-secondary" type="submit" name="action" value="tar">⬇ TAR</button>
  <button class="btn btn-green" type="button" onclick="downloadRawSelected()">⚡ Raw</button>
</div>
<div class="file-grid">{rows_html}</div>
</form>

<div id="toast"></div>
<script>{_JS}</script>
</body></html>""".encode('utf-8')
