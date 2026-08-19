# Touch Input — Server Side Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a connected client drive the PC's pointer on the virtual display, and confine the server to the USB link first so that capability cannot be reached over Wi-Fi.

**Architecture:** A pure-logic layer (link confinement, message parsing, coordinate scaling) with no GStreamer or D-Bus dependency, and a thin dispatch layer that binds it to Mutter's RemoteDesktop API. The RemoteDesktop session is created only when touch is enabled, and pairs with the existing ScreenCast session so input coordinates land in the virtual display's own space.

**Tech Stack:** Python 3.12, PyGObject, D-Bus (`org.gnome.Mutter.RemoteDesktop` v1), pytest 9.

**Scope:** Server only. The Android client is a separate plan; this one is proven with a scripted test client that moves the real pointer.

## Global Constraints

- Python 3.12; run tests with `./venv/bin/python -m pytest`, NEVER bare `pytest`. 163 tests pass before this plan.
- `server/core/input_protocol.py` and `server/core/link.py` MUST NOT import `gi` or `dbus` — the parsing, scaling and address logic must be unit-testable with no GNOME session.
- **No wire-format change that breaks released clients.** They read exactly 6 magic bytes then a fixed 9-byte `(w,h,codec)` struct; extra bytes in that reply would be misparsed as frame data. The *hello* may be extended, because the server reads only 94 bytes and never reads further.
- Do not alter the video pipeline: `queue leaky=downstream` upstream of the encoder, `appsink drop=false` downstream, the compositor, the geometry derivation, or `ServerConfig.fps`.
- Valid metric counter names are exactly: `frames_encoded`, `frames_sent`, `frames_dropped`, `duplicates_suppressed`, `queue_overflows`, `keyframe_requests`. Adding input counters means extending that tuple in `metrics.py` deliberately, not inventing names at call sites.
- **Touch is off by default.** When off, no RemoteDesktop session is created at all.
- Input must never be able to break streaming. Video is the product; input is an addition.
- Never diagnose GStreamer with bare `gst-inspect-1.0` — Anaconda shadows it with a 1.14.1 build reporting zero encoders.
- See `docs/superpowers/PROJECT-KNOWLEDGE.md` for verified environment facts.

## Verified Facts

- `org.gnome.Mutter.RemoteDesktop` **Version 1**, with:
  ```
  NotifyPointerMotionAbsolute(stream:s, x:d, y:d)
  NotifyPointerButton(button:i, state:b)
  NotifyPointerAxis(dx:d, dy:d, flags:u)
  ```
- Sessions expose a **`SessionId`** string property; passing it as
  `remote-desktop-session-id` to `ScreenCast.CreateSession` binds the two.
- **Coordinates are stream-scoped**, so input lands in the virtual display's own
  coordinate space and the global desktop layout is irrelevant.
- The server currently binds `("0.0.0.0", port)` and broadcasts discovery to
  `255.255.255.255` — reachable from any interface.
- **The tethering interface has no IPv4 address until tethering is active.** At
  rest the machine shows only `lo`, `wlo1`, and docker bridges. Binding to
  `192.168.42.x` at startup would therefore fail outright.

---

### Task 1: Confine the server to the USB link

The security prerequisite. Because the tethering interface may not exist when
the server starts, this filters at accept time rather than at bind time — same
guarantee, no dependence on interface timing, and it survives the cable being
unplugged and replugged.

**Files:**
- Create: `server/core/link.py`
- Test: `tests/test_link.py`

**Interfaces:**
- Produces: `TETHER_SUBNET` (str), `is_tether_peer(addr) -> bool`,
  `tether_broadcast_address() -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_link.py`:

