"""
tracker.py - Transfer state: per-stream metrics, EMA speed, pause/cancel/drain.
"""
import time
import threading

_EMA_ALPHA  = 0.15
BUFFER_SIZE = 8 * 1024 * 1024   # 8 MB - sweet spot for LAN + big files


class TransferTracker:
    _lock    = threading.Lock()
    _entries: dict = {}

    # ---- write --------------------------------------------------------------

    @classmethod
    def update(cls, client_ip: str, label: str, chunk_bytes: int,
               total_size: int, chunk_duration: float) -> int:
        with cls._lock:
            key = (client_ip, label)
            now = time.monotonic()
            if key not in cls._entries:
                cls._entries[key] = {
                    'bytes_sent':     0,
                    'total_size':     total_size,
                    'last_update':    now,
                    'ema_speed_mbps': 0.0,
                    'stored_speed':   0.0,
                    'paused':         False,
                }
            else:
                if cls._entries[key]['paused']:
                    cls._entries[key]['paused'] = False

            e = cls._entries[key]
            inst = (chunk_bytes / (1024 * 1024)) / chunk_duration if chunk_duration > 0 else 0.0
            e['ema_speed_mbps'] = (inst if e['ema_speed_mbps'] == 0.0
                                   else _EMA_ALPHA * inst + (1 - _EMA_ALPHA) * e['ema_speed_mbps'])
            e['bytes_sent']  += chunk_bytes
            e['last_update']  = now
            return e['bytes_sent']

    @classmethod
    def complete(cls, client_ip: str, label: str) -> None:
        with cls._lock:
            cls._entries.pop((client_ip, label), None)

    @classmethod
    def cancel(cls, client_ip: str, label: str) -> None:
        with cls._lock:
            cls._entries.pop((client_ip, label), None)

    # ---- read ---------------------------------------------------------------

    @classmethod
    def active_count(cls) -> int:
        """Number of currently tracked transfers (paused ones count too)."""
        with cls._lock:
            return len(cls._entries)

    @classmethod
    def snapshot(cls) -> dict:
        now = time.monotonic()
        with cls._lock:
            result = {}
            for key, data in cls._entries.items():
                copy = dict(data)
                if now - data['last_update'] > 2.0:
                    copy['paused'] = True
                    if not data['paused']:
                        data['stored_speed'] = data['ema_speed_mbps']
                        data['paused'] = True
                result[key] = copy
        return result
