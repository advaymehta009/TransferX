from __future__ import annotations
"""
dashboard.py — Terminal UI: fixed header + scrolling history + live progress block.

Layout (terminal, top to bottom):
  ═══ banner (fixed, printed once) ═══
  history rows  (append-only, scroll naturally)
  ─── live block (erased+redrawn every 0.4 s) ───

The live block is managed with ANSI cursor-up + erase-to-end.
_last_live_lines tracks EXACTLY how many lines were written last time
so the erase always lands on the right row — never bleeds into history.
"""

import os
import sys
import threading
from datetime import datetime

from tracker import TransferTracker

# ── colour palette (256-colour ANSI) ─────────────────────────────────────────
_R  = '\x1b[0m'          # reset
_B  = '\x1b[1m'          # bold
_DIM = '\x1b[2m'         # dim
_CYAN   = '\x1b[96m'
_GREEN  = '\x1b[92m'
_YELLOW = '\x1b[93m'
_RED    = '\x1b[91m'
_BLUE   = '\x1b[94m'
_GREY   = '\x1b[90m'
_WHITE  = '\x1b[97m'


def _fmt_eta(seconds: float) -> str:
    if seconds <= 0:
        return '—'
    s = int(seconds)
    if s < 60:
        return f'{s}s'
    if s < 3600:
        return f'{s//60}m {s%60:02d}s'
    return f'{s//3600}h {(s%3600)//60:02d}m'