```python
from server.core.link import (
    TETHER_SUBNET,
    is_tether_peer,
    tether_broadcast_address,
)


def test_accepts_addresses_on_the_tethering_subnet():
    assert is_tether_peer("192.168.42.1") is True
    assert is_tether_peer("192.168.42.129") is True
    assert is_tether_peer("192.168.42.254") is True


def test_rejects_a_wifi_lan_address():
    """The case this exists for: a peer on the same Wi-Fi."""
    assert is_tether_peer("10.0.0.193") is False
    assert is_tether_peer("192.168.1.50") is False


def test_rejects_docker_bridge_addresses():
    assert is_tether_peer("172.17.0.1") is False


def test_rejects_a_neighbouring_subnet_that_merely_looks_similar():
    assert is_tether_peer("192.168.43.1") is False
    assert is_tether_peer("192.168.4.2") is False


def test_allows_loopback_so_local_tooling_still_works():
    """Test harnesses and the diagnostic tools connect over loopback."""
    assert is_tether_peer("127.0.0.1") is True


def test_rejects_garbage_rather_than_raising():
    assert is_tether_peer("") is False
    assert is_tether_peer("not-an-address") is False
    assert is_tether_peer(None) is False


def test_broadcast_address_is_scoped_to_the_tether_subnet():
    """Broadcasting to 255.255.255.255 announces the server to the whole LAN."""
    assert tether_broadcast_address() == "192.168.42.255"
    assert tether_broadcast_address() != "255.255.255.255"


def test_subnet_constant_is_the_documented_one():
    assert TETHER_SUBNET.startswith("192.168.42.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_link.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.core.link'`

- [ ] **Step 3: Write minimal implementation**

Create `server/core/link.py`:

```python
"""Which peers the server is willing to serve.

TethrLink is a wired second monitor: the only peer that should ever reach it
is the tablet on the other end of the USB cable. The server nevertheless
binds 0.0.0.0 and previously answered anyone on the network — tolerable for a
view-only stream, but not once input can drive the machine.

Filtering happens at accept time rather than by binding to the tethering
address, because that interface has no IPv4 address until tethering is
actually active. The server routinely starts first, so a bind-time approach
would simply fail, and would need re-binding every time the cable moved.

No `gi`/`dbus` import: this is address logic and should be testable anywhere.
"""

import ipaddress
import logging
from typing import Optional

log = logging.getLogger("TethrLink")

# The subnet Android hands out for USB tethering.
TETHER_SUBNET = "192.168.42.0/24"
_TETHER_NET = ipaddress.ip_network(TETHER_SUBNET)


def is_tether_peer(addr: Optional[str]) -> bool:
    """True if `addr` is reachable only over the USB cable (or is local).

    Loopback is permitted so local diagnostic tools and test harnesses keep
    working; a loopback peer is already running as the user.
    """
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    return ip in _TETHER_NET


def tether_broadcast_address() -> str:
    """Where discovery announcements go.

    Scoped to the tethering subnet: broadcasting to 255.255.255.255 announces
    the machine's hostname and port to every network it is attached to, which
    is needless exposure even when connections are filtered.
    """
    return str(_TETHER_NET.broadcast_address)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_link.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Enforce it at accept time**

In `server/core/server_core.py`, `_accept_loop` currently spawns a handler for
every accepted connection. Add the check immediately after `accept()` returns,
before the handler thread is started: if `is_tether_peer(addr[0])` is false,
log a warning naming the rejected address and close the connection.

Import `is_tether_peer` alongside the other `server.core` imports.

- [ ] **Step 6: Scope the discovery broadcast**

In `server/core/discovery.py`, replace the global `BROADCAST_ADDRESS` with
`tether_broadcast_address()`. Keep the existing `127.255.255.255` destination so
local tooling still discovers the server.

- [ ] **Step 7: Verify a non-tether peer is refused**

Start the server, then attempt a connection from the machine's own LAN address
(`10.0.0.193`) rather than loopback, and confirm it is refused and logged:

```bash
./venv/bin/python -c "
import socket
s = socket.socket(); s.settimeout(3)
s.connect(('10.0.0.193', 51137))
s.sendall(b'TLHELO' + b'\x00'*16 + b'\x00'*8 + b'probe')
print('server sent back:', s.recv(16))
"
```

Expected: the server logs a rejection naming `10.0.0.193`, and the probe reads
zero bytes (connection closed) rather than a `TLOK__` handshake.

- [ ] **Step 8: Commit**

```bash
git add server/core/link.py tests/test_link.py server/core/server_core.py server/core/discovery.py
git commit -m "fix: serve only peers reachable over the USB cable

The server bound 0.0.0.0 and broadcast discovery to the whole LAN, so anyone
on the same Wi-Fi could connect and watch the screen. That becomes remote
control of the machine once touch input exists, and there is no
authentication on the connection.

