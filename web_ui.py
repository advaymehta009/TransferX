"""
web_ui.py — Beautiful, responsive HTML/CSS/JS for the browser file browser.

Rendered as a pure Python string template — no Jinja dependency.
Design: dark glassmorphism card layout, mobile-first, touch-friendly.
"""

import html
import os


_CSS = """
:root {
  --bg: #0f1117;
  --surface: rgba(255,255,255,0.05);
  --border: rgba(255,255,255,0.10);
  --accent: #6ee7f7;
  --accent2: #a78bfa;
  --green: #4ade80;
  --yellow: #fbbf24;
  --text: #e2e8f0;
  --muted: #64748b;
  --radius: 12px;
  --font: 'SF Pro Display', 'Segoe UI', system-ui, sans-serif;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  min-height: 100vh;
  padding: 24px 16px 80px;
}
/* header */
.header {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 24px;
}
.header-logo {
  font-size: 1.4rem; font-weight: 700; letter-spacing: -0.5px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.breadcrumb {
  font-size: 0.85rem; color: var(--muted);
  display: flex; align-items: center; gap: 4px; flex-wrap: wrap;
}
.breadcrumb a { color: var(--accent); text-decoration: none; }
.breadcrumb a:hover { text-decoration: underline; }
.breadcrumb span { color: var(--muted); }

/* action toolbar */
.toolbar {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 18px;
  margin-bottom: 20px;
  display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  backdrop-filter: blur(12px);
}
.select-all-label {
  display: flex; align-items: center; gap: 8px;
  color: var(--text); font-size: 0.9rem; cursor: pointer;
  user-select: none;
}
.select-all-label input[type=checkbox] {
  width: 16px; height: 16px; accent-color: var(--accent); cursor: pointer;
}
.toolbar-sep { width: 1px; height: 24px; background: var(--border); margin: 0 4px; }
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 16px; border-radius: 8px; border: none;
  font-size: 0.82rem; font-weight: 600; cursor: pointer;
  transition: opacity .15s, transform .1s;
  text-decoration: none; white-space: nowrap;
}
.btn:hover  { opacity: .85; }
.btn:active { transform: scale(.97); }
.btn-primary   { background: var(--accent);  color: #0f1117; }
.btn-secondary { background: rgba(255,255,255,0.10); color: var(--text); border: 1px solid var(--border); }
.btn-green     { background: var(--green);   color: #0f1117; }
.btn-sm { padding: 4px 10px; font-size: 0.76rem; border-radius: 6px; }

/* file grid */
.file-grid {
  display: flex; flex-direction: column; gap: 6px;
}
.file-row {
  display: flex; align-items: center; gap: 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 16px;
  transition: background .15s, border-color .15s;
  cursor: pointer;
}
.file-row:hover { background: rgba(255,255,255,0.09); border-color: rgba(255,255,255,0.18); }
.file-row.selected { border-color: var(--accent); background: rgba(110,231,247,0.07); }
.file-row input[type=checkbox] {
  width: 16px; height: 16px; accent-color: var(--accent);
  flex-shrink: 0; cursor: pointer;
}
.file-icon { font-size: 1.3rem; flex-shrink: 0; }
.file-info { flex: 1; min-width: 0; }
.file-name {
  font-size: 0.95rem; font-weight: 500;
  color: var(--text);
  word-break: break-all;
}
.file-name a { color: inherit; text-decoration: none; }
.file-name a:hover { color: var(--accent); }
.file-meta { font-size: 0.78rem; color: var(--muted); margin-top: 2px; }
.file-actions { display: flex; gap: 6px; flex-shrink: 0; }

/* parent dir row */
.parent-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px;
  border-radius: var(--radius);
  color: var(--muted); font-size: 0.88rem;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background .15s;
}
.parent-row:hover { background: var(--surface); }
.parent-row a { color: var(--accent); text-decoration: none; font-weight: 500; }

/* empty state */
.empty { text-align: center; padding: 60px 20px; color: var(--muted); }
.empty-icon { font-size: 3rem; margin-bottom: 12px; }

/* toast */
#toast {
  position: fixed; bottom: 24px; right: 24px;
  background: #1e293b; border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 20px;
  font-size: 0.85rem; color: var(--text);
  opacity: 0; transition: opacity .3s;
  pointer-events: none; z-index: 999;
}
#toast.show { opacity: 1; }

@media (max-width: 600px) {
  .file-actions .btn-sm { padding: 6px 10px; }
  .toolbar { gap: 8px; }
}
"""

