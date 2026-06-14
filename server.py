from __future__ import annotations
"""
server.py - HTTP handler with token auth, real-IP extraction, and security hardening.

Security layers:
  1. Session token in URL path - rotates every run, 403 for any mismatch
  2. Root / returns 200+meta-refresh (not 303) so Cloudflare health checks pass
     without following a redirect to a .local hostname CF can't resolve
  3. Real client IP from CF-Connecting-IP / X-Forwarded-For headers when
     traffic arrives via the Cloudflare tunnel (which proxies as 127.0.0.1)
  4. WAN activity recorded per-request so idle-timeout watcher fires correctly
  5. Path traversal blocked in translate_path (strips '..' components)

Performance:
  - sendfile(2): kernel zero-copy, disk->NIC, ~line-rate for big files
  - 8 MB socket send buffer, TCP_NODELAY
  - ThreadingMixIn: one OS thread per connection, GIL released during I/O
  - ZIP_STORED: archiving is I/O-bound not CPU-bound, doesn't block transfers
  - HTTP Range: download managers resume 200 GB transfers automatically
"""

import http.server
import io
import os
import re
import socket
import socketserver
import tarfile
import time
import zipfile
from datetime import datetime
from urllib.parse import parse_qs, unquote

import logger
import tunnel
import web_ui
from dashboard import TerminalDashboard
from tracker import BUFFER_SIZE, TransferTracker

DEFAULT_PORT = 7474

_HIDDEN = {
    'main.py', 'server.py', 'tracker.py', 'dashboard.py',
    'web_ui.py', 'logger.py', 'tunnel.py', 'pyproject.toml',
    'turbo_transfer.log',
}

# Set by main.py before the server starts
SESSION_TOKEN:  str  = ''
WAN_MODE_ACTIVE: bool = False   # True when a Cloudflare tunnel is running


# ---- Helpers ----------------------------------------------------------------

def _stream_zip(wfile, items: list[tuple[str, str]],
                client_ip: str, label: str) -> int:
    total = 0
    with zipfile.ZipFile(wfile, mode='w', compression=zipfile.ZIP_STORED,
                         allowZip64=True) as zf:
        for fs_path, arcname in items:
            zinfo = zipfile.ZipInfo.from_file(fs_path, arcname)
            zinfo.compress_type = zipfile.ZIP_STORED
            with open(fs_path, 'rb') as src, zf.open(zinfo, 'w') as dst:
                while True:
                    t0  = time.monotonic()
                    buf = src.read(BUFFER_SIZE)
                    if not buf:
                        break
                    dst.write(buf)
                    dt     = max(time.monotonic() - t0, 1e-9)
                    total += len(buf)
                    TransferTracker.update(client_ip, label, len(buf), 0, dt)
    return total


def _collect_dir(local_path: str) -> list[tuple[str, str]]:
    items = []
    base  = os.path.dirname(local_path)
    for root, _, files in os.walk(local_path):
        for fname in files:
            fp  = os.path.join(root, fname)
            arc = os.path.relpath(fp, base)
            items.append((fp, arc))
    return items


# ---- Handler ----------------------------------------------------------------

