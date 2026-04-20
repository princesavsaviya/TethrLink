# TetherLink

> Turn your Android tablet into a wired second monitor for Linux — no Wi-Fi, no cloud, no latency spikes.

TetherLink streams a virtual display from your Linux PC to an Android tablet over a direct USB tethering connection. The PC captures a dedicated virtual screen via the Mutter ScreenCast D-Bus API, encodes frames with GStreamer (JPEG or H.264), and pushes them over a private `192.168.42.x` USB subnet. The Android client decodes and renders them fullscreen in real time.

**Measured end-to-end latency: ~47 ms at 30 FPS.**

---

## How It Works

```
┌─────────────────────────────────────────┐        USB Tethering (192.168.42.x)
│             Linux PC  (Server)          │
│                                         │
│  Mutter ScreenCast  → Virtual Display   │  TCP :8080  ┌──────────────────────┐
│  GStreamer pipeline → JPEG / H.264      │ ──────────► │   Android Tablet     │
│                     → [4B len][frame]   │             │                      │
│  GTK4 + Libadwaita desktop UI           │             │  recv → decode       │
│  pystray system tray                    │             │  → fullscreen render  │
└─────────────────────────────────────────┘             └──────────────────────┘
```

Transport: private `192.168.42.x` subnet, ~1–5 ms RTT over USB cable.

---

## Features

- **Zero-config transport** — USB tethering, no router, no Wi-Fi needed
- **Independent virtual display** — tablet gets its own screen space via Mutter ScreenCast
- **Dual codec** — software JPEG or GStreamer H.264, switchable at runtime
- **Hot-reload** — change FPS and JPEG quality without restarting the stream
- **GTK4 + Libadwaita UI** — dark desktop app with system tray integration
- **UDP auto-discovery** — Android app finds the server without manual IP entry
- **Persistent settings** — saved to `~/.config/tetherlink/settings.json`

---

## Performance

| Metric         | Value                         |
|----------------|-------------------------------|
| Encode latency | 7.57 ms / frame (JPEG, Q=80)  |
| Frame size     | ~124 KB / frame (1920×1080)   |
| Stream rate    | 30 FPS                        |
| USB bandwidth  | ~40 Mbps                      |
| End-to-end     | ~47 ms                        |

---

## Requirements

**Linux PC:**
- GNOME on Wayland (Mutter ScreenCast API)
- Python 3.10+
- GStreamer 1.0 with `gstreamer1.0-pipewire`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`
- GTK4 + Libadwaita (`gir1.2-gtk-4.0`, `gir1.2-adw-1`)

**Android tablet:**
- Android 8.0+ (API 26)
- USB cable + USB Tethering enabled

---

## Installation

### Option 1 — Debian / Ubuntu package

Download the latest `.deb` from [Releases](https://github.com/princesavsaviya/TetherLink/releases) and install:

```bash
sudo dpkg -i python3-tetherlink_1.0.0-1_all.deb
sudo apt-get install -f   # resolve any missing dependencies
tetherlink
```

### Option 2 — Run from source

```bash
# Install system dependencies
sudo apt install python3-gi python3-dbus \
  gstreamer1.0-pipewire gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gir1.2-gstreamer-1.0 gir1.2-gtk-4.0 gir1.2-adw-1

# Clone and set up a venv (system site-packages required for GObject bindings)
git clone https://github.com/princesavsaviya/TetherLink.git
cd TetherLink
/usr/bin/python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt

# Launch
python -m server.app
```

---

## Usage

### 1 — Start the server

Run `tetherlink` (installed) or `python -m server.app` (from source).

The GTK4 window opens. Go to the **Dashboard** tab and click **Start Server**.

### 2 — Enable USB Tethering on Android

**Settings → Network → Hotspot & Tethering → USB Tethering → ON**

The PC's tethering address is always `192.168.42.129`.

### 3 — Open the Android app

Install the APK from [Releases](https://github.com/princesavsaviya/TetherLink/releases). The app discovers the server automatically via UDP broadcast and connects.

---

## Desktop UI

The GTK4 app provides three tabs:

**Dashboard** — Start / stop the server, live connection status (codec, FPS, resolution, client IP), scrollable log.

**Stream Settings** — Codec (JPEG / H.264), FPS slider, JPEG quality, H.264 bitrate, TCP port, auto-start toggle. FPS and quality changes apply live without restarting the stream.

**Display Settings** — Virtual display layout relative to your primary monitor (above / below / left / right), orientation (landscape / portrait), resolution preset (720p / 1080p / 1440p) or custom size.

---

## Tech Stack

| Layer          | Technology                                              |
|----------------|---------------------------------------------------------|
| Server UI      | Python 3.12, GTK4 + Libadwaita, pystray (xorg backend) |
| Screen capture | Mutter ScreenCast D-Bus API                             |
| Encode / stream| GStreamer 1.0 (`jpegenc` / `x264enc` / `appsink`)       |
| Transport      | TCP over USB tethering (`192.168.42.x` subnet)          |
| Discovery      | UDP broadcast                                           |
| Settings       | JSON (`~/.config/tetherlink/settings.json`)             |
| Android client | Kotlin 1.9, Android SDK 34, Coroutines                  |

---

## Repository Layout

```
TetherLink/
├── server/
│   ├── app.py            # GTK4 application entry point
│   ├── server_core.py    # Screen capture, encode, TCP server
│   ├── discovery.py      # UDP broadcast discovery
│   ├── tray.py           # pystray system tray (xorg backend)
│   └── ui/
│       ├── window.py     # AdwApplicationWindow, 3-tab layout
│       └── style.css     # Custom GTK CSS
├── android/              # Kotlin Android client
├── tests/                # Protocol, compatibility, stress, security tests
├── desktop/              # .desktop launcher entry
└── setup.py              # Debian packaging config
```

---

## Roadmap

| Milestone | Feature                                    | Status      |
|-----------|--------------------------------------------|-------------|
| M1        | MJPEG over USB, size-prefixed TCP framing  | Done        |
| M2        | Configurable IP / port from Android UI     | Done        |
| M3        | GStreamer pipeline, Mutter virtual display | Done        |
| M4        | Dynamic port scan, Material You dark theme | Done        |
| M5        | GTK4 + Libadwaita desktop UI               | Done        |
| M6        | Touch input forwarding (tablet → PC mouse) | Planned     |
| M7        | Audio forwarding over USB                  | Planned     |
| M8        | Auto-discovery (UDP broadcast)             | Done        |
| M9        | Windows server support                     | Planned     |

---

## Author

**Prince Savsaviya** — [princesavsaviya.learning@gmail.com](mailto:princesavsaviya.learning@gmail.com)
