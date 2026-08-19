# TethrLink

> Turn an Android tablet into a **real second display** for Linux, over a USB cable. No Wi-Fi, no router, no cloud.

TethrLink creates a genuine second monitor on your Linux PC — not a mirror of a screen you already have. Windows you drag onto it stay there, GNOME arranges it like any other display, and the whole thing runs over the private network that USB tethering already gives you.

The PC captures a dedicated virtual monitor through GNOME's ScreenCast API, encodes it with hardware H.264, and streams it down the cable. The tablet decodes it and renders fullscreen.

**Version 2.0.0 — the tablet can now drive your pointer.** Tap to click, drag to drag, long-press to right-click, two-finger drag to scroll. Touch is off by default; you turn it on in the server window.

---

## How it works

```
┌──────────────────────────────────────────────┐
│  Linux PC (server)                           │       USB tethering
│                                              │       (subnet detected at runtime)
│  Mutter ScreenCast ──► virtual monitor       │
│  PipeWire          ──► frame capture         │   TCP :51137   ┌──────────────────┐
│  GStreamer         ──► H.264 (GPU) / JPEG    │ ─────────────► │  Android tablet  │
│  UDP :8765         ──► auto-discovery        │                │   MediaCodec ──► │
│  GTK4 desktop app                            │ ◄───────────── │   fullscreen     │
│  RemoteDesktop     ◄── pointer injection     │  input channel │   touch capture  │
└──────────────────────────────────────────────┘                └──────────────────┘
```

Video goes down the cable; with touch enabled, pointer intent comes back up the same TCP connection.

---

## Requirements

**Linux PC**

- **GNOME on Wayland.** Required for both the virtual display and touch input — the virtual monitor uses Mutter's ScreenCast D-Bus API and input uses GNOME's `RemoteDesktop` API, neither of which has a cross-compositor equivalent. X11 sessions still work, but fall back to JPEG at the PC's own resolution and get video only, no touch.
- Python 3.10+
- GStreamer 1.20+ with the PipeWire, base, good and ugly plugin sets
- GTK4, Libadwaita, and the GObject introspection bindings

**Android tablet**

- Android 5.0+ (API 21) with USB tethering support
- A hardware H.264 decoder — effectively universal, since AVC decode is mandatory in Android's compatibility definition

**Optional but recommended:** a GPU with a hardware H.264 encoder — NVIDIA (NVENC), Intel or AMD (VA-API), or Intel QSV. TethrLink detects and *verifies* one at runtime and falls back to software encoding when none works.

---

## Installation

### Snap

```bash
sudo snap install tethrlink
```

### Debian/Ubuntu package (.deb)

```bash
git clone https://github.com/princesavsaviya/TethrLink.git
cd TethrLink
./build_deb.sh
sudo apt install ./tethrlink_2.0.0_all.deb
```

`build_deb.sh` copies the current `server/` source tree and pulls in `mss` and `qrcode[pil]` via pip; `apt` then resolves the GStreamer, GTK4 and Libadwaita dependencies declared in the package. You get a `tethrlink` launcher, a desktop entry and an icon through normal `apt`/`dpkg` mechanisms. Uninstall with `sudo apt remove tethrlink`.

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

The venv **must** use `--system-site-packages`. The GObject and GStreamer bindings come from the system, not from pip.

### Android app

Build from `android/` in Android Studio and install on the tablet.

---

## Usage

1. **Start the server** and click **Start Server**.
2. **Enable USB tethering** on the tablet: *Settings → Connections → Mobile Hotspot and Tethering → USB tethering*.
3. **Open the Android app.** It listens for the server's UDP broadcast on port 8765 and connects on its own — no IP to type in.
4. **Arrange the display** in *GNOME Settings → Displays*. The tablet shows up as a new monitor; put it where you want it and drag windows across.
5. **Enable touch** in the server window if you want the tablet to drive the pointer.

---

## Touch input

Off by default. A capability that can drive your desktop should be something you opt into, so you enable it in the server window. The choice is remembered across restarts, and can be toggled while a client is connected.

