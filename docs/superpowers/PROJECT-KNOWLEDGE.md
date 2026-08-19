# TethrLink — Project Knowledge

Durable reference for facts that were **expensive to establish empirically** and
should not be re-derived, plus decisions and their reasoning. Read this before
starting work on the streaming pipeline.

Last updated: 2026-08-18

---

## Current state

| | |
|---|---|
| `main` | untouched, publication only |
| `develop` | v1.1.0 merged, **not yet pushed** (no git credentials in the agent environment) |
| `feature/touch-and-audio` | current branch; touch spec committed, no implementation yet |
| Tags | `v1.1.0` (video pipeline), `best-quality-h264` (dev marker) |
| Tests | 163, run with `./venv/bin/python -m pytest` |

Version plan (standard semver): **2.0.0** touch, **2.1.0** audio, **2.2.0**
keyboard. `2.0.x` is reserved for fixes.

Server version strings live in `setup.py`, `snap/snapcraft.yaml`,
`debian_build/DEBIAN/control`. Android `versionName` is separate;
`versionCode` is just a monotonic Play Console upload counter.

---

## Environment traps

**Anaconda shadows GStreamer.** `/home/prince/anaconda3/bin/gst-inspect-1.0` is
version 1.14.1 with **zero** H.264 encoders registered, and it comes first on
`PATH`. This sent an entire investigation down a false path once. Always use
`/usr/bin/gst-inspect-1.0`, or probe through PyGObject. The app itself is
unaffected: the venv is `--system-site-packages`, `gi` resolves to
`/usr/lib/python3/dist-packages`, and GStreamer is **1.24.2**.

**Never run bare `pytest`** — use `./venv/bin/python -m pytest`.

**Environment:** GNOME Shell 46, Wayland, NVIDIA GTX 1650 Ti + Intel UHD
(Comet Lake). Test device: Samsung SM-X920, reports 2960×1848.

---

## Mutter / capture

- `org.gnome.Mutter.ScreenCast` **Version 4**.
- **`RecordVirtual(a{sv}) → o` takes no size arguments.** The virtual monitor's
  size comes entirely from **PipeWire caps negotiation** — the caps filter on
  `pipewiresrc` is what sets it. Verified: requesting 2960×1848 produced a
  monitor at exactly that size. Without a caps filter, nothing negotiates.
- **Capture is strictly damage-driven.** A static display stops delivering
  frames entirely — verified in isolation with
  `tools/diagnose_capture_stall.py` (capture only, no encoder/socket/device):
  21 frames, then zero.
- **No caps request changes that.** Asking for a fixed `framerate=30/1` instead
  of `max-framerate=30/1` still yields zero frames when idle. A constant rate
  must be manufactured downstream.
- **`compositor` solves it at zero latency cost.** It aggregates on its own
  clock and keeps emitting when input goes quiet. Measured on an idle display:

  | `latency` | frames/sec |
  |---|---|
  | 0 ms | 30, 30, 30, 30, 30 |
  | 33 ms | 30, 30, 30, 30, 30 |

  `latency=0` is used, because pipeline buffering was deliberately cut to
  ~133 ms and giving any back would undo that.
  **Cost:** an idle session now holds ~0.4 core, versus ~0 before. That is
  inherent to producing 30 fps, not a compositor inefficiency.

- `org.gnome.Mutter.RemoteDesktop` **Version 1** is available for input
  injection. Coordinates are **scoped to the ScreenCast stream**, so the global
  desktop layout is never needed. Pairs via the session's `SessionId` property
  passed as `remote-desktop-session-id` to `CreateSession`.
- **Once paired, the ScreenCast session must not be started or stopped
  directly.** `Start()`/`Stop()` on it then raises *"Must be started/stopped
  from remote desktop session"* — only the RemoteDesktop session's
  `Start()`/`Stop()` drives both. `MutterVirtualDisplay.setup()` calls
  `session.Start()` today, so it needs a paired-mode path.
- The ScreenCast **Stream object exposes no size property** — only an opaque
  `mapping-id`. Stream dimensions must be carried by the caller, which is why
  `RemoteInput` takes width/height at construction rather than reading them
  back.
- `Stop()` on a session that was never started raises
  `org.freedesktop.DBus.Error.Failed: Session not started`. Handle it.
- `/dev/uinput` is `crw------- root root` — the kernel-level input alternative
  would need root or a udev rule. RemoteDesktop avoids that entirely.

---

## Encoders — names differ per vendor, and per GPU

Guessing property names fails at pipeline-parse time or silently. All of the
below were verified with `set_property` against real elements.

| Element | Rate-control property | Nicknames actually accepted |
|---|---|---|
| `nvh264enc` | **`rc-mode`** | `cbr`, `vbr`, `cbr-ld-hq`, `vbr-hq`, **`constqp`** — there is **no `cqp`** |
| `x264enc` | **`pass`** | `cbr`, `qual`, `quant` |
| `vaapih264enc` | `rate-control` | `cbr`, `vbr`, `cqp` — but **fails at runtime** here (not-negotiated) |
| `vah264lpenc` | `rate-control` | **`cqp` only** on this Intel GPU |
| `openh264enc` | `rate-control` | none of cbr/vbr/cqp; takes bitrate in **bits/s**, not kbit/s |

**The enum contents are probed from the render node**, so the same element
differs between machines — `vah264lpenc`'s enum type is literally named
`GstVaEncoderRateControl_H264_LP_renderD128`. This is why the encoder layer
probes the enum *and* verifies by actually encoding, at the real capture size.
Element presence proves nothing.