Filters at accept time rather than binding to the tethering address, because
that interface has no IPv4 until tethering is active and the server usually
starts first."
```

---

### Task 2: Input message protocol

Pure parsing and scaling. No D-Bus, no sockets — this is where the logic that
must be correct lives, so it is where the tests are.

**Files:**
- Create: `server/core/input_protocol.py`
- Test: `tests/test_input_protocol.py`

**Interfaces:**
- Produces: `MSG_POINTER_MOTION = 0x01`, `MSG_POINTER_BUTTON = 0x02`,
  `MSG_POINTER_AXIS = 0x03`; `BUTTON_CODES` mapping wire enum → evdev code;
  `InputMessage` dataclass (`type: int`, `payload: bytes`);
  `parse_messages(buffer) -> tuple[list[InputMessage], bytes]`;
  `decode_motion(payload) -> tuple[float, float] | None`;
  `decode_button(payload) -> tuple[int, bool] | None`;
  `decode_axis(payload) -> tuple[float, float] | None`;
  `to_stream_coords(nx, ny, width, height) -> tuple[float, float] | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_input_protocol.py`:

```python
import struct

from server.core.input_protocol import (
    BUTTON_CODES,
    MSG_POINTER_AXIS,
    MSG_POINTER_BUTTON,
    MSG_POINTER_MOTION,
    decode_axis,
    decode_button,
    decode_motion,
    parse_messages,
    to_stream_coords,
)


def _frame(msg_type, payload):
    return bytes([msg_type, len(payload)]) + payload


# ── framing ──────────────────────────────────────────────────────────────────

def test_parses_a_single_message():
    payload = struct.pack(">ff", 0.5, 0.25)
    msgs, rest = parse_messages(_frame(MSG_POINTER_MOTION, payload))
    assert len(msgs) == 1
    assert msgs[0].type == MSG_POINTER_MOTION
    assert msgs[0].payload == payload
    assert rest == b""


def test_parses_several_messages_in_one_buffer():
    buf = (_frame(MSG_POINTER_MOTION, struct.pack(">ff", 0.1, 0.2))
           + _frame(MSG_POINTER_BUTTON, bytes([0, 1])))
    msgs, rest = parse_messages(buf)
    assert [m.type for m in msgs] == [MSG_POINTER_MOTION, MSG_POINTER_BUTTON]
    assert rest == b""


def test_keeps_a_partial_message_for_the_next_read():
    """TCP delivers arbitrary fragments; a split message must not be lost."""
    full = _frame(MSG_POINTER_MOTION, struct.pack(">ff", 0.1, 0.2))
    msgs, rest = parse_messages(full[:5])
    assert msgs == []
    assert rest == full[:5]

    msgs, rest = parse_messages(rest + full[5:])
    assert len(msgs) == 1
    assert rest == b""


def test_empty_buffer_yields_nothing():
    assert parse_messages(b"") == ([], b"")


def test_unknown_message_type_is_skipped_not_fatal():
    """The length byte exists so an unrecognised type can be stepped over."""
    buf = (_frame(0x7F, b"\x01\x02\x03")
           + _frame(MSG_POINTER_BUTTON, bytes([0, 1])))
    msgs, rest = parse_messages(buf)
    assert [m.type for m in msgs] == [0x7F, MSG_POINTER_BUTTON]
    assert rest == b""


def test_zero_length_payload_is_handled():
    msgs, rest = parse_messages(_frame(0x40, b""))
    assert len(msgs) == 1 and msgs[0].payload == b""
    assert rest == b""


# ── payload decoding ─────────────────────────────────────────────────────────

def test_decodes_motion():
    x, y = decode_motion(struct.pack(">ff", 0.25, 0.75))
    assert abs(x - 0.25) < 1e-6
    assert abs(y - 0.75) < 1e-6


def test_motion_with_wrong_length_is_rejected():
    assert decode_motion(b"\x00\x00") is None
    assert decode_motion(b"") is None


def test_decodes_button():
    code, pressed = decode_button(bytes([0, 1]))
    assert code == BUTTON_CODES[0]
    assert pressed is True

    code, pressed = decode_button(bytes([1, 0]))
    assert code == BUTTON_CODES[1]
    assert pressed is False


def test_unknown_button_index_is_rejected():
    assert decode_button(bytes([99, 1])) is None


def test_button_with_wrong_length_is_rejected():
    assert decode_button(b"\x00") is None


def test_left_and_right_map_to_distinct_evdev_codes():
    assert BUTTON_CODES[0] != BUTTON_CODES[1]
    # evdev BTN_LEFT / BTN_RIGHT
    assert BUTTON_CODES[0] == 0x110
    assert BUTTON_CODES[1] == 0x111