| Gesture | Effect |
|---|---|
| Tap | Left click |
| Drag | Press, move, release |
| Long press | Right click |
| Two-finger drag | Scroll |

Gesture timings come from Android's own `ViewConfiguration`, so they match every other app on the tablet and honour the device's accessibility settings.

**Every touch clicks — there is no hover.** That is inherent to driving a pointer by absolute position rather than relative motion, and it means tooltips and hover-triggered menus will not open from touch.

Input is injected through GNOME's `RemoteDesktop` API and is **scoped to the virtual display by the platform itself** — it cannot reach your laptop's own screen. That boundary is enforced by GNOME, not by care on our part.

Coordinates travel normalised to `[0,1]` in video space, so the wire format stays resolution-independent and codec-specific rendering differences (H.264 fills the panel, JPEG letterboxes) stay in the renderer where they belong. Framing is `type:u8 length:u8 payload[length]` — the explicit length makes an unrecognised message skippable rather than fatal, so new message types never desynchronise an older peer.

**Press Back on the tablet** to reveal the disconnect overlay while streaming.

Touch requires GNOME on Wayland, like the virtual display. On X11 you get video only, and the window says so rather than pretending otherwise.

### Who the server will talk to

The server serves only peers reachable over a **USB-attached network interface**, plus loopback. Wi-Fi and container bridges are refused. This matters more than it did for a view-only stream: reaching the port used to mean seeing your screen, and with input it would mean controlling the machine.

The tether subnet is **detected at runtime, not assumed**. Android hands the tethered subnet to whichever interface the USB cable created, and that varies by device, ROM and OEM — an earlier version hardcoded `192.168.42.0/24`, the AOSP default, and broke for every device handing out something else. What is actually reliable is *which interface is USB-attached*: on Linux, `/sys/class/net/<iface>/device` resolves through a path segment named `usbN` for anything enumerated over USB, and does not for PCI devices (Wi-Fi, Ethernet) or software bridges. So TethrLink enumerates USB-attached interfaces, reads whatever subnet each currently has, and trusts exactly that.

Filtering happens at accept time rather than by binding to the tether address, because that interface has no IPv4 address until tethering is actually active — and the server routinely starts first.

`TETHRLINK_TETHER_SUBNET` overrides the detection, and rejects anything implausibly broad rather than silently disabling the filter.

There is still **no authentication** on the connection. Over a cable that is a reasonable trust boundary. Any future transport that is not a cable must add pairing first.

---

## Configuration

The desktop window exposes a **codec selector** (H.264 / JPEG) and a **touch input** switch. The codec is locked while streaming, because the encoding pipeline is built once per connection — a change applies to the next one. The status line shows the codec **actually in use**, which can differ from the one requested (an X11 session always reports JPEG).

Both choices persist to `~/.config/tethrlink/settings.json`. A missing or corrupt file falls back to defaults per field, never as an all-or-nothing decision, and never stops the app from starting.

Environment variables override the UI:

| Variable | Example | Effect |
|---|---|---|
| `TETHRLINK_CODEC` | `jpeg` | Force a codec |
| `TETHRLINK_RES` | `1920x1080` | Force capture resolution instead of deriving it |
| `TETHRLINK_TOUCH` | `1` | Force touch input on or off |
| `TETHRLINK_TETHER_SUBNET` | `10.42.0.0/24` | Override tether subnet detection |

```bash
TETHRLINK_CODEC=jpeg python3 -m server.app.main
```

Encoder capability and per-device records are cached under `~/.cache/tethrlink/profiles.json`. That is a cache, not configuration — deleting it costs one slower startup and nothing else.

---

## Display geometry

The capture size is derived rather than fixed:

```
height = min(your monitor's height, the tablet's height)
width  = height × (the tablet's aspect ratio)
```

A 1920×1080 PC with a 2960×1848 tablet gives **1730×1080**. Three things fall out of that:

- **The shared edge aligns.** GNOME only lets the pointer cross where two monitors overlap vertically. Matching heights makes the whole edge crossable instead of leaving an invisible wall partway along it.
- **Nothing is stretched.** Taking the aspect ratio from the tablet makes the upscale uniform in both axes. Matching the PC's 16:9 to a 16:10 panel would stretch the picture about 11%.
- **Decode stays affordable.** The tablet's native 2960×1848 at 30 fps demands roughly 164 Mpx/s, near the practical ceiling for a single H.264 stream.

Dimensions reported by the client arrive over the wire in the HELLO handshake and are treated as untrusted: anything outside 64–16384 px is rejected and the PC's primary monitor is used instead. The final size is rounded up to what the encoder accepts — H.264 4:2:0 chroma needs even dimensions.

Override the whole thing with `TETHRLINK_RES` if you would rather trade decode headroom for sharpness.

---

## The video pipeline

**Dropping happens before encoding, never after.** Discarding a raw frame only lowers the frame rate; discarding an *encoded* frame breaks the decoder's reference chain and corrupts everything until the next keyframe. So a leaky queue sits upstream of the encoder, and everything downstream is lossless.

**The encoder is negotiated at runtime, not assumed.** Candidates are tried hardware-first, and each is accepted only after it genuinely encodes a frame at the real capture size. This matters more than it sounds: an element can exist, expose the right property, and still fail — and the rate-control modes a driver offers are read from the GPU itself, so the same element behaves differently between machines. The winning choice is cached with a fingerprint of the GStreamer install, so a driver upgrade re-probes automatically.

**A constant frame rate is manufactured downstream.** Mutter's capture is damage-driven: a display showing something static stops producing frames entirely. A `compositor` element running on its own clock supplies a steady rate at zero added latency, so motion resumes instantly instead of after a stall.

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

**No end-to-end latency figure is quoted here**, because none has been measured under controlled conditions. Earlier versions of this document cited one; it was not reproducible, and it has been withdrawn rather than repeated.

---

## Limitations

- **H.264 requires GNOME on Wayland.** The virtual display uses Mutter's private ScreenCast API. KDE, Sway and other compositors have no equivalent, and there is no cross-compositor standard for *creating* a virtual output. X11 sessions fall back to JPEG at the PC's own resolution.
- **Touch requires GNOME on Wayland** for the same class of reason — it goes through GNOME's `RemoteDesktop` API.
- **There is no hover.** Absolute-position pointer control cannot express "pointer here, not pressed".
- **JPEG sends whole frames.** No inter-frame compression, so it costs far more bandwidth and CPU. It is a fallback, not a quality option.
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
│   │   ├── input_protocol.py  # Client → server input wire format
│   │   ├── remote_input.py    # Pointer injection via GNOME RemoteDesktop
│   │   ├── link.py            # Which peers the server will serve
│   │   ├── settings.py        # Persisted user intent (codec, touch)
│   │   ├── profiles.py        # Cached encoder capability and device records
│   │   ├── metrics.py         # Stream counters
│   │   ├── preflight.py       # Startup GStreamer/encoder diagnostics
│   │   └── discovery.py       # UDP broadcast
│   └── ui/window.py           # GTK4 window
├── android/                   # Kotlin client (Compose, MediaCodec, gesture interpreter)
├── tools/
│   └── diagnose_capture_stall.py   # Isolates capture stalls from the rest of the pipeline
├── tests/                     # Python unit tests
└── docs/                      # Landing page, design specs and implementation plans
```

Run the Python tests with `./venv/bin/python -m pytest`. The Android gesture, codec and geometry logic is pure Kotlin with no Android imports, and its tests run on the plain JVM via `./gradlew test`.

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
| Audio forwarding | Planned (2.1.0) |
| Keyboard input | Planned (2.2.0) |
| Adaptive bitrate from measured link conditions | Planned |
| Real multi-touch and gestures | Planned |

---

## Author

**Prince Savsaviya** — [princesavsaviya2023.learning@gmail.com](mailto:princesavsaviya2023.learning@gmail.com)

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
