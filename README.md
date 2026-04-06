# TetherLink

TetherLink turns an Android tablet into a wired second monitor for your Linux PC using USB tethering. The PC runs a Python server that captures a virtual display via Mutter ScreenCast, encodes frames with GStreamer (JPEG or H.264), and streams them over a direct USB TCP connection. The Android client receives and displays frames fullscreen in real time — no Wi-Fi, no cloud, no latency spikes.

---

## Architecture

```
┌──────────────────────────────────────────┐         USB Tethering (192.168.42.x)
│              PC  (Server)                │
│                                          │
│  Mutter ScreenCast  →  Virtual Display   │    TCP :8080   ┌─────────────────────┐
│  GStreamer pipeline →  JPEG / H.264      │ ──────────────► │   Android Tablet    │
│                     →  [4B len][frame]   │                 │                     │
│  GTK4 + Libadwaita UI                    │                 │  recv → decode      │
│  pystray system tray                     │                 │  → fullscreen view  │
└──────────────────────────────────────────┘                 └─────────────────────┘
```

Transport: private `192.168.42.x` subnet over USB, ~1–5 ms RTT.
End-to-end latency: ~47 ms at 30 FPS (7.6 ms encode + ~39 ms transport + decode).

---

## Milestones

| Milestone | Description                                                      | Status |
|-----------|------------------------------------------------------------------|--------|
| **M1**    | MJPEG over USB tethering, size-prefixed TCP framing              | ✅ Done |
| **M2**    | Configurable IP / port from Android Settings UI                  | ✅ Done |
| **M3**    | GStreamer pipeline — JPEG + H.264, virtual display via Mutter    | ✅ Done |
| **M4**    | Dynamic port auto-scan, named constants, Material You dark theme | ✅ Done |
| **M5**    | GTK4 + Libadwaita desktop GUI replacing headless terminal server | ✅ Done (`feature/server-ui`) |
| M6        | Touch input forwarding (tablet → PC mouse events)                | Planned |
| M7        | Audio forwarding over USB                                        | Planned |
| M8        | Auto-discovery (mDNS / UDP broadcast, no manual IP entry)        | Planned |
| M9        | Windows server support                                           | Planned |

---

## Quick Start

### Requirements (Linux)

```bash
# System packages
sudo apt install python3-gi python3-dbus \
  gstreamer1.0-pipewire gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gir1.2-gstreamer-1.0 gir1.2-gtk-4.0 gir1.2-adw-1

# Create venv with system site-packages (required for gi / GStreamer bindings)
/usr/bin/python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r server/requirements.txt
```

### 1 — Start the server (GUI mode)

```bash
./server/run_server.sh
```

This opens the **TetherLink** GTK4 window. Click **Start Server** from the Dashboard tab.

To run without the GUI (headless / SSH mode):

```bash
./server/run_server.sh --headless [--fps 30] [--quality 80] [--port 8080] [--codec jpeg|h264]
```

### 2 — Enable USB Tethering on Android

**Settings → Network → Hotspot & Tethering → USB Tethering → ON**

The tablet gets an IP on `192.168.42.x`; the PC's tethering address is always `192.168.42.129`.

### 3 — Connect from the Android app

Open the TetherLink Android app. It auto-connects to `192.168.42.129:8080` (configurable in Settings).

---

## Server UI (M5)

The GTK4 desktop app runs `ServerCore` in-process — no subprocess, no IPC.

**Dashboard tab** — Start / stop server, live connection status (codec, FPS, resolution, client IP), scrollable log.

**Stream Settings tab** — Codec toggle (JPEG / H.264), FPS slider (hot-reload, no restart), JPEG quality slider (hot-reload), H.264 bitrate, TCP port, auto-start on launch.

**Display Settings tab** — Visual arrangement diagram: click next to the primary monitor rectangle to position the virtual display above / below / left / right. Orientation (Landscape / Portrait), resolution presets (720p / 1080p / 1440p) and custom size with Apply.

**System tray** — pystray xorg backend (GTK4-safe, no GTK3 conflict). Shows connection status; "Open" restores the window; "Quit" stops the server cleanly.

### Run the M5 branch in a worktree

```bash
git worktree add .worktrees/feature-server-ui feature/server-ui
cd .worktrees/feature-server-ui
./server/run_server.sh
```

---

## Features

- Zero-configuration USB tethering transport — no Wi-Fi required
- Mutter ScreenCast virtual display — tablet gets its own independent screen space
- Dual codec: software JPEG or GStreamer H.264
- Hot-reload FPS and JPEG quality without restarting the stream
- 30 FPS @ ~40 Mbps over USB; ~47 ms end-to-end latency
- GTK4 + Libadwaita dark UI with resizable window and system tray
- Settings persisted to `~/.config/tetherlink/settings.json`
- Headless CLI mode for scripted or SSH use

---

## Tech Stack

| Layer          | Technology                                            |
|----------------|-------------------------------------------------------|
| Server UI      | Python 3.12, GTK4 + Libadwaita (Adw), pystray (xorg) |
| Screen capture | Mutter ScreenCast D-Bus API                           |
| Encode/stream  | GStreamer 1.0 (jpegenc / x264enc / appsink)           |
| Transport      | TCP socket, USB tethering (192.168.42.x)              |
| Settings       | JSON (`~/.config/tetherlink/settings.json`)           |
| Android client | Kotlin 1.9, Android SDK 34, Coroutines                |

---

## Measured Performance

| Metric          | Value                         |
|-----------------|-------------------------------|
| Encode latency  | 7.57 ms / frame (JPEG, Q=80) |
| Frame size      | ~124 KB / frame (1920×1080)  |
| Stream rate     | 30 FPS                        |
| USB bandwidth   | ~40 Mbps                      |
| End-to-end E2E  | ~47 ms                        |

---

## Repository Layout

```
TetherLink/
├── server/
│   ├── app.py                   # GTK4 application entry point (M5)
│   ├── server_core.py           # Capture + encode + TCP server
│   ├── settings.py              # JSON config persistence
│   ├── tray.py                  # pystray system tray (xorg backend)
│   ├── tetherlink_server.py     # Headless CLI shim
│   ├── run_server.sh            # Launcher (GUI default, --headless flag)
│   ├── ui/
│   │   ├── window.py            # AdwApplicationWindow, 3-tab layout
│   │   ├── dashboard.py         # Status card, stat chips, log view
│   │   ├── stream_settings.py   # Codec, FPS, quality, bitrate, port
│   │   └── display_settings.py  # Visual arrangement, orientation, resolution
│   └── icons/
│       ├── tetherlink.png
│       └── ic_monitor.svg
├── android/                     # Android Kotlin client
└── tests/
    ├── test_server_core.py
    └── test_settings.py
```
