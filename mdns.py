from __future__ import annotations
"""
mdns.py - mDNS registration with macOS dns-sd, Linux zeroconf, and raw IP fallback.

Why tx.local fails: python-zeroconf cannot register a NEW custom hostname
on macOS because mDNSResponder already owns port 5353 exclusively for A-record
probing. The fix is dns-sd -P which talks to mDNSResponder directly via IPC
(not the network socket), bypassing the conflict entirely.

Three URLs are always computed and shown in the banner so the user has
guaranteed fallbacks:
  1. http://tx.local:PORT/token/          dns-sd (macOS) / zeroconf (Linux)
  2. http://<hostname>.local:PORT/token/  Bonjour always registers the real hostname
  3. http://IP:PORT/token/                always works, everywhere
"""

import os
import platform
import socket
import subprocess
from typing import NamedTuple


class URLs(NamedTuple):
    custom_local:   str | None   # http://tx.local:PORT/token/
    hostname_local: str | None   # http://hostname.local:PORT/token/
    raw_ip:         str          # http://IP:PORT/token/  — always set


class MDNSHandle:
    """Holds registration handles. Call .close() on shutdown."""
    _dns_sd_proc: subprocess.Popen | None = None
    _zeroconf:    object | None           = None

    def close(self) -> None:
        if self._dns_sd_proc is not None:
            try:
                self._dns_sd_proc.terminate()
                self._dns_sd_proc.wait(timeout=2)
            except Exception:
                pass
        if self._zeroconf is not None:
            try:
                self._zeroconf.close()
            except Exception:
                pass


def _hostname_local() -> str:
    h = socket.gethostname()
    return h if h.endswith('.local') else h + '.local'


def _port_str(port: int) -> str:
    return '' if port == 80 else f':{port}'


def _register_macos(ip: str, port: int, token: str) -> subprocess.Popen | None:
    """
    Register tx.local through mDNSResponder via dns-sd -P.
    dns-sd talks to the Bonjour daemon over a Unix socket — not port 5353 —
    so there is no conflict with mDNSResponder. The record is visible to
    every device on the LAN within ~1 second.

    Command: dns-sd -P <Name> <Type> <Domain> <Port> <Host> <IP> [TXT]
    """
    exe = '/usr/bin/dns-sd'
    if not os.path.isfile(exe):
        return None
    try:
        return subprocess.Popen(
            [exe, '-P',
             'TransferX', '_http._tcp', 'local',
             str(port), 'tx.local', ip,
             f'path=/{token}/'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None


def _register_zeroconf(ip: str, port: int, token: str) -> object | None:
    """Linux/avahi path — zeroconf can own port 5353 without conflicts."""
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


def register(ip: str, port: int, token: str) -> tuple[MDNSHandle, URLs]:
    """
    Register mDNS and return all three reachable URLs.
    Caller stores the MDNSHandle and calls .close() on shutdown.
    """
    handle = MDNSHandle()
    pp     = _port_str(port)

    raw_ip_url  = f'http://{ip}{pp}/{token}/'
    hn_url      = f'http://{_hostname_local()}{pp}/{token}/'
    tx_url      = f'http://tx.local{pp}/{token}/'

    if platform.system() == 'Darwin':
        proc = _register_macos(ip, port, token)
        if proc is not None:
            handle._dns_sd_proc = proc
            custom = tx_url
        else:
            custom = None   # dns-sd unavailable — rare on macOS
    else:
        zc = _register_zeroconf(ip, port, token)
        if zc is not None:
            handle._zeroconf = zc
            custom = tx_url
        else:
            custom = None

    return handle, URLs(
        custom_local   = custom,
        hostname_local = hn_url,
        raw_ip         = raw_ip_url,
    )