_JS = """
// Checkbox visual sync
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
    const checked = document.querySelectorAll('.item-cb:checked');
    document.getElementById('select-all-cb').indeterminate =
      checked.length > 0 && checked.length < all.length;
    document.getElementById('select-all-cb').checked = checked.length === all.length;
  });
});
// Click row to toggle checkbox
document.querySelectorAll('.file-row').forEach(row => {
  row.addEventListener('click', e => {
    if (e.target.tagName === 'A' || e.target.tagName === 'BUTTON' ||
        e.target.closest('.file-actions') || e.target.tagName === 'INPUT') return;
    const cb = row.querySelector('.item-cb');
    if (cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')); }
  });
});
// Raw parallel download
function downloadRawSelected() {
  const checked = document.querySelectorAll('.item-cb:checked');
  if (!checked.length) { showToast('Nothing selected'); return; }
  checked.forEach(cb => {
    const url = cb.dataset.url;
    if (!url) return;
    const a = document.createElement('a');
    a.href = url; a.download = '';
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
  });
  showToast(`Starting ${checked.length} download(s)…`);
}
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}
"""


def _icon(is_dir: bool, name: str) -> str:
    if is_dir:
        return '📁'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return {
        'mp4': '🎬', 'mkv': '🎬', 'avi': '🎬', 'mov': '🎬', 'webm': '🎬',
        'mp3': '🎵', 'flac': '🎵', 'aac': '🎵', 'wav': '🎵',
        'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️', 'webp': '🖼️', 'heic': '🖼️',
        'pdf': '📄', 'doc': '📝', 'docx': '📝', 'txt': '📝', 'md': '📝',
        'zip': '🗜️', 'tar': '🗜️', 'gz': '🗜️', 'rar': '🗜️', '7z': '🗜️',
        'py': '🐍', 'js': '📜', 'ts': '📜', 'html': '🌐', 'css': '🎨',
        'dmg': '💿', 'iso': '💿', 'app': '📦', 'exe': '📦',
        'xlsx': '📊', 'csv': '📊',
    }.get(ext, '📃')


