# TethrLink Touch Input — Design

**Date:** 2026-08-18
**Branch:** `feature/touch-and-audio`
**Target release:** 2.0.0
**Status:** Draft for review

## 1. Goal

Let the tablet drive the PC's pointer by touch, turning the extended display
from something you look at into something you use.

**Scope for 2.0.0: absolute pointer only.** A touch positions the pointer at
that spot on the virtual display and clicks. Real multi-touch — where the
compositor receives genuine touch events with slots and gestures — is a
separate capability, deliberately deferred (see §10).

Why absolute pointer first: a second monitor's value is that *every* app works
on it, and pointer input is the only mode where that is true. Desktop
applications vary wildly in touch support, so real touch would work
beautifully in some apps and not at all in others. Pointer input also needs
just one coordinate mapping and no gesture state machine, which makes it the
right foundation to prove the reverse channel on.

## 2. Verified environment facts

Established by introspecting the live session, not assumed:

- `org.gnome.Mutter.RemoteDesktop` is present, **Version 1**.
- Session methods include `NotifyPointerMotionAbsolute`, `NotifyPointerButton`,
  `NotifyPointerAxis`, `NotifyKeyboardKeycode`, `NotifyKeyboardKeysym`, and
  `NotifyTouchDown`/`Motion`/`Up`.
- Signatures that matter here:
  ```
  NotifyPointerMotionAbsolute(stream:s, x:d, y:d)
  NotifyPointerButton(button:i, state:b)
  NotifyPointerAxis(dx:d, dy:d, flags:u)
  ```
- A RemoteDesktop session exposes a **`SessionId`** string property. Passing it
  as `remote-desktop-session-id` when creating the ScreenCast session binds the
  two together.
- **Coordinates are scoped to the ScreenCast stream**, not the global desktop.
  We position within our own virtual monitor's space and never need to know
  where GNOME placed that monitor in the layout.
- `/dev/uinput` exists but is `crw------- root root`. The kernel-level
  alternative would therefore need root or a udev rule — an unacceptable
  install requirement for a released app. RemoteDesktop avoids it entirely.

## 3. Architecture

```
Android                              Linux server
───────                              ────────────
touch event
  │  normalise to [0,1] in video space
  ▼
input message ──── TCP (existing socket, reverse direction) ────►
                                     input reader thread
                                       │  scale by stream size
                                       ▼
                                     Mutter RemoteDesktop
                                       NotifyPointerMotionAbsolute(stream, x, y)
                                       NotifyPointerButton(button, state)
                                       NotifyPointerAxis(dx, dy, flags)
```

Session lifecycle, per connection:

1. Create the RemoteDesktop session; read its `SessionId`.
2. Create the ScreenCast session, passing `remote-desktop-session-id`.
3. `RecordVirtual` as today; the returned stream path is what input targets.
4. Start both sessions.
5. On disconnect, stop both.

When touch is disabled, **step 1 is skipped entirely** — no RemoteDesktop
session is created. See §7.

## 4. Coordinate mapping

The client sends **normalised `[0,1]` coordinates in video space**; the server
multiplies by the stream's pixel dimensions.

This split is deliberate, because the two codecs render differently and only
the client knows which geometry is on screen:

| Codec | Rendering | Consequence for mapping |
|---|---|---|
| H.264 | MediaCodec → full-screen `SurfaceView`, fills the view | the whole panel is video |
| JPEG | `Canvas.drawFrame`, `scale = minOf(canW/bmpW, canH/bmpH)`, centred | letterboxed — black bars are **not** part of the video |

A single wire format that carried raw pixel coordinates would be wrong on one
of these. Normalising on the client keeps codec-specific knowledge in the
renderer, keeps the protocol resolution-independent, and means a future
change to rendering does not become a protocol change.

The client must therefore compute coordinates against the **drawn video
rectangle**, not the panel: subtract the letterbox offset and divide by the
drawn size. A touch on a black bar falls outside `[0,1]` and is discarded
rather than clamped, since it is not a touch on the desktop at all.

Server side, values are clamped to `[0,1]` before scaling — defence against a
malformed or hostile client, not an expected path.

## 5. Protocol

### 5.1 The compatibility trap

The socket is already duplex, so input rides the existing connection. Two
hazards had to be designed around:

- **A new client talking to an old server would hang.** The old server never
  reads from the client after the handshake, so input bytes accumulate until
  the socket buffer fills and the client's `send` blocks.
