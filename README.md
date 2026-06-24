# TethrLink

> Turn your Android tablet into a wired second monitor for Linux — no Wi-Fi, no cloud, no latency spikes.

TethrLink streams a virtual display from your Linux PC to an Android tablet over a direct USB tethering connection. The PC captures a dedicated virtual screen via the Mutter ScreenCast D-Bus API, encodes frames with GStreamer (JPEG by default, H.264 available), and pushes them over a private `192.168.42.x` USB subnet. The Android client decodes and renders them fullscreen in real time.

**Measured end-to-end latency: ~47 ms at 45 FPS (production default). Tested up to 120 FPS.**

---

## How It Works

```
┌─────────────────────────────────────────┐        USB Tethering (192.168.42.x)
│             Linux PC  (Server)          │
│                                         │
│  Mutter ScreenCast  → Virtual Display   │  TCP :51137  ┌──────────────────────┐
│  GStreamer pipeline → JPEG / H.264      │ ───────────► │   Android Tablet     │
│  UDP discovery broadcast                │             │                      │
│  GTK4 + Libadwaita desktop UI           │             │  recv → decode       │
│  pystray system tray                    │             │  → fullscreen render  │
└─────────────────────────────────────────┘             └──────────────────────┘
```

Transport: private `192.168.42.x` subnet, ~1–5 ms RTT over USB cable.

---

## Features

### Linux Server
- **Zero-config transport** — USB tethering only, no router or Wi-Fi needed
- **Independent virtual display** — tablet gets its own screen space via Mutter ScreenCast D-Bus API
- **Dual codec** — JPEG (default, stable) and H.264 via x264enc (`zerolatency` tune, BT.709 colorimetry, Annex B byte-stream); H.264 streaming is functional with quality improvements actively in progress
- **Hot-reload** — change FPS, JPEG quality, and H.264 bitrate live without restarting the stream
- **UDP auto-discovery** — server broadcasts on port 8765, Android app connects without manual IP entry
- **GTK4 + Libadwaita UI** — dark desktop app with three-tab dashboard and system tray integration
- **Persistent settings** — saved to `~/.config/tethrlink/settings.json`
- **Virtual display layout control** — position tablet above, below, left, or right of primary monitor
- **Resolution presets** — 720p, 1080p, 1440p, or custom size; landscape or portrait orientation
- **Auto-start** — optionally start the server on boot

### Android Client
- **Guided setup flow** — walks through USB cable detection, tethering, scanning, and connection
- **Auto-discovery** — listens for UDP broadcasts and populates server details automatically
- **Fullscreen immersive rendering** — hides system UI; screen stays on during streaming
- **Dual codec decoding** — JPEG via `BitmapFactory`; H.264 via `MediaCodec` async with hardware acceleration, NAL unit splitting, and SPS/PPS auto-detection (no pre-configuration needed)
- **Overlay HUD** — tap to toggle FPS counter, resolution, codec, server name, and disconnect button
- **Resolution & refresh rate selection** — choose 720p / 1080p / 1440p / 4K and 60 / 120 / 144 Hz before connecting
- **Auto-reconnect** — reconnects with 2-second delay on stream interruption

---

## Performance

| Metric         | JPEG (default, stable)        | H.264 (available, WIP quality)|
|----------------|-------------------------------|-------------------------------|
| Encode latency | 7.57 ms / frame (Q=80)        | ~5–10 ms / frame              |
| Frame size     | ~124 KB / frame (1920×1080)   | ~8 KB avg at 3 Mbps target    |
| Stream rate    | 45 FPS (prod) / 120 FPS (test)| 45 FPS (prod) / 120 FPS (test)|
| USB bandwidth  | ~40 Mbps                      | ~3–6 Mbps (configurable)      |
| End-to-end     | ~47 ms                        | ~47 ms                        |
| Color accuracy | —                             | BT.709                        |

---

## Requirements

**Linux PC:**
- GNOME on Wayland (Mutter ScreenCast API)
- Python 3.10+
- GStreamer 1.0 with `gstreamer1.0-pipewire`, `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`
- GTK4 + Libadwaita (`gir1.2-gtk-4.0`, `gir1.2-adw-1`)
- D-Bus bindings: `python3-gi`, `python3-dbus`

**Android tablet:**
- Android 5.0+ (API 21)
- USB cable with USB Tethering support

---

## Installation