def test_decodes_axis():
    dx, dy = decode_axis(struct.pack(">ff", -1.5, 2.0))
    assert abs(dx + 1.5) < 1e-6
    assert abs(dy - 2.0) < 1e-6


# ── coordinate scaling ───────────────────────────────────────────────────────

def test_scales_normalised_coordinates_to_stream_pixels():
    assert to_stream_coords(0.0, 0.0, 1730, 1080) == (0.0, 0.0)
    x, y = to_stream_coords(1.0, 1.0, 1730, 1080)
    assert abs(x - 1730) < 1e-6 and abs(y - 1080) < 1e-6
    x, y = to_stream_coords(0.5, 0.5, 1730, 1080)
    assert abs(x - 865) < 1e-6 and abs(y - 540) < 1e-6


def test_slightly_out_of_range_values_are_clamped():
    """Defence against a malformed client, not an expected path."""
    x, y = to_stream_coords(1.2, -0.3, 1730, 1080)
    assert x == 1730.0
    assert y == 0.0


def test_non_finite_coordinates_are_rejected():
    assert to_stream_coords(float("nan"), 0.5, 1730, 1080) is None
    assert to_stream_coords(0.5, float("inf"), 1730, 1080) is None


def test_zero_sized_stream_is_rejected():
    assert to_stream_coords(0.5, 0.5, 0, 1080) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_input_protocol.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.core.input_protocol'`

- [ ] **Step 3: Write minimal implementation**

Create `server/core/input_protocol.py`:

```python
"""Wire format for client → server input, and coordinate scaling.

Framing is `type:u8 length:u8 payload[length]`. The explicit length is what
makes an unrecognised message *skippable* rather than fatal, so adding message
types later never desynchronises an older peer.

Coordinates arrive normalised to [0,1] in video space. Only the client knows
its own rendering geometry — H.264 fills the panel while JPEG letterboxes —
so normalising there keeps codec-specific knowledge in the renderer and keeps
this format resolution-independent.

No `gi`/`dbus` import: this is the logic that has to be right, so it is kept
testable without a GNOME session.
"""

import math
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

MSG_POINTER_MOTION = 0x01
MSG_POINTER_BUTTON = 0x02
MSG_POINTER_AXIS = 0x03

_HEADER = 2  # type + length

# Wire enum → evdev button code. The wire carries a small index rather than
# evdev constants so a Linux implementation detail does not leak into a
# protocol an Android client also has to speak.
BUTTON_CODES = {
    0: 0x110,  # BTN_LEFT
    1: 0x111,  # BTN_RIGHT
    2: 0x112,  # BTN_MIDDLE
}


@dataclass(frozen=True)
class InputMessage:
    type: int
    payload: bytes


def parse_messages(buffer: bytes) -> Tuple[List[InputMessage], bytes]:
    """Split a byte buffer into complete messages plus any trailing remainder.

    TCP delivers arbitrary fragments, so the remainder must be carried into
    the next read rather than discarded.
    """
    messages: List[InputMessage] = []
    offset = 0
    while len(buffer) - offset >= _HEADER:
        msg_type = buffer[offset]
        length = buffer[offset + 1]
        end = offset + _HEADER + length
        if len(buffer) < end:
            break  # incomplete; wait for more bytes
        messages.append(
            InputMessage(type=msg_type, payload=buffer[offset + _HEADER:end])
        )
        offset = end
    return messages, buffer[offset:]


def decode_motion(payload: bytes) -> Optional[Tuple[float, float]]:
    if len(payload) != 8:
        return None
    return struct.unpack(">ff", payload)


def decode_button(payload: bytes) -> Optional[Tuple[int, bool]]:
    if len(payload) != 2:
        return None
    code = BUTTON_CODES.get(payload[0])
    if code is None:
        return None
    return code, bool(payload[1])


def decode_axis(payload: bytes) -> Optional[Tuple[float, float]]:
    if len(payload) != 8:
        return None
    return struct.unpack(">ff", payload)


def to_stream_coords(
    nx: float, ny: float, width: int, height: int
) -> Optional[Tuple[float, float]]:
    """Normalised [0,1] → pixel coordinates within the ScreenCast stream.

    Values are clamped rather than trusted: the client is remote input, and a
    malformed value must not send the pointer somewhere unexpected. Non-finite
    values are rejected outright, since clamping a NaN silently produces a
    plausible-looking coordinate.
    """
    if width <= 0 or height <= 0:
        return None
    if not (math.isfinite(nx) and math.isfinite(ny)):
        return None
    cx = min(1.0, max(0.0, nx))
    cy = min(1.0, max(0.0, ny))
    return cx * width, cy * height
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_input_protocol.py -v`
Expected: PASS, 17 passed