- **Extending the `MAGIC_OK` response would break old clients.** The client
  reads exactly 6 bytes and compares (`MainActivityV2.kt`), then reads a fixed
  9-byte struct. Any extra bytes would be misparsed as frame data.

### 5.2 Negotiation

- The client appends a **versioned extension block** after the existing 94-byte
  hello. This is safe because an old server calls `recv(94)` and never reads
  further; the extra bytes sit unread in the kernel buffer and are discarded
  with the socket.
- A server that supports input and sees the extension replies with a
  **distinct 6-byte magic** (`MAGIC_OK2`), followed by the same
  `(width, height, codec)` struct as today.
- A server that does not see the extension, or has input disabled, replies with
  the existing `MAGIC_OK`.

Resulting matrix — no case hangs and no case misparses:

| Client | Server | Reply | Outcome |
|---|---|---|---|
| old | new | `MAGIC_OK` | video only, exactly as today |
| new | old | `MAGIC_OK` | client sees no input support, sends none |
| new | new, input on | `MAGIC_OK2` | input enabled |
| new | new, input off | `MAGIC_OK` | client sends none |

### 5.3 Input messages

Client → server, after the handshake:

```
type:u8  length:u8  payload[length]
```

The explicit length is what makes an unknown message type *skippable* rather
than fatal — the reader can step over anything it does not recognise, so
adding message types later never desynchronises an older peer.

| Type | Payload | Meaning |
|---|---|---|
| `0x01` | `x:f32, y:f32` | pointer motion, normalised |
| `0x02` | `button:u8, state:u8` | button press / release |
| `0x03` | `dx:f32, dy:f32` | scroll axis |

Buttons travel as a small enum (`0` left, `1` right, `2` middle) and the server
maps them to evdev codes. Putting evdev constants on the wire would leak a
Linux implementation detail into a cross-platform protocol.

## 6. Interaction model

- **Finger down** → move pointer to that position, then press left button.
  **Finger up** → release. A tap is a click; a drag is a drag.
- **Long-press** → right click. The client detects the hold and sends a right
  button press/release rather than the left.
- **Two-finger drag** → scroll, via `NotifyPointerAxis`. Gesture recognition
  lives on the client, where the raw touch events already are; the server only
  ever sees a scroll command. This is why scrolling does not require real
  multi-touch support.

Accepted consequence: **every touch clicks.** There is no hover. Tooltips and
hover-reveal menus will not trigger from touch. This is inherent to absolute
pointer mode and is the main reason real touch remains on the roadmap.

Motion events are coalesced to at most one per frame interval before dispatch.
A finger drag can generate events faster than the display updates, and each
dispatch is a synchronous D-Bus round trip; sending more than the compositor
can show wastes both.

## 7. Enabling and disabling

A toggle in the server UI controls whether input is accepted. When off, the
server does not create a RemoteDesktop session at all, rather than creating one
and discarding events. Least privilege matters here: this is a capability that
can drive the user's desktop, and the strongest guarantee is that the machinery
does not exist.

The toggle is read at connection time. Like the codec selector, changing it
applies to the next connection, and the UI must say so rather than implying a
live effect.

## 8. Security

### 8.0 The server is not USB-only today — this must change first

`server_core.py` binds `("0.0.0.0", port)` and `discovery.py` broadcasts to
`255.255.255.255`. The server is therefore reachable from **any** network
interface, not just USB tethering, and there is no authentication. This was
already observed in practice: a tablet on the LAN raced a local test connection
for the client lock.

For a view-only stream that is a privacy problem. **With input it becomes
remote control of the machine by anyone on the same Wi-Fi.**

**Required for 2.0.0: bind to the USB tethering interface only** (the
`192.168.42.0/24` subnet), and scope discovery to that interface's broadcast
address rather than the global one. This is a server-side control that a
modified client cannot bypass, and it matches what the product already claims
to be — a wired second monitor, no Wi-Fi involved.

This is listed first because it is the only mitigation here that is a genuine
security boundary rather than a cooperative one.


### 8.1 Layered controls

Input is a genuine escalation: reaching the port used to mean *seeing* the
screen; now it means *controlling* the machine. The controls below are ordered
by how much they can actually be relied on.

**Real boundaries — enforced server-side, not bypassable by a modified client:**