> **H.264 note:** The stable release packages (`.deb`, `.snap`, APK) include JPEG streaming only. H.264 is available on the `develop` branch and requires installing from source on both the Linux server and Android. See [H.264 (develop branch)](#h264-develop-branch) below.

### Option 1 — Debian / Ubuntu package

Download the latest `.deb` from [Releases](https://github.com/princesavsaviya/TethrLink/releases) and install:

```bash
sudo dpkg -i tethrlink_1.0.0_amd64.deb
sudo apt-get install -f   # resolve any missing dependencies
tethrlink
```

### Option 2 — Snap package

```bash
snap install tethrlink_1.0.0_amd64.snap --dangerous
tethrlink
```

### Option 3 — Run from source

```bash
# Install system dependencies
sudo apt install python3-gi python3-dbus \
  gstreamer1.0-pipewire gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gir1.2-gstreamer-1.0 gir1.2-gtk-4.0 gir1.2-adw-1

# Clone and set up a venv (system site-packages required for GObject bindings)
git clone https://github.com/princesavsaviya/TethrLink.git
cd TethrLink
/usr/bin/python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt

# Launch
python -m server.app
```

### Android APK

Download `app-release.apk` from [Releases](https://github.com/princesavsaviya/TethrLink/releases) and install it on your tablet.

---

### H.264 (develop branch)

H.264 streaming is functional but quality is still being refined. If you want to try it early, install both the server and the Android app from the `develop` branch.

**Linux server:**

```bash
# Install H.264 encoder dependency (if not already present)
sudo apt install gstreamer1.0-plugins-ugly   # provides x264enc

# Clone the develop branch
git clone -b develop https://github.com/princesavsaviya/TethrLink.git
cd TethrLink
/usr/bin/python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt

# Launch
python -m server.app
```

Then open **Stream Settings**, switch the codec to **H.264**, and restart the stream.

**Android app:**

The released APK does not include the H.264 decoder update. You need to build the app from the `develop` branch:

1. Clone the repo (`-b develop`) and open the `android/` folder in Android Studio.
2. Connect your tablet via USB, select **Run → Run 'app'** (or build a signed APK via **Build → Generate Signed APK**).
3. Install the built APK on your tablet — it will replace the existing release version.

> H.264 quality improvements are ongoing. If you run into visual artifacts or distortion, switching back to JPEG in Stream Settings is the stable fallback.

---

## Usage

### 1 — Start the server

Run `tethrlink` (installed) or `python -m server.app` (from source).

The GTK4 window opens. Go to the **Dashboard** tab and click **Start Server**.

### 2 — Enable USB Tethering on Android

Connect the USB cable, then:

**Settings → Network → Hotspot & Tethering → USB Tethering → ON**

The PC's tethering address is always `192.168.42.129`.

### 3 — Open the Android app

The app walks you through the connection automatically:

1. **No USB** — prompts to connect the cable
2. **Tethering Off** — prompts to enable USB tethering in Settings
3. **Scanning** — listens for the server's UDP broadcast
4. **Server Found** — shows server details; choose resolution, refresh rate, and monitor position
5. **Streaming** — fullscreen display; tap to show the HUD overlay

---

## Desktop UI

The GTK4 app provides three tabs:

**Dashboard** — Start / stop the server, live connection status (codec, FPS, resolution, client device name), scrollable log.

**Stream Settings** — Codec (JPEG default / H.264 available), FPS slider (45 production, up to 120), JPEG quality, H.264 bitrate, TCP port, auto-start toggle. All changes apply live without restarting the stream.

**Display Settings** — Virtual display layout relative to your primary monitor (above / below / left / right), orientation (landscape / portrait), resolution preset (720p / 1080p / 1440p) or custom size.

---

## Tech Stack

| Layer           | Technology                                              |
|-----------------|---------------------------------------------------------|
| Server UI       | Python 3.12, GTK4 + Libadwaita, pystray                 |
| Screen capture  | Mutter ScreenCast D-Bus API (Wayland), mss (X11 fallback)|
| Encode / stream | GStreamer 1.0 (`x264enc` H.264 / `jpegenc` JPEG, `appsink`) |
| Transport       | TCP over USB tethering (`192.168.42.x` subnet)           |
| Discovery       | UDP broadcast (port 8765)                                |
| Settings        | JSON (`~/.config/tethrlink/settings.json`)               |
| Android client  | Kotlin 1.9, Jetpack Compose, MediaCodec, Coroutines      |

---

## Repository Layout

```
TethrLink/
├── server/
│   ├── app/
│   │   └── main.py           # GTK4 application entry point
│   ├── core/
│   │   ├── server_core.py    # Screen capture, encode, TCP server, virtual display
│   │   └── discovery.py      # UDP broadcast discovery
│   └── ui/
│       ├── window.py         # AdwApplicationWindow, 3-tab layout
│       ├── tray.py           # pystray system tray
│       └── style.css         # Custom GTK CSS
├── android/                  # Kotlin Android client (Jetpack Compose)
├── desktop/                  # .desktop launcher and app icon
├── snap/
│   └── snapcraft.yaml        # Snap package config
├── docs/                     # Landing page and screenshots
├── build_deb.sh              # Debian package builder script
├── stdeb.cfg                 # stdeb packaging config
└── setup.py                  # Python package metadata
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
| M6        | Auto-discovery (UDP broadcast)             | Done        |
| M7        | Snap & Debian packaging                    | Done        |
| M8        | Touch input forwarding (tablet → PC mouse) | Planned     |
| M9        | Audio forwarding over USB                  | Planned     |
| M10       | Windows server support                     | Planned     |

---

## Author

**Prince Savsaviya** — [princesavsaviya2023.learning@gmail.com](mailto:princesavsaviya2023.learning@gmail.com)

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.

TethrLink is free to use, modify, and distribute under GPL v3. If you distribute a modified version, you must release the source under the same license.