`openh264enc` has **no B-frame property at all** — omitting it there is correct,
not an oversight.

### Measured throughput (120 frames, `videotestsrc`)

| Encoder | 1280×720 | 1920×1080 | 2960×1848 |
|---|---|---|---|
| `x264enc` medium qp1 (the old default) | 76 | 30 | **12.8** |
| `x264enc` ultrafast CBR | 141 | 71 | 39 |
| `nvh264enc` CBR | 320 | 196 | **93** |

Encoder selection costs ~2.06 s cold, ~0.44 s from the on-disk cache. The
residual is deliberate re-verification.

---

## Transport

- **The tether subnet is NOT 192.168.42.0/24 on this machine.** The README
  claims it is (the AOSP default); the real interface is `enx7a6c143e84e9` at
  **10.125.32.0/24**. Hardcoding the README's value broke connection and
  discovery outright. The server now derives the link at runtime from
  **USB-attached interfaces** — `/sys/class/net/<iface>/device` resolves to a
  path containing `usb`. That is the product's real intent ("serve what is on
  the other end of the cable") and works on any ROM. Wi-Fi and docker are
  still refused because they are not USB.
- The tablet enumerates on **USB 2.0 High Speed: 480 Mbit/s nominal**,
  ~200–300 Mbit/s realistic. It is *not* on the 10 Gbit/s controller.
- **Raw video is not viable:** NV12 at 2960×1848/30 is ~1.9 Gbit/s, several
  times the link. H.264 at 1730×1080/30 targets ~16 Mbit/s, about 6%.
- The Android client sets **`soTimeout = 3000 ms`**. Three seconds of silence
  and it declares the link dead and reconnect-storms. Any change that can leave
  the stream quiet must account for this.
- The client reads **exactly 6 magic bytes**, compares against `MAGIC_OK`, then
  reads a fixed 9-byte `(w, h, codec)` struct. **Extending that reply breaks old
  clients** — extra bytes get misparsed as frame data. The hello *can* be
  extended, because the server reads only 94 bytes and never reads further.

---

## Architectural principles

**Drop before encoding, never after.** Discarding a raw frame only lowers the
frame rate. Discarding an *encoded* inter-frame breaks the decoder's reference
chain and corrupts everything until the next keyframe. Hence
`queue leaky=downstream` upstream of the encoder and `appsink drop=false`
downstream. Do not "optimise" these.

**Geometry is derived, not fixed:**
```
height = min(monitor_height, device_height)
width  = height × (device_width / device_height)
```
1920×1080 host + 2960×1848 device → **1730×1080**. Height from the host makes
the shared edge fully traversable in GNOME; aspect from the device means no
stretch and a uniform upscale; `min()` never asks for more pixels than the panel
has. The aspect must be computed from **portrait-normalised** dimensions, since
the client locks to landscape while streaming.

**A cache must never break streaming.** Every read of
`~/.cache/tethrlink/profiles.json` is best-effort and self-healing: malformed
records fall through to a re-probe and get overwritten. A corrupt entry costs
one slow start, never a broken session.

**Metric names are fixed:** `frames_encoded`, `frames_sent`, `frames_dropped`,
`duplicates_suppressed`, `queue_overflows`, `keyframe_requests`. `incr()` raises
`KeyError` on anything else.

---

## Bugs fixed here worth not reintroducing

- **The original corruption** was a single mutable frame slot shared between the
  GStreamer callback and the socket loop, with two free-running clocks. It both
  dropped and duplicated encoded frames. This is why the FIFO exists.
- **`pad.send_event()` silently drops upstream events on a sink pad.** Use
  `push_event()`. The force-keyframe path was a no-op while its metric claimed
  success.
- **Idle starvation:** the old duplicate-frame retransmission was accidentally
  acting as an application-level heartbeat. Suppressing it starved the client's
  3 s timeout and caused reconnect storms.
- **Retransmitting a cached IDR is safe; retransmitting a delta frame is not.**
  IDR means instantaneous decoder refresh. After an idle retransmission the
  keyframe gate must be engaged so stale deltas are rejected.
- **The connecting client can report portrait dimensions** — it reads window
  bounds *before* `lockToLandscape()`, and the manifest is `fullUser`. Untrusted
  input; normalise it.

---

## Known open issues

| Issue | Impact |
|---|---|
| **`build_deb.sh` never copies `server/`** into the package; `debian_build/` is a stale hand-copied snapshot | **Blocks any release** — no server change reaches users |
| `.deb` omits `gir1.2-gstreamer-1.0`, `gstreamer1.0-plugins-{base,good,ugly}` | Clean install cannot import GStreamer at all |
| Segfault at process exit after an H.264 session | Teardown ordering, NVENC/CUDA vs interpreter shutdown. Post-session only |
| Idle session holds ~0.4 core | Cost of the constant frame rate |
| `devices` map in the profile store grows unbounded | Whole file rewritten per connection |
| No authentication on the connection | Tolerable for view-only; a bigger deal once touch lands |

---

## Where things live

- Specs: `docs/superpowers/specs/`
- Plans: `docs/superpowers/plans/`
- Verification log: `docs/superpowers/plans/2026-08-17-verification-log.md`
- Capture-stall isolation tool: `tools/diagnose_capture_stall.py`

Env overrides for testing: `TETHRLINK_CODEC=jpeg|h264`,
`TETHRLINK_RES=1920x1080`.
