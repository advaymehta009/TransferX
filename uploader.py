from __future__ import annotations
"""
uploader.py - Server-side parallel chunk upload manager.

Protocol (browser -> server):
  Each file is split into CHUNK_SIZE pieces browser-side.
  Each chunk is a raw binary POST (no multipart overhead) with headers:
    X-File-Id:     random UUID per file (groups chunks)
    X-Filename:    original filename (URL-encoded)
    X-Part:        0-based chunk index
    X-Total-Parts: total number of chunks
    X-File-Size:   total file size in bytes
  Body: raw bytes of that chunk

  When all X-Total-Parts chunks are received for a file-id, the upload
  is considered complete. No "complete" request needed — the server tracks
  received parts and finalises automatically.

Storage strategy:
  - Pre-allocate the final file at full size on first chunk arrival
    (os.ftruncate — instant, no actual disk I/O)
  - Each chunk uses os.pwrite(fd, data, offset) to write directly to the
    correct byte offset in the final file
  - os.pwrite is atomic, releases the GIL, and needs no locking between
    threads writing different offsets of the same file
  - No temp files, no reassembly step, no extra disk space

Threading:
  ThreadingMixIn gives each HTTP connection its own OS thread.
  The GIL is released during socket.recv() and os.pwrite(), so all 15
  cores can be running simultaneous chunk writes at full speed.
  Python code per chunk (header parsing etc.) is ~microseconds.
"""

import os
import threading
import time
import urllib.parse
from dataclasses import dataclass, field


CHUNK_SIZE = 4 * 1024 * 1024   # 4 MB — matches browser-side (safe under CF 100MB limit)


@dataclass
class _FileState:
    filename:    str
    total_parts: int
    file_size:   int
    dest_path:   str
    fd:          int             # open file descriptor for pwrite
    received:    set = field(default_factory=set)
    lock:        threading.Lock = field(default_factory=threading.Lock)
    started_at:  float = field(default_factory=time.monotonic)
    done:        bool = False


class UploadManager:
    """
    Thread-safe registry of in-progress chunked uploads.
    One instance shared across all handler threads.
    """

    def __init__(self, upload_dir: str):
        self._dir   = upload_dir
        self._files: dict[str, _FileState] = {}
        self._lock  = threading.Lock()
        os.makedirs(upload_dir, exist_ok=True)

    def _safe_name(self, raw: str) -> str:
        """Sanitise filename — allow alphanum, dot, dash, underscore, space."""
        import re
        name = os.path.basename(urllib.parse.unquote(raw))
        name = re.sub(r'[^\w\s.\-]', '_', name).strip() or 'upload'
        return name

    def _unique_path(self, name: str) -> str:
        dest = os.path.join(self._dir, name)
        if not os.path.exists(dest):
            return dest
        base, _, ext = name.rpartition('.')
        n = 1
        while True:
            candidate = os.path.join(
                self._dir,
                f'{base}_{n}.{ext}' if ext else f'{name}_{n}',
            )
            if not os.path.exists(candidate):
                return candidate
            n += 1

    def receive_chunk(self, file_id: str, raw_filename: str,
                      part: int, total_parts: int,
                      file_size: int, data: bytes) -> tuple[bool, str, int, float]:
        """
        Write one chunk to the correct offset in the destination file.

        Returns (is_complete, dest_filename, total_bytes, elapsed_seconds).
        is_complete=True when all parts have arrived.
        """
        safe = self._safe_name(raw_filename)

        # Get or create the file state
        with self._lock:
            if file_id not in self._files:
                dest = self._unique_path(safe)
                # Pre-allocate the full file immediately (just sets inode size,
                # no actual disk write for the empty space)
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT, 0o644)
                os.ftruncate(fd, file_size)
                self._files[file_id] = _FileState(
                    filename=os.path.basename(dest),
                    total_parts=total_parts,
                    file_size=file_size,
                    dest_path=dest,
                    fd=fd,
                )
            state = self._files[file_id]

        # Write this chunk directly to its offset — no lock needed because
        # each part writes a different region (os.pwrite is atomic per call)
        offset = part * CHUNK_SIZE
        written = 0
        while written < len(data):
            n = os.pwrite(state.fd, data[written:], offset + written)
            if n == 0:
                break
            written += n

        # Mark part received and check for completion
        with state.lock:
            state.received.add(part)
            is_complete = (len(state.received) == state.total_parts
                           and not state.done)
            if is_complete:
                state.done = True

        if is_complete:
            os.close(state.fd)
            elapsed = time.monotonic() - state.started_at
            with self._lock:
                del self._files[file_id]
            return True, state.filename, state.file_size, elapsed

        return False, state.filename, len(data), 0.0

    def cleanup_stale(self, max_age_seconds: int = 3600) -> None:
        """Remove file state entries older than max_age_seconds (safety net)."""
        now = time.monotonic()
        with self._lock:
            stale = [fid for fid, s in self._files.items()
                     if now - s.started_at > max_age_seconds]
        for fid in stale:
            with self._lock:
                state = self._files.pop(fid, None)
            if state:
                try: os.close(state.fd)
                except Exception: pass
