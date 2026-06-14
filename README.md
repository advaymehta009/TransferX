# ⚡ TransferX

Zero-config **HTTPS** file transfer over your local network.  
Drag a folder, get a URL (and QR code), open it on any device on the same Wi-Fi.

```
uv run python main.py ~/Downloads/media
```

```
══════════════════════════════════════════════════════════════════════════
  ⚡  TRANSFERX  — local-network turbo engine
══════════════════════════════════════════════════════════════════════════
  Share root  : /Users/{username}/Downloads/media
  IP link     : https://xx.xxx.x.xx:{some_random_port}
  mDNS link   : https://{local_url}.local:{some_random_port}  (same LAN, no IP needed)
  Exit        : type q and Enter  |  or Ctrl-C
────────────────────────────────────────────────────────────────────────

  ▄▄▄▄▄ ▄  ▄ ▄▄  ▄▄▄▄▄
  █   █ ██▄▀ ██  █   █
  █▄▄▄█ █ █  ██  █▄▄▄█
  ▄▄▄▄▄▄▄ ▄▀▄ ▄▄▄▄▄▄▄
  …
```

---

## Quick start

```bash
# Install deps (one-time)
cd ~/Projects/learning/transferX
uv sync

# Share a folder (interactive prompt if no arg given)
uv run python main.py
uv run python main.py ~/Downloads/shows
uv run python main.py /Volumes/NAS/Movies --port 9000
```

Open the printed URL on your phone. Accept the certificate once ("Advanced → Proceed") — it's self-signed and local-only.

---

## Features

| | |
|---|---|
| **HTTPS** | Self-signed cert auto-generated each run. mDNS `.local` name so you never type an IP. |
| **QR code** | Scan from the terminal to open on phone instantly. |
| **Folder input** | CLI arg, or interactive prompt — share any path, not just cwd. |
| **Raw download** | Click any file. Full HTTP range/resume support. |
| **Folder → ZIP** | One-click per folder. Streams directly, no temp file. |
| **Batch ZIP / TAR** | Checkbox multi-select, then Download .zip or .tar. |
| **Parallel downloads** | One OS thread per connection — 15 cores, 15 simultaneous streams. |
| **EWMA ETA** | Speed is an exponential moving average per-chunk, not a stale cumulative. ETA responds to real current throughput. |
| **Pause detection** | Idle >2 s → `[PAUSED]` with frozen metrics. Resumes cleanly. |
| **Ghost-free** | Cancelled/dropped connections removed instantly — never lingers as PAUSED. |
| **Beautiful web UI** | Dark glassmorphism — works on mobile, tablets, desktops. |
| **Structured logs** | `turbo_transfer.log` — JSON, with avg speed, duration, buffer config. No file paths. |

---

## Terminal dashboard

```
────────────────────────────────────────────────────────────────────────
  Active streams: 3
  [ACTIVE] The.Wire.S01E01.mkv    [████████████░░░░░░░░]  62% · 91.4 MB/s · ETA 8s
  [PAUSED] BigBuckBunny.mkv       [━━━━━━━━━━━━━━━━━━━━    ]  31% · 48.2 MB/s (frozen)
  [ACTIVE] batch (4 items)        [·········  streaming  ·········]  1.2 GB · 78.3 MB/s
────────────────────────────────────────────────────────────────────────
```

---

## Performance (large files)

- **`os.sendfile`** — kernel zero-copy: data travels disk → NIC without touching Python. This is the ceiling for single-stream throughput.
- **8 MB socket buffer** per connection — keeps the NIC fed on gigabit+ links.
- **One thread per connection** — `ThreadingMixIn` + `daemon_threads=True`. The GIL is released during socket I/O and `sendfile`, so all 15 cores can run transfers simultaneously.
- **No compression** — ZIP_STORED / streaming TAR. Compressing at CPU speed would bottleneck 200 GB transfers. Clients decompress after download.
- **Range requests** — browsers and download managers use byte-range resumption automatically. A dropped 150 GB transfer picks up where it left off.

---

## About the certificate

A fresh RSA-2048 self-signed cert is generated each run and saved as `.cert.pem` in the share directory. It's valid for 24 hours and covers:
- The server's LAN IP
- `transferx.local`
- `<hostname>.local`

**To avoid the browser warning permanently:** install `.cert.pem` into your device's trust store (iPhone: AirDrop it → Settings → Profile → Install; Android: Settings → Security → Install certificate).

The private key (`.key.pem`) is deleted automatically on clean exit.

---

## Project layout

```
transferX/
├── main.py        # CLI entry point, startup, QR code
├── server.py      # HTTPS handler, TLS cert, sendfile streaming
├── tracker.py     # Transfer state, EWMA speed, pause/cancel
├── dashboard.py   # Terminal UI — coloured live progress block
├── web_ui.py      # Browser HTML/CSS/JS — dark glassmorphism
├── logger.py      # Structured JSON logger
└── pyproject.toml
```

## 🚀 Installation & Setup

Download the optimized standalone binary for your operating system from the **Releases** tab.

### 🪟 Windows
1. Download `TransferX_Windows.exe`.
2. Double-click the file to launch the service in your Command Prompt/PowerShell.

### 🍏 macOS
1. Download and extract `TransferX_Mac.zip`.
2. Open your terminal, navigate to the folder where you extracted it, and run this command to bypass Apple Gatekeeper security permissions:
```bash
   xattr -d com.apple.quarantine TransferX_Mac

## Requirements

Python 3.11+ · `cryptography` · `segno` (both auto-installed by `uv sync`)
