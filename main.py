from __future__ import annotations
"""
main.py - TransferX entry point.

Usage:
  uv run python main.py                    # prompt for folder, auto mode
  uv run python main.py ~/Movies           # share specific folder
  sudo uv run python main.py ~/Movies      # port 80, no port in URL

  --serve local   LAN only  (no Cloudflare)
  --serve wan     Cloudflare tunnel only
  --serve both    LAN + Cloudflare (default when cloudflared is installed)

  --idle-timeout N  Kill WAN tunnel after N min idle (default 10, 0=never)
  --no-qr           Skip QR code(s)

Local URLs shown in banner (best-to-worst reliability):
  http://tx.local:7474/<token>/        <- short fixed name (macOS: dns-sd; Linux: avahi)
  http://<hostname>.local:7474/<token>/ <- machine's real Bonjour name (always works on macOS)
  http://192.168.x.x:7474/<token>/    <- raw IP (always works, everywhere)

Safe termination (type q):
  1. Cloudflare tunnel killed FIRST  (internet exposure closed immediately)
  2. mDNS unregistered
  3. Stop accepting new connections
  4. Wait up to 60s for in-flight transfers to finish
  5. Type q again during drain to force-exit
"""

import argparse
import os
import secrets
import signal
import socket
import string
import sys
import threading
import time

import mdns
import server as srv
import tunnel as tun
import uploader as _up
from dashboard import TerminalDashboard
from tracker import BUFFER_SIZE, TransferTracker
import logger

LOCAL_PORT    = 7474
DRAIN_TIMEOUT = 60


# ---- Token ------------------------------------------------------------------

def _new_token() -> str:
    alpha = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alpha) for _ in range(8))


# ---- LAN IP -----------------------------------------------------------------

def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


# ---- QR ---------------------------------------------------------------------

def _print_qr(url: str, label: str = '') -> None:
    try:
        import segno, io as _io
        if label:
            print(f'  {label}')
        qr  = segno.make_qr(url, error='L')
        buf = _io.StringIO()
        qr.terminal(out=buf, compact=True)
        for line in buf.getvalue().splitlines():
            print('    ' + line)
        print()
    except ImportError:
        pass


# ---- Shutdown / drain -------------------------------------------------------

_shutdown_lock   = threading.Lock()
_shutdown_called = False
_draining        = False


def _kill_tunnel(cf_proc) -> None:
    if cf_proc is not None:
        try:
            cf_proc.terminate()
            cf_proc.wait(timeout=3)
        except Exception:
            pass


def _drain_transfers(httpd) -> None:
    global _draining
    _draining = True
    try:
        httpd.socket.close()
    except Exception:
        pass
    deadline = time.monotonic() + DRAIN_TIMEOUT
    while time.monotonic() < deadline:
        n = TransferTracker.active_count()
        if n == 0:
            print('  All transfers complete.')
            return
        remaining = int(deadline - time.monotonic())
        print(f'\r  Draining: {n} transfer(s) in progress... ({remaining}s)  ',
              end='', flush=True)
        time.sleep(0.5)
    print(f'\n  Drain timeout ({DRAIN_TIMEOUT}s) — forcing exit.')


def _do_shutdown(httpd, mdns_handle, cf_proc, force: bool = False) -> None:
    """
    Ordered shutdown — internet closed FIRST, then drain local transfers.
    """
    global _shutdown_called
    with _shutdown_lock:
        if _shutdown_called:
            return
        _shutdown_called = True

    TerminalDashboard.shutdown_clean()

    # 1. Kill internet exposure immediately
    _kill_tunnel(cf_proc)

    # 2. Remove LAN discovery
    if mdns_handle is not None:
        mdns_handle.close()

    # 3. Drain or force
    active = TransferTracker.active_count()
    if active > 0 and not force:
        print(f'\n  {active} transfer(s) still active.')
        print(f'  Waiting up to {DRAIN_TIMEOUT}s for them to finish.')
        print('  Type q again to force-exit now.\n')
        _drain_transfers(httpd)
    else:
        try: httpd.socket.close()
        except Exception: pass

    # 4. Stop server
    print('\n  Shutting down...')
    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass

    logger.log('INFO', 'TransferX stopped', {})
    print('  Done. Goodbye.')
    sys.exit(0)


