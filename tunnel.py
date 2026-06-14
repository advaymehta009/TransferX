from __future__ import annotations
"""
tunnel.py - Cloudflare Quick Tunnel with idle-timeout security.

Security model:
  - Tunnel is killed automatically after IDLE_TIMEOUT minutes with no WAN activity.
    This closes the public internet endpoint even if the operator forgets to type q.
  - Tunnel is killed FIRST during shutdown (before LAN drain) so the internet
    attack surface closes immediately, not after waiting for transfers to finish.
  - WAN client IP is extracted from the CF-Connecting-IP / X-Forwarded-For header
    (cloudflared proxies everything through 127.0.0.1 locally).

Install cloudflared: brew install cloudflared
"""

import re
import shutil
import subprocess
import threading
import time
from typing import Callable


_CF_URL_RE     = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com')
_START_TIMEOUT = 30   # seconds to wait for cloudflared to print its URL

# Module-level state shared between handler threads and the idle watcher
_last_wan_activity: float = 0.0   # monotonic timestamp of last WAN request
_activity_lock = threading.Lock()


def record_wan_activity() -> None:
    """Called by server.py for every request that arrives via the tunnel."""
    global _last_wan_activity
    with _activity_lock:
        _last_wan_activity = time.monotonic()


def wan_idle_seconds() -> float:
    """Seconds since the last WAN request (0 if tunnel not yet used)."""
    with _activity_lock:
        if _last_wan_activity == 0.0:
            return 0.0
        return time.monotonic() - _last_wan_activity


def cloudflared_available() -> bool:
    return shutil.which('cloudflared') is not None


def start_tunnel(local_port: int,
                 on_url:     Callable[[str], None],
                 on_error:   Callable[[str], None],
                 on_idle_close: Callable[[], None] | None = None,
                 idle_timeout_minutes: int = 10,
                 ) -> subprocess.Popen | None:
    """
    Launch cloudflared quick tunnel.

    on_url(url)        - called when the public HTTPS URL is ready
    on_error(reason)   - called if tunnel fails to start in _START_TIMEOUT s
    on_idle_close()    - called when tunnel is killed due to idle timeout
    idle_timeout_minutes - kill tunnel after this many minutes of no WAN activity
                           (0 = never kill automatically)

    Returns the Popen object. Caller must call .terminate() on shutdown.
    """
    global _last_wan_activity
    _last_wan_activity = 0.0   # reset for new session

    if not cloudflared_available():
        on_error('cloudflared not found - install with: brew install cloudflared')
        return None

    try:
        proc = subprocess.Popen(
            ['cloudflared', 'tunnel',
             '--url', f'http://localhost:{local_port}',
             '--no-autoupdate'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        on_error(f'cloudflared failed to start: {e}')
        return None

    # Reader thread: watches stderr for the public URL
    url_found = threading.Event()

    def _reader():
        try:
            for line in proc.stderr:
                m = _CF_URL_RE.search(line)
                if m and not url_found.is_set():
                    url_found.set()
                    # Seed activity clock so idle timer starts from tunnel-ready
                    record_wan_activity()
                    on_url(m.group(0))
        except Exception:
            pass

    threading.Thread(target=_reader, daemon=True, name='cf-reader').start()

    # Watchdog: if URL not received in _START_TIMEOUT seconds, report failure
    def _start_watchdog():
        if not url_found.wait(timeout=_START_TIMEOUT):
            on_error(f'cloudflared timed out after {_START_TIMEOUT}s')

    threading.Thread(target=_start_watchdog, daemon=True, name='cf-start-wd').start()

    # Idle watcher: kill tunnel after N minutes of no WAN activity
    if idle_timeout_minutes > 0:
        idle_secs = idle_timeout_minutes * 60

        def _idle_watcher():
            # Wait for tunnel to be ready first
            url_found.wait(timeout=_START_TIMEOUT + 5)
            while proc.poll() is None:   # while cloudflared is running
                time.sleep(15)
                # Only start counting idle after first use
                with _activity_lock:
                    last = _last_wan_activity
                if last == 0.0:
                    continue   # no WAN traffic yet - don't count as idle
                idle = time.monotonic() - last
                if idle >= idle_secs:
                    proc.terminate()
                    if on_idle_close:
                        on_idle_close()
                    return

        threading.Thread(target=_idle_watcher, daemon=True,
                         name='cf-idle-wd').start()

    return proc