class TurboHandler(http.server.SimpleHTTPRequestHandler):
    share_root: str = '.'

    def log_message(self, format, *args):
        pass  # all logging goes through TerminalDashboard

    # ---- client identity ----------------------------------------------------

    def _real_ip(self) -> str:
        """
        Return the actual client IP.
        When traffic arrives via Cloudflare tunnel the raw socket shows 127.0.0.1;
        the real IP is in CF-Connecting-IP (preferred) or X-Forwarded-For.
        """
        raw = self.client_address[0]
        if raw in ('127.0.0.1', '::1'):
            # Likely proxied through cloudflared
            cf_ip = self.headers.get('CF-Connecting-IP', '').strip()
            if cf_ip:
                return cf_ip
            fwd = self.headers.get('X-Forwarded-For', '').strip()
            if fwd:
                return fwd.split(',')[0].strip()
        return raw

    def _is_wan(self) -> bool:
        """True when this request arrived through the Cloudflare tunnel."""
        raw = self.client_address[0]
        return WAN_MODE_ACTIVE and raw in ('127.0.0.1', '::1')

    def _touch_wan(self):
        """Update WAN idle clock if this is a WAN request."""
        if self._is_wan():
            tunnel.record_wan_activity()

    def _device(self) -> str:
        try:
            return socket.gethostbyaddr(self._real_ip())[0]
        except Exception:
            ua = self.headers.get('User-Agent', '')
            if 'Android' in ua: return 'Android'
            if 'iPhone'  in ua: return 'iPhone'
            if 'iPad'    in ua: return 'iPad'
            return 'Device'

    # ---- socket / helpers ---------------------------------------------------

    def _tune(self):
        try:
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUFFER_SIZE)
            self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass

    def _redirect(self, url: str):
        self.send_response(303)
        self.send_header('Location', url)
        self.end_headers()

    def _forbidden(self):
        body = b'<html><body><h2>403</h2></body></html>'
        self.send_response(403)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- token --------------------------------------------------------------

    def _strip_token(self, path: str) -> str | None:
        """Return inner path (token stripped) or None if token invalid."""
        tok    = SESSION_TOKEN
        prefix = f'/{tok}'
        if path == prefix or path.startswith(prefix + '/'):
            return path[len(prefix):] or '/'
        return None

    def translate_path(self, path: str) -> str:
        path = path.split('?', 1)[0].split('#', 1)[0]
        path = unquote(path)
        tok    = SESSION_TOKEN
        prefix = f'/{tok}'
        if path == prefix or path.startswith(prefix + '/'):
            path = path[len(prefix):] or '/'
        # Strip '..' to block path traversal
        parts = [p for p in path.replace('\\', '/').split('/')
                 if p and p != '..']
        return os.path.join(TurboHandler.share_root, *parts)

    # ---- POST ---------------------------------------------------------------

    def do_POST(self):
        tok     = SESSION_TOKEN
        referer = self.headers.get('Referer', '')
        if not (self.path == f'/{tok}/batch' or
                (self.path == '/batch' and f'/{tok}/' in referer)):
            self._forbidden()
            return

        self._touch_wan()

        cl     = int(self.headers.get('Content-Length', 0))
        body   = self.rfile.read(cl).decode('utf-8')
        params = parse_qs(body)
        items  = params.get('items', [])
        action = params.get('action', ['zip'])[0]

        if not items:
            self._redirect(f'/{tok}/')
            return

        client_ip = self._real_ip()
        device    = self._device()
        ts        = datetime.now().strftime('%Y%m%d_%H%M%S')
        label     = f'Batch ({len(items)} items)'
        t0        = time.monotonic()
        sent      = 0

        if action == 'zip':
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition',
                             f'attachment; filename="batch_{ts}.zip"')
            self.end_headers()

            zip_items: list[tuple[str, str]] = []
            for item in items:
                lp = self.translate_path(item)
                if not os.path.exists(lp):
                    continue
                if os.path.isdir(lp):
                    zip_items.extend(_collect_dir(lp))
                else:
                    zip_items.append((lp, os.path.basename(lp)))
            try:
                sent = _stream_zip(self.wfile, zip_items, client_ip, label)
                dur  = max(time.monotonic() - t0, 1e-9)
                TransferTracker.complete(client_ip, label)
                TerminalDashboard.log_row(client_ip, 'Batch ZIP sent',
                                          f'{len(items)} items', 'SUCCESS')
                logger.log('INFO', 'Batch ZIP Complete', {
                    'device': device, 'items': len(items), 'total_bytes': sent,
                    'avg_speed_mbps': round(sent / (1024**2) / dur, 2),
                    'duration_s': round(dur, 2),
                })
            except (BrokenPipeError, ConnectionResetError):
                TransferTracker.cancel(client_ip, label)
                TerminalDashboard.log_row(client_ip, 'Batch ZIP cancelled',
                                          f'{sent} bytes', 'WARN')

        elif action == 'tar':
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-tar')
            self.send_header('Content-Disposition',
                             f'attachment; filename="batch_{ts}.tar"')
            self.end_headers()
            try:
                with tarfile.open(fileobj=self.wfile, mode='w|') as tar:
                    for item in items:
                        lp = self.translate_path(item)
                        if os.path.exists(lp):
                            tar.add(lp, arcname=os.path.basename(lp))
                dur = max(time.monotonic() - t0, 1e-9)
                TerminalDashboard.log_row(client_ip, 'Batch TAR sent',
                                          f'{len(items)} items', 'SUCCESS')
                logger.log('INFO', 'Batch TAR Complete', {
                    'device': device, 'items': len(items),
                    'duration_s': round(dur, 2),
                })
            except (BrokenPipeError, ConnectionResetError):
                TransferTracker.cancel(client_ip, label)
                TerminalDashboard.log_row(client_ip, 'Batch TAR cancelled',
                                          '', 'WARN')
        else:
            self.send_error(400, 'Unknown action')

    # ---- GET / HEAD ---------------------------------------------------------

    def send_head(self):
        if self.path.endswith('/favicon.ico'):
            self.send_error(404)
            return None

        # Root / — 200 with meta-refresh so Cloudflare health checks pass.
        # A 303 would make CF follow the redirect to tx.local which CF cannot
        # resolve from its edge, causing a 502.
        if self.path == '/':
            self._touch_wan()
            tok  = SESSION_TOKEN
            body = (
                f'<html><head>'
                f'<meta http-equiv="refresh" content="0;url=/{tok}/">'
                f'</head><body><a href="/{tok}/">Open TransferX</a></body></html>'
            ).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            return io.BytesIO(body)

        # All other paths must carry the valid session token
        inner = self._strip_token(self.path)
        if inner is None:
            self._forbidden()
            return None

        self._touch_wan()
        self._tune()

        # /zip/<path> and /tar/<path>
        if inner.startswith('/zip/') or inner.startswith('/tar/'):
            is_zip     = inner.startswith('/zip/')
            sub        = inner[4:]
            local_path = self.translate_path(f'/{SESSION_TOKEN}{sub}')

            if not os.path.isdir(local_path):
                self.send_error(404, 'Not a directory')
                return None

            folder = os.path.basename(os.path.normpath(local_path))
            ext    = 'zip' if is_zip else 'tar'
            mime   = 'application/zip' if is_zip else 'application/x-tar'

            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Disposition',
                             f'attachment; filename="{folder}.{ext}"')
            self.end_headers()

            client_ip = self._real_ip()
            t0 = sent = 0
            t0 = time.monotonic()
            try:
                if is_zip:
                    sent = _stream_zip(self.wfile, _collect_dir(local_path),
                                       client_ip, folder)
                else:
                    with tarfile.open(fileobj=self.wfile, mode='w|') as tar:
                        tar.add(local_path, arcname=folder)
                dur = max(time.monotonic() - t0, 1e-9)
                TransferTracker.complete(client_ip, folder)
                TerminalDashboard.log_row(client_ip, f'Folder {ext} sent',
                                          folder, 'SUCCESS')
                logger.log('INFO', 'Folder sent', {
                    'device': self._device(), 'total_bytes': sent,
                    'avg_speed_mbps': round(sent / (1024**2) / dur, 2),
                    'duration_s': round(dur, 2),
                })
            except (BrokenPipeError, ConnectionResetError):
                TransferTracker.cancel(client_ip, folder)
                TerminalDashboard.log_row(client_ip, 'Folder cancelled',
                                          folder, 'WARN')
            return None

        # Directory listing
        local_path = self.translate_path(self.path)
        if os.path.isdir(local_path):
            if not self.path.endswith('/'):
                self._redirect(self.path + '/')
                return None
            try:
                entries = sorted(os.listdir(local_path), key=str.lower)
            except OSError:
                self.send_error(403, 'Permission denied')
                return None

            body = web_ui.render_directory(
                url_path=inner,
                fs_path=local_path,
                entries=entries,
                skip_names=_HIDDEN,
                token=SESSION_TOKEN,
            )
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            return io.BytesIO(body)

        # Raw file download
        if not os.path.exists(local_path):
            TerminalDashboard.log_row(self._real_ip(), '404', inner, 'WARN')
            self.send_error(404, 'Not found')
            return None

        ctype = self.guess_type(local_path)
        try:
            f = open(local_path, 'rb')
        except OSError:
            self.send_error(403, 'Cannot open')
            return None

        fs   = os.fstat(f.fileno())
        size = fs.st_size

        start, end = 0, size - 1
        is_range   = False
        rng        = self.headers.get('Range', '')
        if rng:
            m = re.match(r'bytes=(\d*)-(\d*)', rng)
            if m:
                s, e = m.groups()
                if s: start = max(0, int(s))
                if e: end   = min(size - 1, int(e))
                is_range = True

        length = end - start + 1

        if is_range:
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            self.send_response(200)

        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()

        if length > 0:
            filename  = os.path.basename(local_path)
            client_ip = self._real_ip()
            sent      = 0
            cancelled = False
            try:
                while sent < length:
                    to_send  = min(BUFFER_SIZE, length - sent)
                    t0       = time.monotonic()
                    n        = self.request.sendfile(f, offset=start + sent,
                                                     count=to_send)
                    if n == 0:
                        break
                    dt    = max(time.monotonic() - t0, 1e-9)
                    sent += n
                    TransferTracker.update(client_ip, filename, n, size, dt)
            except (BrokenPipeError, ConnectionResetError):
                cancelled = True
                TransferTracker.cancel(client_ip, filename)
                TerminalDashboard.log_row(client_ip, 'Cancelled',
                                          filename, 'WARN')
                logger.log('WARN', 'Transfer Cancelled', {
                    'device': self._device(),
                    'bytes_delivered': sent, 'total_bytes': size,
                })

            if not cancelled:
                TransferTracker.complete(client_ip, filename)
                TerminalDashboard.log_row(client_ip, 'Sent', filename, 'SUCCESS')
                logger.log('INFO', 'Transfer Complete', {
                    'device': self._device(), 'total_bytes': size,
                })

        f.close()
        return None


# ---- Server -----------------------------------------------------------------

class TurboServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads      = True
    request_queue_size  = 64