def _fmt_size(b: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'


def render_directory(url_path: str, fs_path: str, entries: list[str],
                     skip_names: set[str], token: str = '') -> bytes:
    """
    Build the directory listing page.

    url_path   – path relative to share root, e.g. '/' or '/Movies/'
    token      – session token; all generated URLs include /<token>/...
    entries    – sorted os.listdir() result
    skip_names – filenames to hide
    """
    # All absolute URLs are rooted at /<token>/
    root_url = f'/{token}/'

    # Breadcrumb — links built with token prefix, names without token
    parts      = [p for p in url_path.split('/') if p]
    crumb_html = f'<a href="{html.escape(root_url)}">⌂ root</a>'
    for i, p in enumerate(parts):
        href = root_url + '/'.join(parts[:i+1]) + '/'
        crumb_html += (
            f' <span>/</span>'
            f' <a href="{html.escape(href)}">{html.escape(p)}</a>'
        )

    rows_html = ''

    # Parent link (relative — always correct regardless of token)
    if url_path not in ('/', ''):
        rows_html += (
            '<div class="parent-row">'
            '<span>⬆️</span>'
            '<a href="..">Parent directory</a>'
            '</div>'
        )

    files_html = dirs_html = ''
    has_any = False

    # Inner path (without token) for constructing sub-URLs
    cur_inner = url_path if url_path.endswith('/') else url_path + '/'

    for name in entries:
        if name.startswith('.') or name in skip_names:
            continue

        fullname  = os.path.join(fs_path, name)
        safe_name = html.escape(name)
        is_dir    = os.path.isdir(fullname)
        icon      = _icon(is_dir, name)
        has_any   = True

        # Paths that the server understands (token-prefixed)
        inner_item = f'{cur_inner}{name}'            # e.g. /Movies/Inception
        tok_item   = f'/{token}{inner_item}'         # e.g. /abc123/Movies/Inception
        tok_zip    = f'/{token}/zip{inner_item}'     # e.g. /abc123/zip/Movies/Inception
        file_url   = f'{tok_item}{"/" if is_dir else ""}'

        # Checkbox value = token-prefixed path so server's translate_path works
        cb_value  = tok_item
        data_url  = tok_zip if is_dir else tok_item

        if is_dir:
            actions = (
                f'<a class="btn btn-secondary btn-sm" href="{html.escape(tok_zip)}" '
                f'title="Download folder as ZIP">⬇ ZIP</a>'
            )
            row = (
                f'<div class="file-row">'
                f'<input type="checkbox" class="item-cb" name="items"'
                f' value="{html.escape(cb_value)}" data-url="{html.escape(data_url)}">'
                f'<span class="file-icon">{icon}</span>'
                f'<div class="file-info">'
                f'<div class="file-name">'
                f'<a href="{html.escape(file_url)}">{safe_name}/</a>'
                f'</div>'
                f'<div class="file-meta">Folder</div>'
                f'</div>'
                f'<div class="file-actions">{actions}</div>'
                f'</div>'
            )
            dirs_html += row
        else:
            try:
                size = os.path.getsize(fullname)
                meta = _fmt_size(size)
            except OSError:
                meta = '—'
            row = (
                f'<div class="file-row">'
                f'<input type="checkbox" class="item-cb" name="items"'
                f' value="{html.escape(cb_value)}" data-url="{html.escape(data_url)}">'
                f'<span class="file-icon">{icon}</span>'
                f'<div class="file-info">'
                f'<div class="file-name">'
                f'<a href="{html.escape(file_url)}">{safe_name}</a>'
                f'</div>'
                f'<div class="file-meta">{meta}</div>'
                f'</div>'
                f'<div class="file-actions"></div>'
                f'</div>'
            )
            files_html += row

    rows_html += dirs_html + files_html

    if not has_any:
        rows_html += (
            '<div class="empty">'
            '<div class="empty-icon">📭</div>'
            '<div>This folder is empty</div>'
            '</div>'
        )

    # Batch form POSTs to /<token>/batch
    batch_url = f'/{token}/batch'

    page = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>TransferX — {html.escape(url_path)}</title>
<style>{_CSS}</style>
</head><body>
<div class="header">
  <div class="header-logo">⚡ TransferX</div>
  <div class="breadcrumb">{crumb_html}</div>
</div>
<form action="{html.escape(batch_url)}" method="POST" id="batchForm">
<div class="toolbar">
  <label class="select-all-label">
    <input type="checkbox" id="select-all-cb" onclick="toggleAll(this)">
    <span>Select all</span>
  </label>
  <div class="toolbar-sep"></div>
  <button class="btn btn-primary" type="submit" name="action" value="zip">⬇ Download .zip</button>
  <button class="btn btn-secondary" type="submit" name="action" value="tar">⬇ Download .tar</button>
  <button class="btn btn-green" type="button" onclick="downloadRawSelected()">⚡ Raw parallel</button>
</div>
<div class="file-grid">
{rows_html}
</div>
</form>
<div id="toast"></div>
<script>{_JS}</script>
</body></html>"""

    return page.encode('utf-8')