1. **Interface binding** (§8.0) — confines reach to a physically attached device.
2. **Off by default.** Touch is disabled unless the user turns it on.
3. **No session, not a filtered session.** When disabled, no RemoteDesktop
   session is created at all. The capability does not exist rather than existing
   and being ignored.
4. **Stream-scoped input.** `NotifyPointerMotionAbsolute` takes the ScreenCast
   stream path, so input is confined to the virtual display by the API itself.
   It cannot reach the laptop's own screen. This is a property of the platform,
   not of our code, which makes it strong.
5. **No keyboard in 2.0.0.** Pointer input alone cannot type a command, a
   password, or a URL. This meaningfully bounds the blast radius, and is worth
   weighing when keyboard support is added in 2.2.0.
6. **Clipboard stays off.** The API exposes `EnableClipboard`, `SetSelection`
   and `SelectionRead`. We deliberately do not call them, so no clipboard or
   file content crosses the link. There is no file transfer of any kind.
7. **Sessions stop on disconnect**, so a dropped connection cannot leave input
   capability alive.
8. **Input is rate-limited**, so a flood cannot saturate the D-Bus connection or
   render the desktop unusable.

**Cooperative controls — good behaviour, not boundaries.** These live in the
Android client and a modified client could ignore them. They are worth having
because they prevent *accidents*, which is the realistic risk on a personal
device:

9. **Input pauses unless the app is genuinely in front** — backgrounded,
   split-screen, or a locked screen all suspend sending. Pointer state is
   released on suspension so no button is left stuck down.

**Still absent, and deliberately so:** there is no authentication or pairing.
Once §8.0 confines the link to a cable, a peer must be physically attached,
which is a reasonable trust boundary for 2.0.0. Any future transport that is
*not* a cable must add pairing before enabling input.

**Recommended: a visible indicator on the PC** while input is enabled. GNOME
shows an indicator for screen recording; the user should likewise be able to
tell at a glance that their desktop is remotely controllable.

## 9. Error handling and testing

Failures must never take the video stream down with them:

- A malformed or truncated input message is logged and skipped; it does not
  close the connection or disturb streaming.
- If the RemoteDesktop session cannot be created, the stream continues without
  input and says so in the log. Video is the product; input is an addition.
- A D-Bus failure on dispatch is logged and dropped, not retried — a stale
  pointer event is worthless.

Testable without GNOME or hardware, and therefore properly unit tested:

- normalised → stream-pixel coordinate scaling, including clamping and
  out-of-range rejection
- message framing: parsing, unknown-type skipping, truncation
- button enum → evdev mapping
- motion coalescing

Requiring live GNOME, and therefore manually verified: session pairing,
`Notify*` dispatch, and end-to-end latency.

## 10. Out of scope

- **Real multi-touch** (`NotifyTouchDown`/`Motion`/`Up`) — a separate capability,
  not a fix to this one.
- **Keyboard input** — 2.2.0. The API is available (`NotifyKeyboardKeysym`).
- **Audio forwarding** — 2.1.0.
- **Clipboard sharing** — the API exposes it (`EnableClipboard`, `SetSelection`);
  not planned yet.
- **Authentication / pairing** — see §8.

## 11. Resolved decisions

**Touch is off by default** (2026-08-18). A capability that can drive the user's
desktop should be opted into, not out of. Turning it on is one toggle.

**Gesture timings come from the platform, not from constants** (2026-08-18).
Android already provides these, and they are the industry reference the rest of
the OS uses:

| Value | Source | Typical |
|---|---|---|
| Long-press threshold | `ViewConfiguration.getLongPressTimeout()` | 500 ms |
| Drag threshold | `ViewConfiguration.get(ctx).scaledTouchSlop` | ~8 dp |

Reading them rather than hardcoding is the better engineering choice for three
reasons: 500 ms and 8 dp are what Android, iOS, GTK and Windows all converge on,
so behaviour matches every other app on the device; `scaledTouchSlop` is
density-scaled, so a fixed pixel count would mean different physical distances
on different tablets; and users who have adjusted long-press timing for
accessibility get their preference honoured automatically.

If testing shows these need tuning for this particular interaction, they become
tunables seeded from the platform values, not replacements for them.

**The absence of hover is accepted** (2026-08-18). Every touch clicks; tooltips
and hover-reveal menus will not trigger. This is inherent to absolute-pointer
mode and is the motivation for real multi-touch later, not a defect to patch.
