from __future__ import annotations
"""
logger.py — Structured JSON logger. Strips file paths; stamps perf analytics.
"""
import json
import threading
from datetime import datetime, timezone
from tracker import BUFFER_SIZE

LOG_FILE = "turbo_transfer.log"
_log_lock = threading.Lock()


def log(level: str, event: str, metadata: dict | None = None) -> None:
    """
    Write a single JSON line to turbo_transfer.log.

    Rules:
    - Absolute path values are stripped (privacy).
    - buffer_size_bytes is always stamped for perf correlation.
    """
    clean: dict = {}
    for k, v in (metadata or {}).items():
        if isinstance(v, str) and (v.startswith('/') or (len(v) > 2 and v[1] == ':')):
            continue  # drop OS paths
        clean[k] = v
    clean['buffer_size_bytes'] = BUFFER_SIZE

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level":     level,
        "event":     event,
        "metadata":  clean,
    }
    line = json.dumps(record, separators=(',', ':')) + '\n'
    with _log_lock:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