# ---- Entry point ------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog='transferx',
        description='TransferX - local-network file transfer',
    )
    parser.add_argument('directory', nargs='?', default=None,
                        help='Folder to share (prompt if omitted)')
    parser.add_argument(
        '--serve', choices=['local', 'wan', 'both', 'auto'], default='auto',
        help='local=LAN only  wan=tunnel only  both=LAN+tunnel  '
             'auto=both if cloudflared installed, else local',
    )
    parser.add_argument('--port', '-p', type=int, default=None,
                        help=f'Local port (default {LOCAL_PORT}; 80 if sudo)')
    parser.add_argument(
        '--idle-timeout', type=int, default=10, metavar='MINUTES',
        help='Kill WAN tunnel after N minutes idle (0=never, default 10)',
    )
    parser.add_argument('--no-qr', action='store_true',
                        help='Skip QR code(s)')
    args = parser.parse_args()

    # ---- resolve --serve ----------------------------------------------------
    serve = args.serve
    if serve == 'auto':
        serve = 'both' if tun.cloudflared_available() else 'local'

    wants_tunnel = serve in ('wan', 'both')
    wants_local  = serve in ('local', 'both')

    if wants_tunnel and not tun.cloudflared_available():
        print('\n  cloudflared not found. Install: brew install cloudflared')
        print('  Falling back to --serve local.\n')
        serve        = 'local'
        wants_tunnel = False
        wants_local  = True

    # ---- share directory ----------------------------------------------------
    share_dir = args.directory
    if not share_dir:
        print()
        print('  \x1b[96m\x1b[1mTransferX\x1b[0m')
        print()
        default = os.getcwd()
        try:
            entered = input(f'  Folder to share [{default}]: ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\n  Cancelled.')
            sys.exit(0)
        share_dir = entered if entered else default

    share_dir = os.path.realpath(os.path.expanduser(share_dir))
    if not os.path.isdir(share_dir):
        print(f'\n  \x1b[91mError:\x1b[0m Not a directory: {share_dir}')
        sys.exit(1)

    os.chdir(share_dir)
    srv.TurboHandler.share_root = share_dir

    # Initialise upload manager — creates _uploads/ sub-dir
    upload_dir = os.path.join(share_dir, '_uploads')
    srv.UPLOAD_MGR = _up.UploadManager(upload_dir)

    # ---- token + server globals ---------------------------------------------
    token = _new_token()
    srv.SESSION_TOKEN   = token
    srv.WAN_MODE_ACTIVE = wants_tunnel

    # ---- port ---------------------------------------------------------------
    if args.port:
        port = args.port
    elif os.geteuid() == 0:
        port = 80
    else:
        port = LOCAL_PORT

    # ---- HTTP server --------------------------------------------------------
    httpd = srv.TurboServer(('0.0.0.0', port), srv.TurboHandler)
    port  = httpd.server_address[1]

    ip = _lan_ip()

    # ---- mDNS ---------------------------------------------------------------
    mdns_handle = None
    urls        = None

    if wants_local:
        mdns_handle, urls = mdns.register(ip, port, token)
    else:
        # wan-only: still compute raw IP URL for the banner
        pp   = '' if port == 80 else f':{port}'
        urls = mdns.URLs(
            custom_local   = None,
            hostname_local = None,
            raw_ip         = f'http://{ip}{pp}/{token}/',
        )

    # Primary local URL for QR: prefer tx.local, fall back to hostname, then IP
    primary_local = (urls.custom_local
                     or urls.hostname_local
                     or urls.raw_ip)

    # ---- Cloudflare tunnel --------------------------------------------------
    cf_proc = None

    if wants_tunnel:
        def _on_idle_close():
            TerminalDashboard.log_row(
                'system', 'WAN tunnel auto-closed',
                f'idle > {args.idle_timeout} min', 'WARN',
            )

        def _on_url(cf_url: str):
            full = cf_url + f'/{token}/'
            TerminalDashboard.update_tunnel_url(full)
            if not args.no_qr:
                threading.Thread(
                    target=_print_qr,
                    args=(full, 'WAN — scan from anywhere:'),
                    daemon=True,
                ).start()

        def _on_error(reason: str):
            TerminalDashboard.log_row('system', 'Tunnel error', reason, 'WARN')

        cf_proc = tun.start_tunnel(
            local_port=port,
            on_url=_on_url,
            on_error=_on_error,
            on_idle_close=_on_idle_close,
            idle_timeout_minutes=args.idle_timeout,
        )

    # ---- banner -------------------------------------------------------------
    TerminalDashboard.clear_banner(
        serve=serve,
        share_dir=share_dir,
        urls=urls if wants_local else None,
        token=token,
        tunnel_url=None,
        idle_timeout=args.idle_timeout if wants_tunnel else 0,
    )

    # QR for local mode (tunnel QR fires async via _on_url)
    if wants_local and not args.no_qr:
        label = 'LAN — scan on same Wi-Fi:' if serve == 'both' else ''
        _print_qr(primary_local, label)

    # ---- log ----------------------------------------------------------------
    logger.log('INFO', 'TransferX started', {
        'serve': serve, 'port': port,
        'idle_timeout_minutes': args.idle_timeout if wants_tunnel else None,
        'share_dir_basename': os.path.basename(share_dir),
        'buffer_size_bytes': BUFFER_SIZE,
    })

    # ---- signals ------------------------------------------------------------
    def _sig(signum, frame):
        _do_shutdown(httpd, mdns_handle, cf_proc)

    signal.signal(signal.SIGINT,  _sig)
    signal.signal(signal.SIGTERM, _sig)

    # ---- threads ------------------------------------------------------------
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=_refresh_loop,       daemon=True).start()

    # ---- CLI ----------------------------------------------------------------
    while True:
        try:
            cmd = input().strip().lower()
            if cmd in ('q', 'quit', 'exit'):
                if _draining:
                    _do_shutdown(httpd, mdns_handle, cf_proc, force=True)
                else:
                    _do_shutdown(httpd, mdns_handle, cf_proc)
        except (KeyboardInterrupt, EOFError):
            _do_shutdown(httpd, mdns_handle, cf_proc)


def _refresh_loop() -> None:
    while True:
        time.sleep(0.4)
        TerminalDashboard.refresh()


if __name__ == '__main__':
    main()
