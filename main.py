from __future__ import annotations
"""
main.py - TransferX entry point.

Usage:
  uv run python main.py                     # prompt for folder, auto mode
  uv run python main.py ~/Movies            # share specific folder
  sudo uv run python main.py ~/Movies       # port 80, URL = http://tx.local/<token>/

  --serve local   LAN only   -> http://tx.local:7474/<token>/
  --serve wan     WAN only   -> https://xxx.trycloudflare.com/<token>/
  --serve both    LAN + WAN  (default when cloudflared is installed)

  --idle-timeout N  Kill WAN tunnel after N minutes idle (default 10, 0=never)
  --no-qr           Skip QR code(s)

Local URL is always:  http://tx.local[:port]/<token>/
  'tx' is a short fixed mDNS name registered via zeroconf.

Safe termination (type q):
  1. Cloudflare tunnel killed FIRST  <- closes internet exposure immediately
  2. mDNS unregistered               <- removes LAN discovery
  3. Stop accepting new connections
  4. Wait for in-flight transfers to finish (drain, up to --drain-timeout s)
  5. Full exit
  Type q a second time during drain to force-exit immediately.
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

import server as srv
import tunnel as tun
from dashboard import TerminalDashboard
from tracker import BUFFER_SIZE, TransferTracker
import logger

LOCAL_PORT     = 7474
DRAIN_TIMEOUT  = 60    # seconds to wait for in-flight transfers on q


# ---- Token ------------------------------------------------------------------

def _new_token() -> str:
    alpha = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alpha) for _ in range(8))


# ---- Network ----------------------------------------------------------------

def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        s.close()


# ---- mDNS -------------------------------------------------------------------

def _register_mdns(ip: str, port: int, token: str) -> object | None:
    """
    Register 'tx.local' via zeroconf.

    ServiceInfo with server='tx.local.' publishes:
      - PTR/SRV/TXT records for _http._tcp.local. (service browser discovery)
      - An A record: tx.local -> ip  (hostname resolution for http://tx.local/...)

    'tx' is short, fixed, memorable — same name every run regardless of
    the machine hostname. Bonjour (macOS/iOS) and Avahi (Linux/Android) both
    resolve it on the LAN without any manual configuration.
    """
    try:
        from zeroconf import Zeroconf, ServiceInfo
        info = ServiceInfo(
            '_http._tcp.local.',
            'TransferX._http._tcp.local.',
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={'path': f'/{token}/'},
            server='tx.local.',
        )
        zc = Zeroconf()
        zc.register_service(info)
        return zc
    except Exception:
        return None


def _local_url(port: int, token: str) -> str:
    port_part = '' if port == 80 else f':{port}'
    return f'http://tx.local{port_part}/{token}/'


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
    """Terminate the Cloudflare process. Fast — closes internet exposure."""
    if cf_proc is not None:
        try:
            cf_proc.terminate()
            cf_proc.wait(timeout=3)
        except Exception:
            pass


def _unregister_mdns(zc) -> None:
    if zc is not None:
        try: zc.close()
        except Exception: pass


def _drain_transfers(httpd) -> None:
    """
    Close the server socket (stop new connections) then wait for
    in-flight transfers to finish or DRAIN_TIMEOUT to expire.
    """
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
        print(f'\r  Draining: {n} transfer(s) in progress... ({remaining}s to force)  ',
              end='', flush=True)
        time.sleep(0.5)
    print(f'\n  Drain timeout ({DRAIN_TIMEOUT}s) — forcing exit.')


def _do_shutdown(httpd, zc, cf_proc, force: bool = False) -> None:
    """
    Ordered shutdown:
      1. Kill WAN tunnel first  (internet exposure closed immediately)
      2. Unregister mDNS        (LAN discovery gone)
      3. Drain in-flight transfers (unless force=True)
      4. Stop server + exit
    """
    global _shutdown_called
    with _shutdown_lock:
        if _shutdown_called:
            return
        _shutdown_called = True

    TerminalDashboard.shutdown_clean()

    # Step 1 — kill internet exposure FIRST, regardless of anything else
    _kill_tunnel(cf_proc)
    TerminalDashboard.log_row('system', 'WAN tunnel closed', '', 'INFO')

    # Step 2 — remove mDNS registration
    _unregister_mdns(zc)

    # Step 3 — drain or force
    active = TransferTracker.active_count()
    if active > 0 and not force:
        print(f'\n  {active} transfer(s) still active.')
        print(f'  Waiting up to {DRAIN_TIMEOUT}s for them to finish.')
        print('  Type q again to force-exit now.\n')
        _drain_transfers(httpd)
    else:
        try: httpd.socket.close()
        except Exception: pass

    # Step 4 — full server stop
    print('\n  Shutting down server...')
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
        help=(
            'local = LAN only (http://tx.local:7474/<token>/)  '
            'wan = Cloudflare tunnel only  '
            'both = LAN + tunnel  '
            'auto = both if cloudflared installed, else local'
        ),
    )
    parser.add_argument('--port', '-p', type=int, default=None,
                        help=f'Local port (default {LOCAL_PORT}; 80 if sudo)')
    parser.add_argument(
        '--idle-timeout', type=int, default=10, metavar='MINUTES',
        help='Kill WAN tunnel after N minutes of no activity (0=never, default 10)',
    )
    parser.add_argument('--no-qr', action='store_true', help='Skip QR code(s)')
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

    # ---- token + server globals ---------------------------------------------
    token = _new_token()
    srv.SESSION_TOKEN    = token
    srv.WAN_MODE_ACTIVE  = wants_tunnel

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
    zc        = None
    local_url = _local_url(port, token)
    if wants_local:
        zc = _register_mdns(ip, port, token)

    # ---- Cloudflare tunnel --------------------------------------------------
    cf_proc = None

    if wants_tunnel:
        def _on_idle_close():
            TerminalDashboard.log_row(
                'system',
                'WAN tunnel closed',
                f'idle > {args.idle_timeout} min — internet exposure removed',
                'WARN',
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

    # ---- banner + QR --------------------------------------------------------
    TerminalDashboard.clear_banner(
        serve=serve,
        share_dir=share_dir,
        local_url=local_url if wants_local else None,
        token=token,
        tunnel_url=None,
        idle_timeout=args.idle_timeout if wants_tunnel else 0,
    )

    if wants_local and not args.no_qr:
        _print_qr(local_url,
                  'LAN — scan on same Wi-Fi:' if serve == 'both' else '')

    # ---- log ----------------------------------------------------------------
    logger.log('INFO', 'TransferX started', {
        'serve': serve,
        'port': port,
        'idle_timeout_minutes': args.idle_timeout if wants_tunnel else None,
        'share_dir_basename': os.path.basename(share_dir),
        'buffer_size_bytes': BUFFER_SIZE,
    })

    # ---- signals ------------------------------------------------------------
    def _sig(signum, frame):
        _do_shutdown(httpd, zc, cf_proc)

    signal.signal(signal.SIGINT,  _sig)
    signal.signal(signal.SIGTERM, _sig)

    # ---- background threads -------------------------------------------------
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=_refresh_loop,       daemon=True).start()

    # ---- CLI ----------------------------------------------------------------
    while True:
        try:
            cmd = input().strip().lower()
            if cmd in ('q', 'quit', 'exit'):
                if _draining:
                    # Second q = force exit
                    _do_shutdown(httpd, zc, cf_proc, force=True)
                else:
                    _do_shutdown(httpd, zc, cf_proc)
        except (KeyboardInterrupt, EOFError):
            _do_shutdown(httpd, zc, cf_proc)


def _refresh_loop() -> None:
    while True:
        time.sleep(0.4)
        TerminalDashboard.refresh()


if __name__ == '__main__':
    main()
