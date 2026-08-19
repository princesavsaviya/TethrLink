# TethrLink

> Turn an Android tablet into a wired second monitor for Linux, over USB — no Wi-Fi, no router, no cloud.

TethrLink creates a **real second display** on your Linux PC — not a mirror of your existing screen — and streams it to an Android tablet over a direct USB connection. Windows you drag onto it stay there. The PC captures a dedicated virtual monitor through GNOME's ScreenCast API, encodes it with hardware H.264, and sends it over the private subnet that USB tethering already provides.

**Version 2.0.0** — the tablet can now drive your pointer. Touch is off by default; turn it on in the server window.

---

## How it works

```
┌────────────────────────────────────────────┐
│  Linux PC (server)                         │      USB tethering
│                                            │      192.168.42.x
│  Mutter ScreenCast ─► virtual monitor      │
│  PipeWire          ─► frame capture        │   TCP :51137   ┌─────────────────┐
│  GStreamer         ─► H.264 (GPU) / JPEG   │ ─────────────► │ Android tablet  │
│  UDP broadcast     ─► auto-discovery       │                │  MediaCodec ─►  │
│  GTK4 desktop app                          │                │  fullscreen     │
└────────────────────────────────────────────┘                └─────────────────┘
```

The virtual monitor is sized from **your PC's display height and the tablet's aspect ratio**, so the shared edge lines up for seamless pointer movement and nothing is stretched on the panel. See [Display geometry](#display-geometry).

With touch enabled, taps and drags on the tablet drive the PC's pointer on that
virtual display — see [Touch input](#touch-input).

---

## Requirements

**Linux PC**
- **GNOME on Wayland** — required for H.264. The virtual display uses Mutter's ScreenCast D-Bus API, which has no cross-compositor equivalent. X11 works but falls back to JPEG (see [Limitations](#limitations)).
- Python 3.10+
- GStreamer 1.20+ with the PipeWire, base, good and ugly plugin sets
- GTK4 and the GObject introspection bindings

**Android tablet**
- Android 5.0+ (API 21), USB tethering support
- A hardware H.264 decoder — effectively universal, since AVC decode is mandatory in Android's compatibility definition

**Optional but recommended:** a GPU with a hardware H.264 encoder — NVIDIA (NVENC), Intel or AMD (VA-API), or Intel QSV. TethrLink detects and verifies one at runtime and falls back to software encoding when none works.

---

## Installation

### Debian/Ubuntu package (.deb)

```bash
git clone https://github.com/princesavsaviya/TethrLink.git
cd TethrLink
./build_deb.sh
sudo apt install ./tethrlink_2.0.0_all.deb
```

There's no hosted release yet, so you build the package locally — `build_deb.sh` copies the current `server/` source tree and pulls in `mss`/`qrcode[pil]` via pip; `apt` then resolves the GStreamer, GTK4 and Libadwaita dependencies declared in the package. This installs a `tethrlink` launcher, a desktop entry, and an icon, all via normal `apt`/`dpkg` mechanisms (`sudo apt remove tethrlink` to uninstall).

This path was broken for the 1.1.0 and 2.0.0 releases — the packaged code had silently drifted from source — and has just been fixed. It's been verified by inspecting the built package's contents (every module present, matching source byte-for-byte) but hasn't seen the same real-world mileage as the from-source path below. If something looks off, from-source is the fallback.

### From source

```bash
sudo apt install python3-gi python3-dbus python3-pil \
  gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-ugly gir1.2-gstreamer-1.0 gir1.2-gtk-4.0 gir1.2-adw-1

# Optional — hardware encoding
sudo apt install gstreamer1.0-plugins-bad gstreamer1.0-vaapi

git clone https://github.com/princesavsaviya/TethrLink.git
cd TethrLink
/usr/bin/python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt

python3 -m server.app.main
```

The venv **must** use `--system-site-packages`: the GObject and GStreamer bindings come from the system, not from pip.

### Android app

Build from `android/` in Android Studio and install on the tablet.

---

## Usage

1. **Start the server** and click **Start Server**.
2. **Enable USB tethering** on the tablet: *Settings → Connections → Mobile Hotspot and Tethering → USB tethering*. The PC is reachable at `192.168.42.129`.
3. **Open the Android app.** It listens for the server's UDP broadcast and connects on its own.
4. **Arrange the display** in *GNOME Settings → Displays*. The tablet appears as a new monitor; place it where you want and drag windows across.

---

## Configuration

The desktop window exposes a **codec selector** (H.264 / JPEG). It is locked while streaming, because the encoding pipeline is built once per connection — a change applies to the next one. The status line shows the codec **actually in use**, which can differ from the one requested (an X11 session always reports JPEG).

Everything else is set through environment variables:

| Variable | Example | Effect |
|---|---|---|
| `TETHRLINK_CODEC` | `jpeg` | Force a codec, overriding the UI |
| `TETHRLINK_RES` | `1920x1080` | Force capture resolution instead of deriving it |

```bash
TETHRLINK_CODEC=jpeg python3 -m server.app.main
```

Settings are **not** persisted between runs. Encoder capability and per-device records are cached under `~/.cache/tethrlink/profiles.json`, but that is a cache, not configuration — deleting it costs one slower startup and nothing else.

---

## Display geometry

The capture size is derived rather than fixed:

```
height = min(your monitor's height, the tablet's height)
width  = height × (the tablet's aspect ratio)
```

A 1920×1080 PC with a 2960×1848 tablet gives **1730×1080**. Three things fall out of that:

- **The shared edge aligns.** GNOME only lets the pointer cross where two monitors overlap vertically. Matching heights makes the whole edge crossable instead of leaving an invisible wall.
- **Nothing is stretched.** Taking the aspect from the tablet means the upscale is uniform in both axes. Matching the PC's 16:9 to a 16:10 panel would stretch the picture about 11%.
- **Decode stays affordable.** The tablet's native 2960×1848 at 30 fps demands roughly 164 Mpx/s, near the practical ceiling for one H.264 stream.

Override it with `TETHRLINK_RES` if you would rather trade decode headroom for sharpness.

---

## Touch input

Off by default. A capability that can drive your desktop should be opted into,
so enable it in the server window before connecting.

- **Tap** clicks, **drag** drags, **long press** right-clicks, **two-finger
  drag** scrolls. Gesture timings come from Android's own `ViewConfiguration`,
  so they match every other app on the tablet and honour accessibility settings.
- **Every touch clicks — there is no hover.** That is inherent to driving a
  pointer by absolute position, and it means tooltips and hover menus will not
  trigger from touch.
- Input is injected through GNOME's `RemoteDesktop` API and is **scoped to the
  virtual display by the platform itself** — it cannot reach your laptop's own
  screen.
- **Press Back on the tablet** to reveal the disconnect overlay while streaming.
- Requires GNOME on Wayland, like the virtual display. X11 sessions get video
  only, and the window says so rather than claiming otherwise.

### Who the server will talk to

The server serves only peers reachable over a **USB-attached network
interface**, plus loopback. Wi-Fi and container bridges are refused. That
matters more now than it did for a view-only stream: reaching the port used to
mean seeing your screen, and with input it would mean controlling the machine.

The tether subnet is detected at runtime rather than assumed, so it works
whatever address your device hands out. `TETHRLINK_TETHER_SUBNET` overrides it,
and rejects anything implausibly broad rather than silently disabling the
filter.

There is still **no authentication** on the connection. Over a cable that is a
reasonable trust boundary; any future transport that is not a cable must add
pairing first.

## The video pipeline

**Dropping happens before encoding, never after.** Discarding a raw frame only lowers the frame rate; discarding an *encoded* frame breaks the decoder's reference chain and corrupts everything until the next keyframe. So a leaky queue sits upstream of the encoder, and everything downstream is lossless.

**The encoder is negotiated at runtime, not assumed.** Candidates are tried hardware-first, and each is accepted only after it genuinely encodes a frame at the real capture size. This matters more than it sounds: an element can exist, expose the right property, and still fail — and the rate-control modes a driver offers are read from the GPU itself, so the same element differs between machines. The winning choice is cached with a fingerprint of the GStreamer install, so a driver upgrade re-probes automatically.

**A constant frame rate is manufactured downstream.** Mutter's capture is damage-driven: a display showing something static stops producing frames entirely. A `compositor` running on its own clock supplies a steady rate at zero added latency, so motion resumes instantly instead of after a stall.

**Buffering is kept deliberately shallow** — about four frames in flight, roughly 133 ms at 30 fps. A deeper queue would hide jitter, but at the cost of latency on *every* frame.

---

## Measured performance

Encoder throughput, 120 frames of synthetic video, on a GTX 1650 Ti with an Intel UHD iGPU:

| Encoder | 1280×720 | 1920×1080 | 2960×1848 |
|---|---|---|---|
| `nvh264enc` (NVENC) | 320 fps | 196 fps | 93 fps |
| `x264enc` ultrafast CBR | 141 fps | 71 fps | 39 fps |
| `x264enc` veryfast CBR | 84 fps | 48 fps | 30 fps |

Other measurements from the same machine:

- **USB link:** the tablet enumerates at USB 2.0 High Speed — 480 Mbit/s nominal, ~200–300 Mbit/s realistic. H.264 at 1730×1080/30 targets about 16 Mbit/s, roughly 6% of that.
- **Uncompressed video is not viable:** raw NV12 at 2960×1848/30 is ~1.9 Gbit/s, several times what the link can carry. Compression is what makes this work at all.
- **Encoder startup:** ~2.06 s to probe and verify cold, ~0.44 s from cache.

No end-to-end latency figure is quoted here because none has been measured under controlled conditions. Earlier versions of this document cited one; it was not reproducible.

---

## Limitations

- **H.264 requires GNOME on Wayland.** The virtual display uses Mutter's private ScreenCast API. KDE, Sway and other compositors have no equivalent, and there is no cross-compositor standard for *creating* a virtual output. X11 sessions fall back to JPEG at the PC's own resolution.
- **JPEG sends whole frames.** It has no inter-frame compression, so it costs far more bandwidth and CPU. It exists as a fallback, not as a quality option.
- **An idle session still costs CPU.** Holding a constant frame rate means running the pipeline continuously even when nothing changes — roughly half a core. Worth knowing on battery.
- **The frame rate applies per connection.** H.264 pins it in the pipeline at connect time; changing it takes effect on the next connection.
- **One client at a time.** A second device is answered with a busy signal.
- **The process can crash on exit** after an H.264 session, during GStreamer/NVENC teardown. It happens after streaming has ended and does not affect the session.

---

## Repository layout

```
TethrLink/
├── server/
│   ├── app/main.py            # GTK4 entry point
│   ├── core/
│   │   ├── server_core.py     # Capture, encode, virtual display, TCP server
│   │   ├── encoder.py         # Vendor-neutral encoder selection + property mapping
│   │   ├── geometry.py        # Capture-size derivation
│   │   ├── frame_queue.py     # Lossless FIFO (H.264) and latest-wins slot (JPEG)
│   │   ├── profiles.py        # Cached encoder capability and device records
│   │   ├── metrics.py         # Stream counters
│   │   ├── preflight.py       # Startup GStreamer/encoder diagnostics
│   │   └── discovery.py       # UDP broadcast
│   └── ui/window.py           # GTK4 window
├── android/                   # Kotlin client (Compose + XML layouts, MediaCodec)
├── tools/
│   └── diagnose_capture_stall.py   # Isolates capture stalls from the rest of the pipeline
├── tests/                     # 163 unit tests
└── docs/superpowers/          # Design specs and implementation plans
```

Run the tests with `./venv/bin/python -m pytest`.

---

## Roadmap

| Feature | Status |
|---|---|
| MJPEG over USB, length-prefixed TCP framing | Done |
| GStreamer pipeline, Mutter virtual display | Done |
| UDP auto-discovery, Snap and Debian packaging | Done |
| Hardware H.264 with runtime encoder negotiation | Done (1.1.0) |
| Device-derived display geometry | Done (1.1.0) |
| Touch input — pointer, click, right-click, scroll | Done (2.0.0) |
| Adaptive bitrate from measured link conditions | Planned |
| Touch input forwarding (tablet → PC pointer) | Planned |
| Audio forwarding | Planned (2.1.0) |
| Keyboard input | Planned (2.2.0) |
| Real multi-touch and gestures | Planned |

---

## Author

**Prince Savsaviya** — [princesavsaviya2023.learning@gmail.com](mailto:princesavsaviya2023.learning@gmail.com)

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