- [ ] **Step 5: Commit**

```bash
git add server/core/input_protocol.py tests/test_input_protocol.py
git commit -m "feat: add input wire format and coordinate scaling

Length-prefixed framing so unknown message types can be skipped rather than
desynchronising the stream. Coordinates arrive normalised because only the
client knows whether the video fills the panel (H.264) or is letterboxed
(JPEG); clamped and finite-checked on arrival since this is remote input."
```

---

### Task 3: RemoteDesktop session

The D-Bus binding. Thin by design — the logic lives in Task 2.

**Files:**
- Create: `server/core/remote_input.py`
- Test: manual (requires a live GNOME session)

**Interfaces:**
- Consumes: `to_stream_coords`, `BUTTON_CODES` from Task 2.
- Produces: `RemoteInput` class with `create() -> str | None` returning the
  session id, `start()`, `bind_stream(stream_path)`, `move(nx, ny)`,
  `button(code, pressed)`, `axis(dx, dy)`, `stop()`.

- [ ] **Step 1: Write the module**

Create `server/core/remote_input.py`. Requirements, in your own code:

- `create()` calls `org.gnome.Mutter.RemoteDesktop.CreateSession`, reads the
  session's **`SessionId`** property and returns it, so the caller can pass it
  as `remote-desktop-session-id` when creating the ScreenCast session. Return
  `None` on any failure — input is optional, video is not.
- `start()` calls `Start()` on the session. It must be called *after* the
  ScreenCast session exists, since the two are paired.
- `bind_stream(path)` stores the ScreenCast stream path that
  `NotifyPointerMotionAbsolute` and touch methods require.
- `move(nx, ny)` scales through `to_stream_coords` using the stream's
  dimensions and calls `NotifyPointerMotionAbsolute(stream, x, y)`.
- `button(code, pressed)` calls `NotifyPointerButton(code, pressed)`.
- `axis(dx, dy)` calls `NotifyPointerAxis(dx, dy, 0)`.
- `stop()` calls `Stop()` and is safe to call twice.
- **Every method is exception-safe.** A D-Bus failure is logged and dropped,
  never retried — a stale pointer event is worthless — and never propagates to
  the caller. This module sits on the client-connection path, and this project
  has already shipped a defect where an auxiliary failure broke the main path.
- **Motion is coalesced**: at most one dispatch per frame interval. A drag
  generates events faster than the display updates and each dispatch is a
  synchronous D-Bus round trip. Keep the most recent position, not the first.

- [ ] **Step 2: Verify against the live session**

Write a scratch script that creates a RemoteDesktop session, pairs a ScreenCast
virtual monitor with it using the returned session id, starts both, and then
moves the pointer to three known positions on the virtual display.

Expected: the pointer visibly moves on the virtual monitor and not on the
laptop's own screen — the stream-scoped API should make the latter impossible.
Report what you observed.

- [ ] **Step 3: Commit**

```bash
git add server/core/remote_input.py
git commit -m "feat: add Mutter RemoteDesktop pointer injection"
```

---

### Task 4: Negotiate input support in the handshake

**Files:**
- Modify: `server/core/server_core.py` — `_handle_client`

**Interfaces:**
- Produces: `MAGIC_OK2` constant; hello-extension parsing.

- [ ] **Step 1: Implement negotiation**

Requirements:

- Add `MAGIC_OK2 = b"TLOK2_"` beside the existing magics. It must be exactly
  **6 bytes**, because the client reads a fixed 6-byte header.
- After reading the 94-byte hello, attempt a short-timeout read for an
  appended extension block. Absence means a legacy client — this must not
  block or fail the connection, since legacy clients are the common case today.
- Reply `MAGIC_OK2` **only** when the client advertised input support *and*
  touch is enabled in config. Otherwise reply the existing `MAGIC_OK`.
- The `(width, height, codec)` struct that follows is **unchanged in both
  cases** — only the magic differs. Anything else would break the client's
  fixed-size read.

