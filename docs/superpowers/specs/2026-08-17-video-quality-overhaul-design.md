# TethrLink Video Pipeline Overhaul — Design

**Date:** 2026-08-17
**Branch:** `work/video-quality-review`
**Status:** Draft for review

## 1. Goal

Make TethrLink behave like a real extended monitor over USB-C: correct geometry
matching the Android device, maximum image fidelity, and latency low enough that
desktop work feels native.

**Honest latency target: 35–50 ms end-to-end.** A DisplayPort monitor adds ~0 ms.
Any encode/decode link cannot reach that. The truthful product promise is
"sub-50 ms, feels native for desktop work" — not "zero lag". Budget:

| Stage | Cost |
|---|---|
| Capture (PipeWire) | ~1 frame (16.7 ms @ 60) |
| Encode | 3–8 ms hardware / 10–20 ms software |
| USB-C transport | 1–3 ms |
| Decode (MediaCodec low-latency) | 5–10 ms |
| Render / compose | ~1 frame |

## 2. Confirmed root causes

All verified by reading code, not inferred from the README.

### 2.1 The distortion is a frame-plumbing bug, not a codec bug

`get_frame()` returns a single mutable slot
([server_core.py:555](../../../server/core/server_core.py#L555)) that the GStreamer
callback overwrites in place ([:524](../../../server/core/server_core.py#L524)).
The send loop polls that slot on its own `time.sleep` clock
([:1043-1059](../../../server/core/server_core.py#L1043)) while the encoder runs on
the pipeline's `framerate=N/1` clock. Two free-running clocks, one slot, no queue:

- **Encoder faster than send loop** → frames overwritten and never sent. A missing
  P-frame breaks the reference chain for the rest of the GOP.
- **Send loop faster than encoder** → the slot is never cleared, so the *same*
  access unit is transmitted twice, re-feeding the decoder a P-frame against
  already-consumed state.

Both occur continuously because the clocks drift. This is a latest-value-wins
design — correct for JPEG (independent frames), fatal for any inter-frame codec.

**This is why JPEG is the default and H.264 "distorts".** It is not an NVIDIA bug,
an Android bug, or a codec bug. Hardware encoding on this plumbing would still
corrupt, just less often.

### 2.2 Silent drop points

`appsink ... drop=true` ([:476](../../../server/core/server_core.py#L476)) discards
*encoded* frames under backpressure — a third drop point above the two above.

### 2.3 Error propagation window is 45× wider than intended

`key-int-max=45` ships, while the adjacent comment says `key-int-max=1`
([:466-468](../../../server/core/server_core.py#L466)). Any lost frame corrupts
output for up to 45 frames.

### 2.4 No rate control; the bitrate setting is dead code

`quantizer=1` is fixed near-lossless CQ (the comment claims 15). Frame sizes are
enormous and wildly variable, which *causes* the backpressure in 2.1/2.2. The
`bitrate` config value is parsed and stored but never referenced in the pipeline
string — the UI control does nothing.

### 2.5 Geometry is wrong end-to-end

The device reports real window bounds
([MainActivityV2.kt:390](../../../android/app/src/main/java/com/tethrlink/MainActivityV2.kt#L390))
in the handshake. The server parses them
([:881](../../../server/core/server_core.py#L881)) then explicitly discards them:
*"used by UI canvas, not for resolution"*
([:895](../../../server/core/server_core.py#L895)). Resolution comes from the PC's
primary monitor ([:907](../../../server/core/server_core.py#L907)) and H.264 is then
hard-capped to 1280 px wide with height derived from the **PC's** aspect ratio.

Additionally `MutterVirtualDisplay.__init__` accepts `width, height`
([:239](../../../server/core/server_core.py#L239)) but `RecordVirtual` is called with
only `cursor-mode` ([:284](../../../server/core/server_core.py#L284)) — the stored
size is never sent to Mutter; the effective size falls out of downstream caps
negotiation.

Net effect on a 1080×2400 phone: a 16:9 PC-shaped desktop, downscaled to 1280 wide,
upscaled again on device. Wrong aspect ratio plus two resampling passes. **This is
the single largest image-quality loss in the product.**

### 2.6 Software-only encoding, hardcoded

`x264enc` is hardcoded. Probing the actual runtime (GStreamer 1.24.2) shows
available hardware encoders go unused.

### 2.7 One-way protocol

Frames flow server→client only. The client cannot request a keyframe when its
decoder loses sync, cannot report queue depth, and cannot advertise its decoder
capabilities. There is no mechanism to recover from corruption.

## 3. Decisions

| Decision | Rationale |
|---|---|
| **Keep H.264; do not adopt H.265** | Bandwidth is not the constraint (USB-C gives 100–300 Mbit/s; 1080p60 needs 30–50). H.265 costs latency, has far narrower encoder coverage (Intel exposed *zero* HEVC encoders on the dev machine vs two for H.264), requires rewriting the fragile NAL parsing (2-byte headers, VPS/SPS/PPS), is not CDD-mandatory on Android, and carries a messier patent position for a Play Store product. |
| **Vendor-neutral encoder selection** | No hardcoded vendor element. Runtime probe + fallback chain. Plugin presence ≠ working encoder. |
| **GNOME-first capture; preserve backend seam** | No cross-compositor standard exists for *creating* a virtual output. EVDI would be universal but costs a DKMS kernel module (Secure Boot, kernel upgrades, root install) — support burden outweighs coverage at this stage. GNOME covers Ubuntu/Fedora/Debian/Pop!_OS defaults; existing X11 path covers more. |
| ~~**Match device geometry exactly; zero resampling**~~ — **superseded 2026-08-18, see §12** | Largest available quality win. |
| **Logical-size-matched scaling (not pixel-matched)** — *confirmed* | Pixel-matching a 1080×2400 panel at scale 1 makes desktop UI physically microscopic on a phone. Real HiDPI monitors use a scale factor. Derive scale from reported DPI so content renders at full pixel density but at a readable physical size. |
| **Codec-agnostic negotiation layer** | H.265/AV1 slot in later without rework if 4K or Wi-Fi transport ever makes bandwidth the constraint. Build the seam, not the feature. |

## 4. Core architectural principle

> **Drop before encoding, never after.**

Dropping a *raw* frame is safe — it lowers framerate and nothing else. Dropping an
*encoded* inter-frame corrupts every subsequent frame until the next keyframe.

The current pipeline does the opposite. The fix:

```
pipewiresrc
  ! queue leaky=downstream max-size-buffers=2   ← drops here are SAFE
  ! videorate ! videoconvert
  ! video/x-raw,format=NV12,width=W,height=H,framerate=F/1
  ! <negotiated encoder>
  ! h264parse config-interval=-1
  ! appsink drop=false                          ← lossless from here on
```

Downstream of the encoder, a **bounded FIFO queue** (2–4 frames) replaces the
single slot. The queue stays small deliberately: a deep buffer would hide drops at
the cost of latency, defeating the product goal.

On queue overflow the system does **not** drop a single frame in isolation. It
drains the queue and issues a `GstForceKeyUnit` event — a controlled resync
instead of silent corruption.

JPEG retains latest-value-wins behaviour, which is correct for it. Branch by codec.

## 5. Protocol evolution

The handshake is a fixed 94-byte layout (`6+16+8+64`) with no version field, and
there are **released clients in the wild**. Breaking it would break existing users.

**Approach: append-only extension block.** The legacy server calls
`conn.recv(94)` and never reads further from the client, so extra bytes sit
harmlessly in the kernel buffer. Therefore:

- New clients send the existing 94-byte hello **plus** a versioned, length-prefixed
  extension block.
- Legacy servers ignore it entirely (forward compatible).
- New servers read the 94 bytes, then attempt a short-timeout read for the
  extension; absence means a legacy client (backward compatible).

Extension payload carries: `density_dpi`, `refresh_rate`, decoder max
width/height, supported profiles/levels, and codec preferences.

**Reverse channel:** the socket is already duplex. Add a server-side reader thread
and client-side writer for control messages, same length-prefix framing:

- `KEYFRAME_REQUEST` — client's decoder lost sync
- `STATS` — queue depth / render latency (enables real adaptive bitrate later)
- `GEOMETRY_CHANGED` — device rotated or window resized

## 6. Encoder abstraction

Normalized config → per-encoder adapter. Native property names and **units** differ
(`openh264enc` takes bits/s where others take kbit/s — a classic footgun).

```
EncoderConfig(bitrate_kbps, gop_length, low_latency=True, bframes=0, profile)
```

Probe order, each verified by building `videotestsrc num-buffers=1 ! videoconvert
! <enc> ! fakesink` and driving it to `PLAYING` with a timeout, then cached:

1. `nvh264enc` (NVIDIA)
2. `vah264enc` / `vah264lpenc` (modern VA — Intel/AMD)
3. `vaapih264enc` (legacy VA)
4. `qsvh264enc` (Intel QSV)
5. `v4l2h264enc` (ARM/embedded)
6. `x264enc` → `openh264enc` (software floor)

Verification must be a real instantiation: NVENC registers without a loaded driver,
VAAPI registers without render-node permission, and on the dev machine
`vah264enc` was absent while `vah264lpenc` was present — element names cannot be
assumed even within one vendor.

**B-frames must be explicitly zero on every encoder** (defaults differ) — they add
reordering latency.

Rate control moves to CBR/VBR targeting a bitrate derived from
`width × height × fps × bits-per-pixel` (~0.1 bpp), clamped to a sane range. The
existing `bitrate` config finally gets wired in.

## 7. Phased plan

Ordering is by risk, not convenience. Each phase is independently shippable and
verifiable.

### Phase 0 — Instrumentation and versioning
Without measurement none of the later fixes can be proven.
- Counters: frames encoded / sent / dropped / duplicated, queue overflows,
  keyframe requests, end-to-end latency.
- **Startup preflight**: log the resolved GStreamer version, plugin search path
  and the encoders actually available to the running process. This is the check
  that distinguishes a genuine packaging failure from a shadowed `PATH`, and it
  gives support a single line to ask users for.
- Handshake extension block + reverse control channel scaffolding.

**Acceptance:** drop and duplicate counts are observable on the current build, and
demonstrably non-zero on the H.264 path (confirming §2.1 empirically); preflight
output identifies the active GStreamer and encoder set.

### Phase 1 — Correctness (the FIFO fix)
- Bounded lossless FIFO downstream of encoder; single-slot removed for H.264.
- `leaky=downstream` queue upstream of encoder; `drop=false` on appsink.
- Overflow → drain + force keyframe.
- Remove `time.sleep` pacing; the send loop blocks on the queue (one clock).

**Acceptance:** zero duplicate access units and zero encoded-frame drops over a
10-minute run; no visible error propagation.

### Phase 2 — Geometry
- Use handshake dimensions for the virtual monitor.
- Pass size to Mutter via caps filter on `pipewiresrc` output.
- Delete `videoscale` and the `h264_width=1280` cap.
- Handle encoder alignment (round to even/16, rely on SPS cropping).
- Apply DPI-derived scale factor.
- Clamp to client-reported decoder limits.

**Acceptance:** capture, encode, decode and render resolutions are identical and
equal to the device's window bounds; no resampling anywhere in the path.

### Phase 3 — Hardware encoding
- Encoder abstraction, probe chain, per-encoder adapters.
- Replace `quantizer=1` with real rate control; wire up `bitrate`.
- B-frames off, low-latency mode per encoder.

**Acceptance:** hardware encoder selected and logged on NVIDIA, Intel, AMD and
software-only machines; CPU usage drops materially versus x264; measured latency
within the §1 budget.

### Phase 4 — Resilience
- `KEYFRAME_REQUEST` handling → immediate IDR.
- Shorter GOP and/or intra-refresh where supported.

**Acceptance:** induced frame loss recovers within ~1 frame instead of up to 45.

### Phase 5 — Packaging and rollout

**Confirmed defect — the `.deb` under-declares its dependencies.** Current
`debian_build/DEBIAN/control` declares only `gstreamer1.0-pipewire`, which pulls
`pipewire, libc6, libglib2.0-0t64, libgstreamer-plugins-base1.0-0,
libgstreamer1.0-0` — and therefore does *not* satisfy:

| Missing | Provides | Impact |
|---|---|---|
| `gir1.2-gstreamer-1.0` | `Gst-1.0.typelib` | `gi.require_version('Gst','1.0')` fails — no streaming at all |
| `gstreamer1.0-plugins-good` | `jpegenc` | The default codec's encoder |
| `gstreamer1.0-plugins-base` | `videoconvert`, `videoscale` | Every pipeline breaks |
| `gstreamer1.0-plugins-ugly` | `x264enc` | H.264 path |
| `gstreamer1.0-plugins-bad`, `gstreamer1.0-vaapi` | hardware encoders | Phase 3 |

Note `libgstreamer-plugins-base1.0-0` (library) is a *different package* from
`gstreamer1.0-plugins-base` (plugin elements); only the former is pulled in.

This currently passes unnoticed because a standard Ubuntu desktop already carries
most of these; it would fail on minimal installs and derivatives.

Corrected `Depends` adds `gir1.2-gstreamer-1.0`, `gstreamer1.0-plugins-base`,
`gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-ugly`, with
`Recommends: gstreamer1.0-plugins-bad, gstreamer1.0-vaapi` (hardware acceleration
is optional — software fallback must still work).

**Snap:** `stage-packages` lists only `python3`, `python3-gi`, `python3-gi-cairo`
— no GStreamer staged at all, relying entirely on the `gnome` extension's platform
snap. Verify with `snap connections tethrlink` on a real install, especially
whether `/dev/dri` is reachable for VAAPI/NVENC under strict confinement.

- Surface the selected encoder in UI/logs for support diagnosis.
- Keep JPEG as the released default until H.264 is validated; promote H.264 to
  default only after the device matrix passes.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Environment supplies an unexpected GStreamer → silent software fallback, or missing plugins → hard failure | Startup preflight logging the resolved GStreamer version, plugin path and available encoders (Phase 0). Note: a shadowing toolchain on `PATH` (e.g. Anaconda ships GStreamer 1.14.1 with no encoders) can make CLI diagnostics report a different GStreamer than the app actually loads — always verify via the app's own runtime, not `gst-inspect-1.0` |
| Released clients break on protocol change | Append-only extension; both directions tested against legacy |
| Odd Android resolutions trip fussy hardware encoders | Alignment rounding + SPS cropping; verify on real devices |
| Tall portrait modes exceed decoder limits | Client reports `VideoCapabilities`; server clamps |
| Per-SoC MediaCodec quirks (an MTK `C2_CORRUPTED` bug was already fixed once) | Device test matrix before promoting H.264 to default |

## 9. Out of scope

- H.265 / AV1 (revisit if 4K60 or Wi-Fi transport lands)
- KDE / wlroots / EVDI capture backends (seam preserved, not built)
- Automatic adaptive bitrate (Phase 0 `STATS` enables it later)
- Mirror-only mode for non-GNOME compositors
- Audio

## 10. Open decisions

None outstanding.

### Resolved

- **Scaling philosophy** — logical-size-matched, confirmed 2026-08-17. The virtual
  monitor is created at the device's pixel dimensions, with a Mutter scale factor
  derived from the device's reported `density_dpi`. Content therefore renders at
  full pixel density (sharp) while occupying a readable physical size. A 1080×2400
  panel at ~2.5× yields roughly a 432×960 logical desktop.

- **Rotation handling** — renegotiate live, confirmed 2026-08-17. See §11.

## 11. Live geometry renegotiation

Rotating the device must not require a reconnect. It is *not* seamless, however:
the virtual monitor, the encoder and the decoder all have to be reconfigured, so
the honest target is **an automatic sub-second transition**, not a glitch-free one.

Sequence:

1. Client observes a configuration change and re-reads its window bounds.
2. Client **debounces ~300 ms** — rotation animations emit several config changes,
   and acting on each would thrash the pipeline.
3. Client sends `GEOMETRY_CHANGED{width, height}` on the reverse control channel.
4. Server pauses the send loop and drains the FIFO.
5. Server rebuilds the virtual monitor and GStreamer pipeline at the new
   dimensions. Mutter's `RecordVirtual` has no live-resize operation, so this is a
   teardown and recreate.
6. Server sends `STREAM_RESET{width, height, codec}` before resuming frames.
7. Client flushes and reconfigures `MediaCodec` for the new format, then resumes.

The explicit `STREAM_RESET` matters: relying on the decoder to adapt silently to
new in-band SPS/PPS works on some MediaCodec implementations and not others.
Signalling the change out-of-band and reconfiguring deliberately is the portable
path across SoCs.

Because step 5 recreates the pipeline, the first frame after a rotation is
necessarily an IDR — no additional keyframe request is needed.

## 12. Edge-aligned geometry — supersedes "match device exactly" (2026-08-18)

Hardware testing overturned the §3 decision to match the device's dimensions
exactly. Matching a 2960×1848 tablet produced three problems at once:

1. **Decode cost.** ~164 Mpx/s at 30 fps sits near the practical ceiling for a
   single H.264 stream. The tablet fell behind, and because the pipeline never
   drops an encoded frame, latency accumulated rather than degrading.
2. **Broken edge traversal.** GNOME shares only the vertically overlapping part
   of an edge between two monitors. An 1848-tall virtual display beside a
   1080-tall laptop panel left 768px of that edge untraversable — the pointer
   hit an invisible wall.
3. **Unreadable UI.** 2960×1848 on a 14.6" panel is ~239 PPI, so a 1:1 desktop
   is physically tiny. Fixing that needs a Mutter scale factor, which rewrites
   the user's display layout and remains out of scope.

Matching the *host* instead fixes all three, but the host is 16:9 while the
tablet is 16:10, and the H.264 path renders through MediaCodec into a
full-screen SurfaceView with no aspect correction — so a 16:9 stream is
stretched ~11% vertically. Confirmed visually on hardware.

**The rule adopted instead — take the height from the host, the aspect from
the device:**

```
target_height = min(monitor_height, device_height)
target_width  = round(target_height × (device_width / device_height))
```

- Height from the host makes the shared edge fully traversable.
- Aspect from the device means nothing is stretched, and the upscale is
  uniform in both axes instead of 1.54× horizontally against 1.71× vertically.
- `min(...)` never requests more pixels than the panel physically has, so a 4K
  host driving this tablet still tops out at the tablet's own height.

Worked examples: host 1920×1080 + device 2960×1848 → **1730×1080** (the
configuration the user tested and preferred). Host 3840×2160 + the same device
→ 2960×1848. Host 1920×1080 + a 1280×800 device → 1280×800.

The aspect must be computed from the *portrait-normalised* device dimensions,
since the client locks to landscape while streaming.

Explicit configuration (including `TETHRLINK_RES`) still overrides the
derivation entirely.
