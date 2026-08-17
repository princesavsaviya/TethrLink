# Frame Delivery Correctness — Hardware Verification Log

**Date:** 2026-08-17
**Branch:** `work/video-quality-review`
**Machine:** HP OMEN Laptop 15-ek0xxx, GNOME/Wayland, Intel UHD + NVIDIA GTX 1650 Ti
**Client:** Samsung SM-X920 tablet (reports 2960×1848), USB-C tethering
**Unit tests:** 64 passing

## Preflight

```
GStreamer version : GStreamer 1.24.2
Plugin path       : /usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstcoreelements.so
H.264 encoders    : nvh264enc, vah264lpenc, vaapih264enc, x264enc, openh264enc
```

Confirms the app loads system GStreamer 1.24.2, not the Anaconda-shadowed 1.14.1
that `gst-inspect-1.0` reports on this machine. Run is valid.

## H.264 session — PASS

90 seconds continuous, `TETHRLINK_CODEC=h264`, encoding 1280×720 (the
`h264_width` cap), `x264enc` software encoder.

| Counter | Start of stream | End (t+90s) | Verdict |
|---|---|---|---|
| `encoded` | 20 | 3311 | — |
| `sent` | 8 | 3299 | — |
| `dropped` | 12 | **12 (frozen)** | PASS |
| `overflows` | 3 | **3 (frozen)** | PASS |
| `keyframe_reqs` | 4 | **4 (frozen)** | PASS |
| `dup_suppressed` | 0 | **0** | PASS — confirms H.264 path |

**Accounting is exact: 3311 encoded − 12 dropped = 3299 sent.** Every encoded
frame after startup was either accounted as dropped or delivered, with nothing
unexplained. This is the direct evidence that the FIFO is lossless.

`dropped`, `overflows` and `keyframe_reqs` all stopped moving once streaming was
established and never moved again for 90 seconds. Sustained 27–54 fps with a
112 fps peak during a burst.

### Startup transient (accepted, not a defect)

All 12 drops, 3 overflows and 4 keyframe requests occurred in the ~1.3 s window
between `pipeline playing` (14:22:15.017) and shortly after `Streaming →`
(14:22:15.518). The encoder runs as soon as the pipeline reaches PLAYING, while
the send loop is still completing the handshake and dimension probe, so the
4-deep queue fills and correctly sheds the backlog with a forced keyframe.

This is the designed behaviour working: a bounded resync rather than silent
corruption. It costs a fraction of a second of startup latency. Removing it
would mean deferring pipeline start until the client is ready — a candidate
improvement, not a correctness problem.

## JPEG regression session — PASS (after two fixes)

The JPEG path exposed two regressions that unit tests could not have found,
both caused by `LatestFrameSlot` returning `None` when no new frame has arrived.
Before this branch, `get_frame()` returned the same stale frame indefinitely.

**Regression 1 — idle starvation.** The JPEG pipeline has no `videorate`, so a
static virtual display produces no damage, no samples, and therefore nothing to
send. The old duplicate retransmission had been acting as an accidental
application-level heartbeat. The Android client's `soTimeout = 3000` fired, it
declared the link dead and reconnected, and the server — still holding
`_client_lock` — answered `MAGIC_BUSY`, producing an endless reconnect storm
("Incoming: SM-X920" every ~2 s). Fixed by an explicit 1.0 s idle keepalive
(`KEEPALIVE_INTERVAL_S`): resend the last JPEG frame, or force a fresh IDR on
H.264 where retransmitting an access unit would corrupt decoder state.

**Regression 2 — handshake ate the only frame.** `resolve_stream_dimensions`
polled `capture.get_frame()`, which *consumes* from the slot. Its docstring
claimed this "costs at most one redundant frame that would have been superseded
anyway" — false on a static display, where nothing supersedes it. With the only
frame gone and the keepalive gated on `last_sent_payload is not None`, the
session sent literally nothing (`sent=0`) and reconnect-stormed again. Fixed by
adding a non-consuming `LatestFrameSlot.peek()` / `peek_frame()` and using it for
dimension discovery, mirroring what the H.264 path already did correctly.

Post-fix JPEG run: reconnect storm gone, single clean session, `sent` climbing
steadily, `dropped` flat at 11–12 (harmless on independent frames),
`dup_suppressed` non-zero — proving identical frames are no longer retransmitted
at frame rate.

## Performance note — not an apples-to-apples comparison

JPEG ran ~20 fps at 1920×1080; H.264 ran ~35 fps at 1280×720. In pixels per
second that is roughly 41 Mpx/s versus 32 Mpx/s, so the fps gain is partly just
the lower resolution from the `h264_width=1280` cap, not pure codec efficiency.
H.264's real advantage here is bandwidth, since inter-frame prediction sends a
fraction of the bytes.

The fps ceiling is almost certainly `x264enc` software encoding at
`speed-preset=medium quantizer=1` being CPU-bound — precisely what Phase 3
(hardware encoder negotiation) addresses. Removing the 1280 cap is Phase 2.

## Outstanding

- Overflow recovery under deliberate CPU stress was not exercised; the session
  never overflowed after startup, so the freeze-instead-of-smear behaviour is
  unconfirmed on hardware.
- Mid-stream display reconfiguration ended the session cleanly with no
  `MAGIC_BUSY` lock-up, but was not tested repeatedly.
- **Visual confirmation that the error-propagation artifact is gone is still
  pending** — the metrics prove no frames were lost, which removes the
  mechanism, but only a human can confirm the picture looks correct.

## Release blocker (pre-existing, out of this branch's scope)

`build_deb.sh` never copies `server/` into the `.deb` payload; it packages a
hand-copied snapshot in `debian_build/` that has drifted from source, and
`frame_queue.py`, `metrics.py` and `preflight.py` are absent from it entirely.
None of this work would reach users. The `.deb` also under-declares its
dependencies — see spec §7 Phase 5.