Do not invent the extension's internal layout beyond what is needed: a short
marker plus a length plus a capability byte is sufficient, and it must be
documented in the spec's §5.2 terms.

- [ ] **Step 2: Verify both paths**

Using a scripted client, confirm:
- a hello with no extension receives `TLOK__` followed by the 9-byte struct
- a hello with the extension, touch enabled, receives `TLOK2_` and the same struct
- a hello with the extension, touch disabled, receives `TLOK__`

Paste the real bytes observed.

- [ ] **Step 3: Commit**

```bash
git add server/core/server_core.py
git commit -m "feat: negotiate input support without breaking old clients"
```

---

### Task 5: Read and dispatch input

**Files:**
- Modify: `server/core/server_core.py`, `server/core/metrics.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: an input reader thread per connection.

- [ ] **Step 1: Add input counters**

In `server/core/metrics.py`, extend the `_COUNTERS` tuple with
`input_events_received` and `input_events_dropped`. Adding names anywhere else
raises `KeyError` by design, so they must be declared here.

- [ ] **Step 2: Implement the reader**

Requirements:

- A dedicated thread per connection, started only when input was negotiated.
- Accumulates bytes and calls `parse_messages`, carrying the remainder forward.
- Dispatches each known message through `RemoteInput`; counts unknown or
  undecodable messages under `input_events_dropped` and continues.
- **Rate-limits** dispatch so a flood cannot saturate D-Bus or make the desktop
  unusable.
- Exits cleanly when the socket closes or the session ends, and never leaves a
  button stuck down: on exit, release any button it believes is pressed.
- A failure anywhere in this thread must not disturb the send loop.

- [ ] **Step 3: Verify end to end with a scripted client**

Write a scratch client that completes the handshake advertising input, then
sends motion, button and axis messages. Confirm the pointer moves on the virtual
display, a click registers, and scrolling scrolls.

Then confirm the failure paths: send a truncated message, an unknown type, and a
flood, and show that streaming continues throughout.

- [ ] **Step 4: Commit**

```bash
git add server/core/server_core.py server/core/metrics.py
git commit -m "feat: read and dispatch client input"
```

---

### Task 6: Enable/disable control

**Files:**
- Modify: `server/core/server_core.py`, `server/ui/window.py`, `server/app/main.py`

- [ ] **Step 1: Add the config field**

Add `touch_enabled: bool = False` to `ServerConfig`, with a comment recording
that off is deliberate: a capability that can drive the user's desktop is opted
into. Add a `TETHRLINK_TOUCH=1` override in `main.py` beside the existing ones,
following their pattern.

- [ ] **Step 2: Gate session creation**

When `touch_enabled` is false, **no RemoteDesktop session is created** — not a
session whose events are discarded. Verify by confirming no session appears on
the bus while disabled.

- [ ] **Step 3: Add the UI toggle**

Follow the codec dropdown's existing pattern in `window.py`: a control wired
through a callback, locked while streaming, with the label making clear the
change applies to the next connection. The status line should show whether
input is active for the current session.

- [ ] **Step 4: Verify**

Confirm the toggle defaults to off, that enabling it and reconnecting activates
input, and that with it off a client advertising input still streams video and
receives `TLOK__`.

- [ ] **Step 5: Commit**

```bash
git add server/core/server_core.py server/ui/window.py server/app/main.py
git commit -m "feat: add touch enable/disable control, default off"
```

---

### Task 7: Verification

- [ ] **Step 1: Full suite** — `./venv/bin/python -m pytest -q`, expect ~188.
- [ ] **Step 2:** Confirm a LAN peer is refused while a loopback peer is served.
- [ ] **Step 3:** Confirm pointer, click, right-click and scroll all work from
      the scripted client, and that input never reaches the laptop's own screen.
- [ ] **Step 4:** Confirm video is unaffected: `dropped` and `overflows` stay at
      zero through a session with input flowing.
- [ ] **Step 5:** Record results in the verification log and commit.

---

## Out of Scope

- **The Android client** — its own plan. This one is proven with a scripted client.
- **Real multi-touch**, keyboard, audio, clipboard.
- **Authentication/pairing** — Task 1 confines reach to the cable, which is the
  2.0.0 trust boundary. Any non-cable transport must add pairing first.
- **A PC-side "input active" indicator** — recommended in the spec, deferred to
  the Android plan so it can reflect real client state.