def _fmt_size(b: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if b < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'


def _cols() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 120


# ─────────────────────────────────────────────────────────────────────────────

class TerminalDashboard:
    _last_live_lines: int = 0
    _ui_lock = threading.Lock()

    # Tunnel URL slot — set asynchronously once cloudflared reports back
    _tunnel_url: str = ''

    # ── banner ────────────────────────────────────────────────────────────────

    @staticmethod
    def clear_banner(serve: str, share_dir: str,
                     urls,           # mdns.URLs | None
                     token: str,
                     tunnel_url: str | None = None,
                     idle_timeout: int = 10) -> None:
        TerminalDashboard._banner_args = dict(
            serve=serve, share_dir=share_dir,
            urls=urls, token=token, idle_timeout=idle_timeout,
        )
        os.system('cls' if os.name == 'nt' else 'clear')
        w   = _cols()
        bar = '=' * w

        serve_label = {
            'local': f'{_GREEN}LOCAL{_R}  {_DIM}(LAN){_R}',
            'wan':   f'{_CYAN}WAN{_R}    {_DIM}(Cloudflare HTTPS){_R}',
            'both':  f'{_GREEN}LOCAL{_R} + {_CYAN}WAN{_R}',
        }.get(serve, serve)

        print(f'{_CYAN}{_B}{bar}{_R}')
        print(f'{_CYAN}{_B}  TransferX  --  local-network file transfer{_R}')
        print(f'{_CYAN}{bar}{_R}')
        print(f'  {_DIM}Serve       {_R}: {serve_label}')
        print(f'  {_DIM}Sharing     {_R}: {_WHITE}{share_dir}{_R}')

        if urls is not None:
            if urls.custom_local:
                print(f'  {_DIM}Local URL   {_R}: {_GREEN}{_B}{urls.custom_local}{_R}'
                      f'  {_DIM}<- try this first{_R}')
            if urls.hostname_local:
                label = '  {_DIM}<- if tx.local fails{_R}' if urls.custom_local else ''
                print(f'  {_DIM}Bonjour URL {_R}: {_GREEN}{urls.hostname_local}{_R}')
            print(f'  {_DIM}IP URL      {_R}: {_DIM}{urls.raw_ip}{_R}'
                  f'  {_DIM}<- always works{_R}')

        if serve in ('wan', 'both'):
            if tunnel_url:
                print(f'  {_DIM}Public URL  {_R}: {_CYAN}{_B}{tunnel_url}{_R}')
            else:
                print(f'  {_DIM}Public URL  {_R}: {_YELLOW}waiting for tunnel...{_R}')
            if idle_timeout > 0:
                print(f'  {_DIM}WAN idle    {_R}: {_YELLOW}auto-close after {idle_timeout} min{_R}')

        print(f'  {_DIM}Token       {_R}: {_YELLOW}{_B}{token}{_R}  {_DIM}(rotates each run){_R}')
        print(f'  {_DIM}Upload dir  {_R}: {_WHITE}{share_dir}/_uploads/{_R}')
        print(f'  {_DIM}Exit        {_R}: {_B}q{_R} + Enter (drain)  |  q q = force')
        print(f'{_CYAN}{"-" * w}{_R}')
        print(f'  {_DIM}{"Timestamp":<20}  {"Client":<18}  {"Status":<9}  Event{_R}')
        print(f'{_CYAN}{"-" * w}{_R}')

    _banner_args: dict = {}

    @classmethod
    def update_tunnel_url(cls, tunnel_url: str) -> None:
        with cls._ui_lock:
            cls._erase()
            a = cls._banner_args
            cls.clear_banner(
                serve=a.get('serve', 'both'),
                share_dir=a.get('share_dir', ''),
                urls=a.get('urls'),
                token=a.get('token', ''),
                tunnel_url=tunnel_url,
                idle_timeout=a.get('idle_timeout', 10),
            )
            cls._render()

    @classmethod
    def _erase(cls) -> None:
        if cls._last_live_lines > 0:
            sys.stdout.write(f'\x1b[{cls._last_live_lines}A\x1b[J')
            cls._last_live_lines = 0

    @classmethod
    def _render(cls) -> None:
        """
        CONTRACT: always sets _last_live_lines — 0 when nothing active.
        Must be called with _ui_lock held.
        """
        snap = TransferTracker.snapshot()

        if not snap:
            cls._last_live_lines = 0
            sys.stdout.flush()
            return

        w        = _cols()
        name_col = max(18, w - 14 - 22 - 40)

        stream_rows = []
        for (client_ip, label), d in snap.items():
            is_paused = d['paused']
            sent      = d['bytes_sent']
            total     = d['total_size']
            spd       = d['stored_speed'] if is_paused else d['ema_speed_mbps']

            if is_paused:
                badge = f'{_YELLOW}[PAUSED]{_R}'
                bar   = f'{_YELLOW}[{"━" * 20}    ]{_R}'
            else:
                badge = f'{_GREEN}[ACTIVE]{_R}'
                if total > 0:
                    pct    = min(100, sent * 100 // total)
                    filled = int(20 * sent // total)
                    bar    = (f'{_GREEN}[{"█" * filled}'
                              f'{_GREY}{"░" * (20 - filled)}{_GREEN}]{_R}')
                else:
                    bar = f'{_BLUE}[{"·" * 8}  streaming  {"·" * 8}]{_R}'
                    pct = -1

            spd_str = f'{spd:.1f} MB/s'
            if is_paused:
                metrics = f'{_YELLOW}{_fmt_size(int(sent))} · {spd_str} (frozen){_R}'
            elif total > 0:
                remaining = total - sent
                eta = (remaining / (spd * 1024 * 1024)) if spd > 0 else 0
                metrics = (f'{_WHITE}{pct}%{_R} · '
                           f'{_CYAN}{spd_str}{_R} · '
                           f'ETA {_B}{_fmt_eta(eta)}{_R}')
            else:
                metrics = f'{_CYAN}{_fmt_size(int(sent))} · {spd_str}{_R}'

            name = label if len(label) <= name_col else label[:name_col - 1] + '…'
            stream_rows.append(
                f'  {badge} {_B}{name:<{name_col}}{_R}  {bar}  {metrics}'
            )

        w_str = '─' * w
        lines = ([f'{_CYAN}{w_str}{_R}',
                  f'  {_DIM}Active streams: {len(snap)}{_R}']
                 + stream_rows
                 + [f'{_CYAN}{w_str}{_R}'])

        sys.stdout.write('\n'.join(lines) + '\n')
        cls._last_live_lines = len(lines)
        sys.stdout.flush()

    # ── public API ────────────────────────────────────────────────────────────

    @classmethod
    def refresh(cls) -> None:
        with cls._ui_lock:
            cls._erase()
            cls._render()

    @classmethod
    def log_row(cls, client_ip: str, action: str, detail: str,
                level: str = 'INFO') -> None:
        colour = {'INFO': _GREY, 'SUCCESS': _GREEN,
                  'WARN': _YELLOW, 'ERROR': _RED}.get(level, _GREY)
        with cls._ui_lock:
            cls._erase()
            ts     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            badge  = f'{colour}[{level:<7}]{_R}'
            ip_str = f'{_DIM}{client_ip:<18}{_R}'
            print(f'  {_DIM}{ts}{_R}  {ip_str}  {badge}  {action}: {_B}{detail}{_R}')
            cls._render()

    @classmethod
    def shutdown_clean(cls) -> None:
        with cls._ui_lock:
            cls._erase()
