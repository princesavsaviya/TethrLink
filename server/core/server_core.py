"""
TethrLink Server Core — v0.9.5
Importable server logic. No argparse, no __main__.
Used by both app.py (GUI) and the CLI shim (tethrlink_server.py).
"""

import logging
import os
import select
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import dbus
import dbus.mainloop.glib
import gi
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

try:
    gi.require_version("GstVideo", "1.0")
    from gi.repository import GstVideo
except (ValueError, ImportError):
    GstVideo = None

from .discovery import DiscoveryBroadcaster
from server.core.link import is_tether_peer
from server.core.profiles import ProfileStore
from server.core.preflight import (
    H264_ENCODER_CANDIDATES,
    format_preflight,
    probe_encoders,
)
from server.core.frame_queue import EncodedFrameQueue, LatestFrameSlot
from server.core.geometry import is_plausible_size, resolve_capture_size
from server.core.metrics import StreamMetrics
from server.core.remote_input import (
    RemoteInput,
    RD_BUS,
    RD_PATH,
    RD_SES_IF,
    dispose_remote_desktop_session,
)
from server.core.input_protocol import (
    InputMessage,
    MSG_POINTER_AXIS,
    MSG_POINTER_BUTTON,
    MSG_POINTER_MOTION,
    decode_axis,
    decode_button,
    decode_motion,
    parse_messages,
)
from server.core.encoder import (
    CANDIDATES,
    CQP_PROPERTY,
    RC_NICKS,
    RC_PROPERTY,
    EncoderConfig,
    EncoderSpec,
    RateControl,
    build_spec,
    default_bitrate_kbps,
)

# Mandatory global D-Bus initialization for GLib loop integration.
# Must be called once before any D-Bus objects are created.
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

log = logging.getLogger("TethrLink")

VERSION = "0.9.5"

# ── Codec constants ───────────────────────────────────────────────────────────
CODEC_H264 = 1
CODEC_JPEG = 2

# ── Timing constants ──────────────────────────────────────────────────────────
HANDSHAKE_TIMEOUT_S     = 10.0
# A legacy client sends nothing after the 94-byte hello and immediately blocks
# waiting for our reply, so this must stay well under HANDSHAKE_TIMEOUT_S — if
# it did not, every legacy connection (the common case today) would eat a
# multi-second stall for an extension block that will never arrive. 0.2s is
# long enough for a same-host/USB-tethered peer's extension bytes (sent
# immediately after the hello, on the same write or the next one) to already
# be sitting in the kernel receive buffer by the time we ask for them.
EXTENSION_READ_TIMEOUT_S = 0.2
FRAME_WAIT_ATTEMPTS     = 100
FRAME_WAIT_SLEEP_S      = 0.05
CAPTURE_INIT_SLEEP_S    = 0.5
MUTTER_SETUP_TIMEOUT_MS = 10_000
# Idle keepalive: if nothing has been written to the client socket for this
# long, the send loop acts (resend on JPEG, keyframe request on H.264) so the
# Android client's 3000ms socket read timeout is never starved. 1.0s gives a
# 3x margin. A static extended monitor is normal use for this product, not an
# edge case, so this must hold indefinitely, not just bridge a brief stall.
KEEPALIVE_INTERVAL_S    = 1.0

# How many consecutive idle keepalives (H.264 IDR retransmissions) pass between
# user-visible log lines. An idle extended monitor keepalives once per interval
# forever, so per-event logging above DEBUG would be pure spam; at 1.0s
# intervals this summarises about once a minute.
IDLE_KEEPALIVE_LOG_EVERY = 60

# ── Network constants ─────────────────────────────────────────────────────────
PORT_SCAN_RANGE     = 10
APPSINK_MAX_BUFFERS = 2

# H.264-only buffering depths. The JPEG path keeps APPSINK_MAX_BUFFERS above
# unchanged — its latency profile is the shipping default and is not part of
# this fix. These three depths (leaky queue upstream of the encoder, the
# H.264 appsink, and the lossless FIFO) together bounded the H.264 path at
# roughly 8 frames (~265ms at 30fps) of pure pre-render buffering, which was
# the dominant source of reported pointer lag. Shrinking them to ~4 frames
# (~133ms) trades a higher chance of FIFO overflow — which costs one brief
# keyframe resync — for lower latency on every single frame. Do not raise
# these back up without accounting for that trade.
H264_LEAKY_QUEUE_MAX_BUFFERS = 1
H264_APPSINK_MAX_BUFFERS     = 1
H264_FIFO_MAXSIZE            = 2

# ── Mutter constants ──────────────────────────────────────────────────────────
MUTTER_BUS    = "org.gnome.Mutter.ScreenCast"
MUTTER_PATH   = "/org/gnome/Mutter/ScreenCast"
MUTTER_SC_IF  = "org.gnome.Mutter.ScreenCast"
MUTTER_SES_IF = "org.gnome.Mutter.ScreenCast.Session"
MUTTER_STR_IF = "org.gnome.Mutter.ScreenCast.Stream"

MAGIC_HELLO = b"TLHELO"
MAGIC_OK    = b"TLOK__"
MAGIC_OK2   = b"TLOK2_"  # MAGIC_OK, but input was negotiated. Exactly 6 bytes
                         # like MAGIC_OK — the client reads a fixed 6-byte
                         # header and then a fixed 9-byte (w, h, codec) struct
                         # regardless of which magic it got, so the length and
                         # layout of the reply must never change; only this
                         # value differs. See the extension block below for
                         # how a client advertises that it wants MAGIC_OK2.
MAGIC_BUSY  = b"TLBUSY"

# ── Input-capability extension (optional, appended by new clients only) ───────
# A client that wants pointer/touch input appends this small, versioned,
# self-describing block directly after the existing 94-byte hello:
#
#   offset  size  field        meaning
#   0       4     marker       b"TLX1" — extension format version 1
#   4       1     cap_len      u8, number of capability bytes that follow
#   5       cap_len  capability  bitfield; bit 0 (CAP_POINTER_INPUT) = client
#                                wants pointer input
#
# This is safe against every released server: they call `conn.recv(94)` once
# and never read the client again, so these extra bytes just sit unread in
# the kernel socket buffer and are discarded along with the connection.
# A server that understands the extension reads the header, then reads
# exactly `cap_len` more bytes — never more, never fewer — so nothing is
# left over on the wire to be misread as the first input message on the
# same connection. `cap_len` is what makes the block self-describing: a
# future version can grow the capability payload and an older server that
# only inspects byte 0 still consumes the whole block correctly.
EXT_MARKER        = b"TLX1"
EXT_HEADER_LEN     = len(EXT_MARKER) + 1  # marker + cap_len byte
CAP_POINTER_INPUT  = 0x01

FALLBACK_WIDTH  = 1920
FALLBACK_HEIGHT = 1080


# ── Extension parsing (testable without network) ───────────────────────────────

def parse_input_extension(header: bytes, payload: bytes) -> bool:
    """Parse input-capability extension block and return whether input is advertised.

    Pure function, no socket operations. Takes the bytes already read and
    determines whether the client advertised pointer input support. Never raises
    — all malformed blocks degrade to "no input support" rather than propagating
    an exception into the connection handler.

    Args:
        header: The 5-byte extension header (marker + cap_len), or b"" if not read.
        payload: The capability bytes (exactly cap_len of them), or b"" if not read.

    Returns:
        True if the extension is well-formed and the capability byte has
        CAP_POINTER_INPUT bit set. False for any malformed/missing extension.
    """
    try:
        # Header must be exactly EXT_HEADER_LEN bytes (5: "TLX1" + cap_len).
        if len(header) != EXT_HEADER_LEN:
            return False

        # Header must start with the marker.
        if header[:len(EXT_MARKER)] != EXT_MARKER:
            return False

        # Extract the declared payload length from the cap_len byte.
        cap_len = header[len(EXT_MARKER)]

        # cap_len of 0 means no capability bytes follow — nothing to check.
        if cap_len == 0:
            return False

        # Payload must be exactly the declared length (never more, never fewer).
        # This ensures nothing is left on the wire for the input reader to
        # misinterpret as the first input message.
        if len(payload) != cap_len:
            return False

        # Capability byte is the first byte of the payload. Check bit 0.
        return bool(payload[0] & CAP_POINTER_INPUT)

    except Exception:
        # No input can make this raise: indexing, bitwise operations, len()
        # all fail gracefully. Catch any unforeseen exception and degrade
        # to "no input support" rather than breaking the connection.
        return False


# ── Input reading (Task 5) ────────────────────────────────────────────────────
# Consumes input_protocol.py's framing/decoders (Task 2) and drives the
# pointer through remote_input.py's RemoteInput (Task 3). Kept as plain
# module-level functions, same as parse_input_extension() above, so the
# pieces that don't need a socket or a live D-Bus session — buffer
# accumulation/capping, message routing, counter bookkeeping — are testable
# on their own.

INPUT_READ_CHUNK_BYTES = 4096
# How often the reader thread's select() call returns on its own so it can
# re-check should_stop() even when the client sends nothing. Deliberately
# NOT implemented via conn.settimeout(): that would change the timeout on
# the very socket object the send loop's blocking sendall() calls also use,
# which is exactly the cross-thread interference requirement 8 ("never
# disturb the send loop") rules out. select() lets this loop wait without
# touching that shared state at all.
INPUT_READ_POLL_S = 1.0
# The wire format caps one pending frame at header(2) + max payload(255) =
# 257 bytes — see input_protocol.py's module docstring (type:u8 length:u8
# payload[length]). A well-behaved peer's carried-forward remainder should
# never sit above that. This reader enforces its own ceiling regardless: a
# peer that sends a header claiming a payload and never finishes it (or any
# other malformed stream) must not be allowed to grow this buffer without
# bound while more bytes keep arriving — so once the remainder exceeds
# this, it is discarded outright and framing resyncs from the next byte
# read, rather than trusting the peer's declared length forever.
INPUT_BUFFER_CAP_BYTES = 257
# Caps sustained input dispatches (motion + button-press + axis combined)
# to this many per second, after an initial burst — see
# INPUT_DISPATCH_BURST below for why a burst allowance, not a strict
# minimum interval, is what's actually needed. RemoteInput.move() already
# coalesces motion on its own (see its docstring), but button() and axis()
# are never coalesced there — each is its own synchronous D-Bus round
# trip — so a flood of either, or just raw message volume in general,
# must be bounded here or it can saturate D-Bus or make the desktop
# unusable.
INPUT_DISPATCH_RATE_HZ = 200
# A single realistic gesture routinely produces more than one dispatch at
# once: a tap sends motion (the touch-down position) immediately followed
# by a button press, and a drag start adds axis on top of that — normally
# arriving in the same read, microseconds apart. A limiter with zero
# burst tolerance (a strict "no two dispatches closer than 1/rate")
# would throttle every one of these completely ordinary bursts, most
# visibly as taps whose press silently never reaches the desktop. This
# is the token-bucket burst size that absorbs any single gesture's
# coupled events before the sustained INPUT_DISPATCH_RATE_HZ limit takes
# over — see _RateLimiter below.
INPUT_DISPATCH_BURST = 20


class _RateLimiter:
    """Token bucket: up to `burst` dispatches allowed instantly, then
    refilling at `rate_hz` tokens/second. Each run_input_reader() call
    constructs its own — the budget is per connection, never shared."""

    def __init__(self, rate_hz: float = INPUT_DISPATCH_RATE_HZ,
                 burst: int = INPUT_DISPATCH_BURST):
        self._rate = rate_hz
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._last = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


def dispatch_input_message(msg: InputMessage, remote_input: RemoteInput,
                            metrics: StreamMetrics,
                            allow_rate_limited: Callable[[], bool] = lambda: True) -> None:
    """Decode one already-framed input message and forward it to
    `remote_input`, or count it dropped.

    Factored out of the reader loop so routing is testable with a fake
    RemoteInput and no socket. Callers are responsible for counting the
    message under input_events_received before calling this.

    `allow_rate_limited` gates motion, axis, and button-*press* events —
    call sites pass the flood limiter here, and a False consults counts
    as dropped. A button *release* never consults it at all and is
    always forwarded on to RemoteInput.button(): dropping a press just
    means nothing happens on the OS side, but dropping a release for a
    button RemoteInput already believes is held would leave it stuck
    down until the connection ends — the single worst failure mode this
    feature has (see remote_input.py's module docstring) — so releases
    are deliberately exempt from the flood limiter. The default no-op
    limiter (always allow) is what makes this function usable standalone
    in tests that aren't exercising rate limiting at all.

    That exemption alone would let a flood of releases reach D-Bus
    completely unthrottled, though — RemoteInput.button() is what
    actually bounds it: a release for a code it does not believe is held
    is a no-op there, and its return value tells us so, which is what
    gets counted as dropped below. Every *meaningful* release (for a
    button genuinely held) still gets through unthrottled, exactly as
    intended; only a no-op release is discarded, and at most a handful of
    buttons can be held at once, so a flood of the rest is naturally
    bounded without ever risking a stuck button.

    Never raises: RemoteInput's own methods are exception-safe by
    contract, and every decode_* helper here returns None rather than
    raising on malformed input.
    """
    if msg.type == MSG_POINTER_MOTION:
        decoded = decode_motion(msg.payload)
        if decoded is None or not allow_rate_limited():
            metrics.incr("input_events_dropped")
            return
        remote_input.move(*decoded)
    elif msg.type == MSG_POINTER_BUTTON:
        decoded = decode_button(msg.payload)
        if decoded is None:
            metrics.incr("input_events_dropped")
            return
        code, pressed = decoded
        if pressed and not allow_rate_limited():
            metrics.incr("input_events_dropped")
            return
        # button() itself decides whether a release is meaningful — see
        # this function's docstring above. A False return means it was
        # discarded as a no-op (a release for a code not currently held),
        # which is counted as dropped the same as every other drop on
        # this path; a True return covers both a press and a genuine
        # release, neither of which is a drop.
        if not remote_input.button(code, pressed):
            metrics.incr("input_events_dropped")
    elif msg.type == MSG_POINTER_AXIS:
        decoded = decode_axis(msg.payload)
        if decoded is None or not allow_rate_limited():
            metrics.incr("input_events_dropped")
            return
        remote_input.axis(*decoded)
    else:
        metrics.incr("input_events_dropped")


def accumulate_input_buffer(buffer: bytes, chunk: bytes) -> Tuple[List[InputMessage], bytes, bool]:
    """Append a freshly-read `chunk` to `buffer`, parse out complete
    messages, and enforce INPUT_BUFFER_CAP_BYTES on what's carried forward.

    Returns (messages, new_buffer, overflowed). `overflowed` is True when
    the remainder parse_messages() left behind exceeded the cap, in which
    case `new_buffer` is already b"" — the pending bytes were discarded so
    framing resyncs from the next read, rather than growing forever on a
    peer that sends a header claiming a payload and never finishes it (or
    any other malformed stream). Split out of the reader loop so it is
    unit-testable without a socket.
    """
    messages, remainder = parse_messages(buffer + chunk)
    if len(remainder) > INPUT_BUFFER_CAP_BYTES:
        return messages, b"", True
    return messages, remainder, False


def run_input_reader(conn: socket.socket, remote_input: RemoteInput,
                      metrics: StreamMetrics,
                      should_stop: Callable[[], bool]) -> None:
    """Read client input messages off `conn` until it closes or
    `should_stop()` says to, dispatching each to `remote_input`.

    Runs on its own daemon thread, one per connection, started only when
    input was negotiated and a RemoteInput session exists (see
    _handle_client). select() — not conn.settimeout() — is what lets this
    loop wake periodically to re-check should_stop(): the same socket
    object is shared with the send loop's blocking sendall() calls, and
    settimeout() would change the timeout for those too. This function
    never sets or reads conn's timeout at all, so it cannot disturb them.

    Every parsed message is counted under input_events_received; an
    unknown type, an undecodable payload, or a motion/axis/button-press
    beyond what the per-connection _RateLimiter currently allows is
    additionally counted under input_events_dropped and skipped — never
    raised, and never a reason to close the connection. Button releases
    are exempt from that limiter regardless of volume — see
    dispatch_input_message()'s docstring for why.

    A failure anywhere in this function is caught and logged, never
    raised — its only contract with the rest of the server is to never
    disturb the send loop, and an uncaught exception on a daemon thread
    would otherwise vanish silently while conceivably leaving a button
    held. release_all() in the finally block is what actually guarantees a
    button is never left stuck down, regardless of how this loop exits:
    clean disconnect, error, or shutdown.
    """
    buffer = b""
    limiter = _RateLimiter()  # one independent budget per connection
    try:
        while not should_stop():
            try:
                ready, _, _ = select.select([conn], [], [], INPUT_READ_POLL_S)
            except (OSError, ValueError):
                break  # socket already closed out from under us
            if not ready:
                continue
            try:
                chunk = conn.recv(INPUT_READ_CHUNK_BYTES)
            except (OSError, socket.timeout):
                break
            if not chunk:
                break  # peer closed its write side
            messages, buffer, overflowed = accumulate_input_buffer(buffer, chunk)
            if overflowed:
                log.warning(
                    "Input buffer exceeded %d bytes — discarding and "
                    "resyncing framing", INPUT_BUFFER_CAP_BYTES,
                )
            for msg in messages:
                metrics.incr("input_events_received")
                dispatch_input_message(msg, remote_input, metrics, limiter.allow)
    except Exception as e:
        log.debug("Input reader stopping on unexpected error: %s", e)
    finally:
        try:
            remote_input.release_all()
        except Exception:
            pass


# ── Config & State ────────────────────────────────────────────────────────────

@dataclass
class ServerConfig:
    # 30, not 45. At the device's native 2960×1848, 45 fps demands roughly
    # 246 Mpx/s of decode, which sits right on H.264 Level 5.1's 251 Mpx/s
    # ceiling — no headroom at all on the tablet's decoder. 30 fps drops that
    # to about 164 Mpx/s. It also lowers the derived bitrate from ~24,600 to
    # ~16,400 kbps, which matters because the USB link enumerates at USB 2.0
    # High Speed (480 Mbit/s theoretical, ~200–300 Mbit/s realistic) —
    # verified on the live device. `gop_length` is derived from the live fps
    # at session start, so the keyframe interval follows automatically.
    fps: int = 30
    quality: int = 90
    # H.264 is the default as of this release, not JPEG. It used to be the
    # other way around because the H.264 path was genuinely broken — it
    # corrupted the picture. That has since been fixed: frames now travel
    # through a lossless FIFO instead of a lossy one, the hardware encoder is
    # negotiated at runtime under real rate control instead of a guess,
    # capture geometry is derived from the connected device rather than the
    # PC's monitor, and the pipeline runs at a constant frame rate. The
    # result has been validated on hardware, so this flip is deliberate, not
    # an accident. `TETHRLINK_CODEC` still overrides this — set it to `jpeg`
    # to fall back to the old path for comparison.
    codec: int = CODEC_H264
    bitrate: int = 0        # 0 = derive automatically from resolution and frame rate
    h264_width: int = 1280
    port: int = 51137
    width: int = 0          # 0 = use device dims (auto)
    height: int = 0
    device_width: int = 0   # saved dims from last connected device
    device_height: int = 0
    orientation: str = "landscape"      # "landscape" | "portrait"
    auto_start: bool = False


@dataclass
class ServerState:
    running: bool = False
    connected: bool = False
    client_name: str = ""
    fps: int = 0
    resolution: str = ""
    codec_name: str = ""
    port: int = 0
    active_width: int = 0    # resolved dims from last/current connection
    active_height: int = 0
    device_width: int = 0   # dims reported by last connected device
    device_height: int = 0
    _callbacks: List[Callable] = field(default_factory=list, repr=False)

    def on_change(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def update(self, **kwargs) -> None:
        """Update fields and fire callbacks via GLib.idle_add (thread-safe)."""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
        for cb in self._callbacks:
            GLib.idle_add(cb)

    def update_direct(self, **kwargs) -> None:
        """Update fields without firing callbacks. Safe to call from any thread."""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)


# ── Resolution detection ──────────────────────────────────────────────────────

def detect_primary_resolution_mutter(bus) -> Optional[tuple]:
    try:
        proxy = bus.get_object("org.gnome.Mutter.DisplayConfig",
                               "/org/gnome/Mutter/DisplayConfig")
        iface = dbus.Interface(proxy, "org.gnome.Mutter.DisplayConfig")
        _, monitors, logical_monitors, _ = iface.GetCurrentState()
        for lm in logical_monitors:
            if not bool(lm[4]):
                continue
            monitor_connector = lm[5][0][0]
            for monitor in monitors:
                if monitor[0][0] != monitor_connector:
                    continue
                for mode in monitor[1]:
                    if mode[6].get("is-current", False):
                        w, h = int(mode[1]), int(mode[2])
                        log.info("Detected primary monitor: %dx%d", w, h)
                        return w, h
    except Exception as e:
        log.debug("Mutter DisplayConfig detection failed: %s", e)
    return None


def detect_primary_resolution_xrandr() -> Optional[tuple]:
    try:
        result = subprocess.run(["xrandr", "--query"], capture_output=True,
                                text=True, timeout=3)
        lines = result.stdout.splitlines()
        candidates = [l for l in lines if " connected primary" in l]
        if not candidates:
            candidates = [l for l in lines if " connected" in l]
        for line in candidates:
            for part in line.split():
                if "x" in part and "+" in part:
                    try:
                        w, h = part.split("+")[0].split("x")
                        return int(w), int(h)
                    except ValueError:
                        continue
    except Exception as e:
        log.debug("xrandr detection failed: %s", e)
    return None


def resolve_resolution(bus, requested_w: int, requested_h: int) -> tuple:
    if requested_w > 0 and requested_h > 0:
        return requested_w, requested_h
    detected = detect_primary_resolution_mutter(bus)
    if detected:
        return detected
    detected = detect_primary_resolution_xrandr()
    if detected:
        return detected
    log.warning("Could not detect resolution — using %dx%d", FALLBACK_WIDTH, FALLBACK_HEIGHT)
    return FALLBACK_WIDTH, FALLBACK_HEIGHT


def bind_server_socket(preferred_port: int) -> tuple:
    for port in range(preferred_port, preferred_port + PORT_SCAN_RANGE):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.listen(5)
            srv.settimeout(1.0)
            if port != preferred_port:
                log.warning("Port %d taken — bound to %d", preferred_port, port)
            else:
                log.info("Server ready on port %d", port)
            return srv, port
        except OSError:
            try:
                srv.close()
            except Exception:
                pass
    raise OSError(f"No available port in {preferred_port}–{preferred_port + PORT_SCAN_RANGE - 1}")


def detect_codec(codec_str: str) -> int:
    if codec_str == "jpeg":
        return CODEC_JPEG
    if codec_str == "h264":
        return CODEC_H264
    result = subprocess.run(["gst-inspect-1.0", "x264enc"],
                            capture_output=True, text=True)
    if "x264 H.264 Encoder" in result.stdout:
        return CODEC_H264
    return CODEC_JPEG


def detect_session_type() -> str:
    """Return 'wayland' if running in a Wayland session, otherwise 'x11'."""
    import os
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    xdg = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if xdg == "wayland":
        return "wayland"
    return "x11"


# ── Mutter virtual display ────────────────────────────────────────────────────

def _sweep_session_tree(bus, service_name: str, base_path: str,
                         session_iface: str,
                         dispose: Optional[Callable] = None) -> None:
    """Enumerate live session objects under `base_path` on `service_name`
    and tear each one down.

    Exception-safe at two levels, deliberately: the whole sweep is
    wrapped (a bus with nothing listening at `base_path`, or a session
    tree that fails to introspect, must not raise — cleanup that raises
    would be worse than the leak it exists to fix), and each discovered
    node is torn down independently, in its own try/except, so one
    session that refuses to die can't stop the rest of the sweep from
    running.

    `dispose`, when given, replaces the bare `Stop()` call used for
    ScreenCast sessions below. RemoteDesktop sessions need
    dispose_remote_desktop_session()'s more thorough Start()-then-Stop()
    fallback (see its docstring in remote_input.py): a session that was
    created but never started rejects a bare Stop() and, left at that,
    never actually leaves the bus.
    """
    try:
        obj   = bus.get_object(service_name, base_path)
        intro = dbus.Interface(obj, "org.freedesktop.DBus.Introspectable")
        import defusedxml.ElementTree as ET
        root = ET.fromstring(intro.Introspect())
        for node in root.findall("node"):
            name = node.get("name", "")
            if not name:
                continue
            try:
                session_obj = bus.get_object(service_name, f"{base_path}/{name}")
                session = dbus.Interface(session_obj, session_iface)
                if dispose is not None:
                    ok = dispose(session)
                else:
                    session.Stop()
                    ok = True
                if ok:
                    log.info("Cleaned orphaned session: %s", name)
                else:
                    log.debug("Orphaned session %s did not dispose", name)
            except Exception as e:
                log.debug("Could not clean orphaned session %s: %s", name, e)
    except Exception as e:
        log.debug("Session cleanup at %s: %s", base_path, e)


def cleanup_orphaned_sessions(bus):
    """Sweep leftover ScreenCast AND RemoteDesktop sessions from a
    previous run.

    Two independent object trees, so two independent sweeps.
    RemoteDesktop sessions (Task 5's input pairing, created by
    RemoteInput.create()) live entirely under RD_PATH and are never
    returned by introspecting MUTTER_PATH — without this second sweep, a
    RemoteDesktop session orphaned by a run that never got the chance to
    tear it down itself (a crash, a kill -9, or simply a connection that
    hit MutterVirtualDisplay.setup()'s paired-failure path before that
    path's own immediate teardown was added) would persist on the bus
    forever instead of being cleaned up the next time a client connects.
    """
    _sweep_session_tree(bus, MUTTER_BUS, MUTTER_PATH, MUTTER_SES_IF)
    _sweep_session_tree(bus, RD_BUS, RD_PATH, RD_SES_IF,
                         dispose=dispose_remote_desktop_session)


class MutterVirtualDisplay:
    def __init__(self, width: int, height: int, bus=None,
                 remote_input: Optional[RemoteInput] = None,
                 remote_input_session_id: Optional[str] = None):
        self.width       = width
        self.height      = height
        self._node_id    = None
        self._error      = None
        self._in_setup   = True   # True until setup() returns
        self.on_closed   = None   # callable(); set after setup() for external close events
        self._bus  = bus or dbus.SessionBus()
        self._context = GLib.MainContext()
        self._loop    = GLib.MainLoop(self._context)
        # Task 5's input pairing: when both are given, this ScreenCast
        # session is created with remote-desktop-session-id set to
        # remote_input_session_id, binding it to remote_input's
        # already-created RemoteDesktop session. Verified on a live Mutter
        # 46 session: once paired, only the RemoteDesktop session's own
        # Start()/Stop() may be called — calling this ScreenCast session's
        # Start()/Stop() directly raises "Must be started/stopped from
        # remote desktop session". See setup()/close() below.
        self._remote_input = remote_input
        self._remote_input_session_id = remote_input_session_id
        self._paired = False   # set in setup() once pairing props are used
        self._stream_path: Optional[str] = None
        cleanup_orphaned_sessions(self._bus)
        sc_obj    = self._bus.get_object(MUTTER_BUS, MUTTER_PATH)
        self._sc  = dbus.Interface(sc_obj, MUTTER_SC_IF)
        self._session_path = None

    def setup(self) -> int:
        log.info("Creating Mutter ScreenCast session...")

        pairing_props = {}
        if self._remote_input is not None and self._remote_input_session_id is not None:
            pairing_props = {
                "remote-desktop-session-id": dbus.String(self._remote_input_session_id)
            }

        # Try to suppress GNOME screen-recording indicator (Mutter ≥ 44
        # honours this), retrying without it if that combination is
        # rejected. Pairing props, when present, are included in every
        # attempt — unlike disable-notifications they are not optional once
        # input was negotiated and paired for this connection. Which
        # attempt is "last" is now tracked by index rather than by the
        # props dict being falsy, since a non-empty pairing_props dict
        # would otherwise be (wrongly) truthy on the final attempt too.
        attempts = [
            {**pairing_props, "disable-notifications": dbus.Boolean(True)},
            dict(pairing_props),
        ]
        for i, props in enumerate(attempts):
            try:
                self._session_path = str(self._sc.CreateSession(
                    dbus.Dictionary(props, signature="sv")
                ))
                break
            except Exception as e:
                if i == len(attempts) - 1:
                    raise
                log.debug("CreateSession with disable-notifications failed (%s), retrying plain", e)
        self._paired = bool(pairing_props)

        # Everything from here through the loop run is wrapped: once
        # CreateSession above has succeeded, a paired session means
        # RemoteInput.create() already created a RemoteDesktop session on
        # the bus too (before this object even existed — see
        # ServerCore._handle_client). If RecordVirtual raises, the setup
        # timeout fires, or the ScreenCast session gets closed out from
        # under us before the PipeWire stream ever shows up, Start() on
        # that RemoteDesktop session is never reached — and it must not
        # be left dangling on the bus for cleanup_orphaned_sessions() to
        # maybe find on some later run when it can be disposed right now
        # instead. See the except block below.
        try:
            session_obj = self._bus.get_object(MUTTER_BUS, self._session_path)
            session     = dbus.Interface(session_obj, MUTTER_SES_IF)

            def _on_closed():
                if self._in_setup:
                    setattr(self, "_error", "Session closed during setup")
                    self._loop.quit()
                elif self.on_closed:
                    try:
                        self.on_closed()
                    except Exception:
                        log.exception("on_closed callback raised")

            session_obj.connect_to_signal("Closed", _on_closed, dbus_interface=MUTTER_SES_IF)

            stream_path = str(session.RecordVirtual(
                dbus.Dictionary({"cursor-mode": dbus.UInt32(1)}, signature="sv")
            ))
            self._stream_path = stream_path
            stream_obj = self._bus.get_object(MUTTER_BUS, stream_path)
            stream_obj.connect_to_signal("PipeWireStreamAdded",
                lambda node_id: (setattr(self, "_node_id", int(node_id)),
                                 log.info("PipeWire node: %d", int(node_id)),
                                 self._loop.quit()),
                dbus_interface=MUTTER_STR_IF)

            if self._paired:
                # Paired: only the RemoteDesktop session's own Start() may be
                # called (see __init__'s docstring note) — it drives this
                # ScreenCast session too. bind_stream() first so the pointer
                # methods have a valid stream the instant the session comes up.
                self._remote_input.bind_stream(stream_path)
                self._remote_input.start()
            else:
                session.Start()

            GLib.timeout_add(MUTTER_SETUP_TIMEOUT_MS, lambda: (
                setattr(self, "_error", "Timeout"), self._loop.quit()
            ))
            self._loop.run()
            self._in_setup = False
            if self._error:
                raise RuntimeError(self._error)
        except Exception:
            self._in_setup = False
            if self._paired and self._remote_input is not None:
                # Tear down the paired RemoteDesktop session immediately,
                # rather than counting on the caller to do it (it does —
                # see ServerCore._handle_client — but this class should
                # not depend on that). remote_input.stop() is exception-
                # safe end-to-end on its own (it routes through
                # dispose_remote_desktop_session(), which specifically
                # handles a session that was created but never started —
                # see remote_input.py), but it is wrapped again here
                # anyway: a cleanup attempt raising out of an exception
                # handler would replace this failure with a worse one.
                try:
                    self._remote_input.stop()
                except Exception:
                    pass
            raise
        return self._node_id

    def close(self):
        if self._paired and self._remote_input is not None:
            # This ScreenCast session's own Stop() raises "must be stopped
            # from remote desktop session" once paired (same constraint as
            # Start(), verified on a live Mutter 46 session) — only the
            # RemoteDesktop session's Stop() actually tears down both.
            # RemoteInput.stop() also flushes any pending motion and
            # releases any held button, so this is the only call needed
            # for a paired session's teardown.
            try:
                self._remote_input.stop()
            except Exception:
                pass
            return
        if self._session_path:
            try:
                obj = self._bus.get_object(MUTTER_BUS, self._session_path)
                dbus.Interface(obj, MUTTER_SES_IF).Stop()
            except Exception:
                pass


# ── X11 virtual display ───────────────────────────────────────────────────────

class X11VirtualDisplay:
    """
    Tries to provide a virtual second monitor on X11.
    Strategy 1: xrandr VIRTUAL output (requires driver support).
    Strategy 2: Xvfb on a spare display number.
    setup() returns a monitor dict on success, None on failure.
    The dict may include a 'display' key (str) for Xvfb.
    """

    def __init__(self, width: int, height: int):
        self._width  = width
        self._height = height
        self._strategy: str = ""
        self._xrandr_output: str = ""
        self._xrandr_mode:   str = ""
        self._xvfb_proc: Optional[subprocess.Popen] = None
        self._xvfb_display: str = ""

    def setup(self) -> Optional[dict]:
        mon = self._try_xrandr_virtual()
        if mon is not None:
            return mon
        return self._try_xvfb()

    def _try_xrandr_virtual(self) -> Optional[dict]:
        try:
            result = subprocess.run(["xrandr", "--query"],
                                    capture_output=True, text=True, timeout=5)
            virtual = None
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts and parts[0].startswith("VIRTUAL") and "disconnected" in line:
                    virtual = parts[0]
                    break
            if not virtual:
                return None

            mode_name = f"TL_{self._width}x{self._height}"
            cvt = subprocess.run(
                ["cvt", str(self._width), str(self._height), "60"],
                capture_output=True, text=True, timeout=5,
            )
            modeline_parts = None
            for line in cvt.stdout.splitlines():
                if line.startswith("Modeline"):
                    modeline_parts = line.split()[2:]
                    break
            if not modeline_parts:
                return None

            subprocess.run(["xrandr", "--newmode", mode_name] + modeline_parts,
                           check=True, capture_output=True, timeout=5)
            subprocess.run(["xrandr", "--addmode", virtual, mode_name],
                           check=True, capture_output=True, timeout=5)
            primary_w = (detect_primary_resolution_xrandr() or (1920, 1080))[0]
            subprocess.run(
                ["xrandr", "--output", virtual, "--mode", mode_name,
                 "--right-of", "eDP-1"],
                check=True, capture_output=True, timeout=5,
            )
            self._strategy      = "xrandr"
            self._xrandr_output = virtual
            self._xrandr_mode   = mode_name
            log.info("X11 virtual display: xrandr %s %dx%d", virtual,
                     self._width, self._height)
            return {"left": primary_w, "top": 0,
                    "width": self._width, "height": self._height}
        except Exception as e:
            log.debug("xrandr virtual output failed: %s", e)
            return None

    def _try_xvfb(self) -> Optional[dict]:
        for num in range(99, 120):
            if not os.path.exists(f"/tmp/.X11-unix/X{num}"):
                display_num = num
                break
        else:
            return None
        try:
            self._xvfb_proc = subprocess.Popen(
                ["Xvfb", f":{display_num}",
                 "-screen", "0", f"{self._width}x{self._height}x24",
                 "-ac", "+extension", "GLX", "+render", "-noreset"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(0.8)
            if self._xvfb_proc.poll() is not None:
                return None
            self._strategy     = "xvfb"
            self._xvfb_display = f":{display_num}"
            log.info("X11 virtual display: Xvfb %s %dx%d",
                     self._xvfb_display, self._width, self._height)
            return {"left": 0, "top": 0,
                    "width": self._width, "height": self._height,
                    "display": self._xvfb_display}
        except Exception as e:
            log.debug("Xvfb start failed: %s", e)
            return None

    def close(self):
        if self._strategy == "xrandr":
            for cmd in [
                ["xrandr", "--output", self._xrandr_output, "--off"],
                ["xrandr", "--delmode", self._xrandr_output, self._xrandr_mode],
                ["xrandr", "--rmmode", self._xrandr_mode],
            ]:
                try:
                    subprocess.run(cmd, capture_output=True, timeout=5)
                except Exception:
                    pass
        elif self._strategy == "xvfb" and self._xvfb_proc:
            try:
                self._xvfb_proc.terminate()
                self._xvfb_proc.wait(timeout=3)
            except Exception:
                pass
            self._xvfb_proc = None


# ── GStreamer capture ─────────────────────────────────────────────────────────

_ENCODER_CACHE = {}


def probe_rate_controls(element: str) -> set:
    """Report which rate-control modes this element accepts ON THIS MACHINE.

    Determined by actually setting the property and seeing what sticks, which
    is more reliable than introspecting the enum class through PyGObject. The
    answer is genuinely hardware-dependent: the enum is populated from the
    render node, so `vah264lpenc` accepts only `cqp` on Intel Comet Lake
    while `vaapih264enc` accepts all three.

    Elements with no rate-control property at all (v4l2h264enc, openh264enc)
    are reported as supporting everything, because their adapters express
    rate control through other properties entirely.

    Never raises. This runs on the client connection path (via
    select_encoder), and a GStreamer diagnostic escaping from here would be
    exactly the bug class log_gstreamer_preflight() below is already
    defensive about — it could prevent the caller (ServerCore.__init__) from
    ever completing. Any unexpected failure is treated as "this element
    offers nothing usable" (empty set) and logged, never swallowed silently.
    """
    try:
        # Gst.ElementFactory.find() silently returns None for every element,
        # hardware included, until Gst.init() has run — and this is the first
        # Gst call standalone callers (this task's own verification script,
        # select_encoder() before any ServerCore exists) make. Idempotent, like
        # every other Gst.init(None) call in this file.
        Gst.init(None)
        factory = Gst.ElementFactory.find(element)
        if factory is None:
            return set()
        try:
            el = factory.create(None)
        except Exception:
            return set()
        if el is None:
            return set()

        prop_name = RC_PROPERTY.get(element)
        if prop_name is None:
            return {RateControl.CBR, RateControl.VBR, RateControl.CQP}

        try:
            if el.find_property(prop_name) is None:
                return {RateControl.CBR, RateControl.VBR, RateControl.CQP}
        except Exception:
            return {RateControl.CBR, RateControl.VBR, RateControl.CQP}

        modes = set()
        for mode in (RateControl.CBR, RateControl.VBR, RateControl.CQP):
            nick = RC_NICKS.get(element, {}).get(mode)
            if nick is None:
                continue
            try:
                el.set_property(prop_name, nick)
                modes.add(mode)
            except Exception:
                pass
        return modes
    except Exception as e:
        # Catches failures in Gst.init(None)/Gst.ElementFactory.find(), which
        # sit outside every try/except above — everything else in this
        # function already narrows its own exceptions and returns early.
        log.warning(
            "probe_rate_controls(%s) failed unexpectedly — assuming no "
            "usable rate-control modes: %s", element, e
        )
        return set()


def _encoder_runs(spec: EncoderSpec, width: int, height: int) -> bool:
    """Actually encode a frame, at the size about to be used. Presence and
    properties are not enough.

    vaapih264enc advertises cbr and still fails with an internal data stream
    error at runtime on this hardware, so nothing short of running it counts
    as verification.

    The size matters as much as the element does. This used to verify at a
    fixed 320x240, which every encoder passes — including ones with a level or
    frame-size ceiling below the real capture size (v4l2h264enc is commonly
    capped at 1920x1080, and low-power VA encoders have their own limits).
    Such an encoder passed the toy test, got selected, and then failed
    negotiation at the real size, ending the session. Verifying at the actual
    resolution is the only test that answers the question being asked.

    `num-buffers=2` is the minimum that exercises a real inter-frame encode
    (one keyframe plus one predicted frame) and is kept deliberately low: the
    frames are now full-size, so each one costs real memory and time. The 5s
    per-candidate timeout is unchanged — a full re-probe stays bounded at
    len(CANDIDATES) * 5s.
    """
    desc = (
        f"videotestsrc num-buffers=2 ! "
        f"video/x-raw,format=NV12,width={width},height={height},framerate=30/1 ! "
        f"{spec.to_pipeline_fragment()} ! fakesink sync=false"
    )
    pipeline = None
    try:
        pipeline = Gst.parse_launch(desc)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return False
        msg = pipeline.get_bus().timed_pop_filtered(
            5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
        )
        return msg is not None and msg.type == Gst.MessageType.EOS
    except Exception as e:
        log.debug("Encoder %s failed verification: %s", spec.element, e)
        return False
    finally:
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)


def gstreamer_fingerprint() -> str:
    """Identify this GStreamer installation for cache invalidation.

    A cached "nvh264enc works here" is a claim about the drivers and plugins
    present, not about the app. Upgrading GStreamer, installing a VA driver,
    or losing access to a render node must all invalidate it — so the
    fingerprint is the version plus exactly which candidate encoders the
    registry can currently see.
    """
    try:
        Gst.init(None)
        present = [
            name for name in CANDIDATES
            if Gst.ElementFactory.find(name) is not None
        ]
        return f"{Gst.version_string()}|{','.join(sorted(present))}"
    except Exception as e:
        # An unfingerprintable environment simply never matches a stored
        # entry, so this degrades to always probing rather than to failing.
        log.debug("Could not fingerprint GStreamer: %s", e)
        return "unknown"


def _encoder_spec_from_cache(cached: dict) -> Optional[EncoderSpec]:
    """Reconstruct an EncoderSpec from a disk-cached encoder record.

    Returns None for anything malformed instead of raising. profiles.py
    documents its own contract as: a missing, corrupt, or wrongly-shaped
    store degrades to "no cached value" — one slow start, nothing else (see
    its module docstring). That contract has to hold for what's *inside* a
    well-formed-looking record too, not just for the store's outer shape.
    A hand-edited or corrupted `props` field — a list, a string, a number
    where a mapping was expected — used to reach a bare `dict(...)` call
    here and raise (`dict(['a', 'b', 'c'])` raises ValueError). That raise
    happened before `_encoder_runs` (the trust-but-verify gate) and before
    the CANDIDATES probe loop ever got a chance to run and overwrite the bad
    entry, so a single corrupted record used to break every subsequent
    connection identically instead of costing one slow start.

    `element` must be a non-empty string — a spec naming no element isn't a
    usable cached selection. `props`, when present, must be a mapping; when
    absent it defaults to `{}` (that was already the pre-fix behaviour via
    `cached.get("props") or {}` and is not a malformed case, just an encoder
    recorded with no extra properties).
    """
    if not isinstance(cached, dict):
        return None
    element = cached.get("element")
    if not isinstance(element, str) or not element:
        return None
    rate_control = cached.get("rate_control", "")
    if not isinstance(rate_control, str):
        return None
    props = cached.get("props")
    if props is None:
        props = {}
    if not isinstance(props, dict):
        return None
    return EncoderSpec(
        element=element,
        is_hardware=bool(cached.get("is_hardware")),
        rate_control=rate_control,
        props=dict(props),
    )


def select_encoder(config: EncoderConfig, width: int, height: int):
    """Pick the best encoder that actually works at this size, hardware first.

    Result is cached: probing instantiates pipelines, which is too expensive
    to repeat per client connection. Only a successful (non-None) selection
    is cached — see the comment above the cache write below for why.

    The capture size is part of the cache key, not just an argument. An
    encoder that works at one resolution can fail at another (level and
    frame-size ceilings), so a device connecting with different dimensions
    must trigger fresh verification rather than inheriting a verdict reached
    for some other size.
    """
    key = (config.rate_control, config.gop_length, config.bitrate_kbps,
           config.low_latency, width, height)
    if key in _ENCODER_CACHE:
        return _ENCODER_CACHE[key]

    fingerprint = gstreamer_fingerprint()
    store = ProfileStore()
    store.load()
    cached = store.get_encoder(fingerprint)
    if cached:
        spec = _encoder_spec_from_cache(cached)
        if spec is None:
            # Mirrors the defensive style around build_spec() in the probe
            # loop below: a corrupted or hand-edited cache entry must
            # degrade to "no cached value" and fall through to a normal
            # probe — never raise and break every subsequent connection
            # identically. INFO, not WARNING: a successful probe below will
            # overwrite this bad entry with a freshly-verified one (see the
            # cache write further down), so this is a self-healing recovery
            # being logged, not a failure — but it still leaves a line a
            # user reporting "it went slow again" can find.
            log.info(
                "Cached encoder record was malformed (%r) — discarding "
                "and re-probing", cached,
            )
        elif spec.element and _encoder_runs(spec, width, height):
            # Trust but verify: a cached encoder that no longer works — a GPU in
            # use by another session, a driver that loaded differently — must not
            # break the stream. Re-running it is far cheaper than the full probe.
            log.info("Encoder from cache: %s (%s, rate-control=%s)",
                     spec.element,
                     "hardware" if spec.is_hardware else "software",
                     spec.rate_control)
            _ENCODER_CACHE[key] = spec
            return spec
        else:
            log.info("Cached encoder %s no longer usable — re-probing",
                     spec.element)

    chosen = None
    for element in CANDIDATES:
        # Runs on the client connection path, same as probe_rate_controls()
        # above. probe_rate_controls()/_encoder_runs() are already
        # exception-safe on their own, but build_spec() (encoder.py) is not
        # under this function's control — one pathological candidate must
        # not abort selection for every candidate ranked below it. Log and
        # move on to the next one instead of propagating.
        try:
            spec = build_spec(element, config, probe_rate_controls(element))
            if spec is None:
                continue
            if not _encoder_runs(spec, width, height):
                log.info("Encoder %s present but not usable at %dx%d — skipping",
                         element, width, height)
                continue
        except Exception as e:
            log.warning(
                "Encoder candidate %s raised during probing — skipping: %s",
                element, e
            )
            continue
        chosen = spec
        break

    if chosen is None:
        log.error("No usable H.264 encoder found at %dx%d", width, height)
    else:
        log.info("Encoder selected: %s (%s, rate-control=%s, verified at %dx%d)",
                 chosen.element,
                 "hardware" if chosen.is_hardware else "software",
                 chosen.rate_control, width, height)
    # Only cache a successful selection. Caching None here would latch "no
    # usable encoder" for this config for the rest of the process's
    # lifetime with no retry — a transient failure (a momentarily busy GPU,
    # a slow _encoder_runs bus wait) would need an app restart to ever get
    # hardware encoding again. Leaving None uncached means a machine with
    # genuinely no usable encoder re-probes on every connection instead of
    # once, but that path stays bounded: _encoder_runs already caps each
    # candidate at a 5s timeout, so a full re-probe is capped at
    # len(CANDIDATES) * 5s, not unbounded — do not raise that timeout.
    if chosen is not None:
        _ENCODER_CACHE[key] = chosen
        store.set_encoder(fingerprint, {
            "element": chosen.element,
            "is_hardware": chosen.is_hardware,
            "rate_control": chosen.rate_control,
            "props": chosen.props,
        })
        store.save()
    return chosen


def describe_h264_encoding(spec, bitrate_kbps: int, bitrate_source: str) -> str:
    """Describe what the chosen encoder will actually do.

    Deliberately reports the selected spec rather than the request. A bitrate
    was asked for, but CBR may not have been available: `build_spec` degrades
    to CQP rather than refusing the encoder, and CQP has no bitrate target at
    all. Reporting the requested number in that case overstates what happened,
    which is a defect in its own right.
    """
    if spec is None:
        # PipeWireCapture falls back to its hardcoded software encoder; keep
        # this message in step with that fragment.
        return (
            f"H.264: no usable encoder found — falling back to built-in "
            f"x264enc (CBR 25000 kbps). The {bitrate_kbps} kbps target "
            f"({bitrate_source}) is NOT used."
        )

    kind = "hardware" if spec.is_hardware else "software"
    if spec.rate_control == RateControl.CQP:
        quantizers = ", ".join(
            f"{name}={spec.props[name]}"
            for name in CQP_PROPERTY.get(spec.element, ())
            if name in spec.props
        ) or "encoder default"
        return (
            f"H.264: {spec.element} ({kind}) — rate-control=cqp, "
            f"constant quantizer {quantizers}. CBR was unavailable on this "
            f"hardware, so the {bitrate_kbps} kbps target ({bitrate_source}) "
            f"is NOT used."
        )
    return (
        f"H.264: {spec.element} ({kind}) — rate-control={spec.rate_control}, "
        f"{bitrate_kbps} kbps ({bitrate_source})"
    )


def log_gstreamer_preflight() -> None:
    """Log the GStreamer the running process actually loaded."""
    try:
        Gst.init(None)
        registry = Gst.Registry.get()
        plugin = registry.find_plugin("coreelements")
        plugin_path = plugin.get_filename() if plugin else "unknown"
        available = probe_encoders(
            finder=Gst.ElementFactory.find,
            candidates=H264_ENCODER_CANDIDATES,
        )
        for line in format_preflight(
            Gst.version_string(), plugin_path, available
        ).splitlines():
            log.info(line)
    except Exception as e:
        log.warning("GStreamer preflight diagnostics unavailable: %s", e)


class PipeWireCapture:
    # Class-level default so a capture built without __init__ (the unit
    # tests construct bare instances to exercise the real accessors without
    # a GStreamer pipeline) still has a well-defined empty cache.
    _last_keyframe: Optional[bytes] = None

    def __init__(self, node_id: int, width: int, height: int, codec: int,
                 fps: int, bitrate: int, h264_width: int, quality: int = 90,
                 flip_orientation: bool = False,
                 on_error: Optional[Callable] = None,
                 encoder_spec: Optional[EncoderSpec] = None):
        self.width  = width
        self.height = height
        self.codec  = codec
        self._fw    = width
        self._fh    = height
        self._lock  = threading.Lock()
        self._encoder_spec = encoder_spec

        # H.264 is an inter-frame codec: losing one encoded frame corrupts
        # every frame until the next keyframe, so its path must be lossless.
        # JPEG frames are independent, so latest-wins remains correct there.
        self.metrics        = StreamMetrics()
        self.is_inter_frame = (codec == CODEC_H264)
        if self.is_inter_frame:
            self._sink_buffer = EncodedFrameQueue(
                maxsize=H264_FIFO_MAXSIZE,
                metrics=self.metrics,
                on_overflow=self._request_keyframe,
            )
        else:
            self._sink_buffer = LatestFrameSlot(metrics=self.metrics)
        # Set once the appsink callback has OBSERVED a sample, so the
        # handshake can learn the real (possibly scaled) frame dimensions
        # without popping anything off the encoded-frame queue.
        self._first_sample = threading.Event()
        # Most recent IDR access unit, kept so the send loop can retransmit
        # it as an idle keepalive when the source stops producing frames
        # entirely. See last_keyframe().
        self._last_keyframe: Optional[bytes] = None
        self._jpegenc  = None
        self._flip     = None
        self._on_error = on_error  # called when pipeline errors — lets ServerCore abort the stream
        self._closed   = False     # guard against double-close

        Gst.init(None)
        self._loop = GLib.MainLoop()

        if codec == CODEC_H264:
            if self._encoder_spec is not None:
                encoder_fragment = self._encoder_spec.to_pipeline_fragment()
            else:
                # Preserved fallback: the previously hardcoded software encoder.
                encoder_fragment = (
                    "x264enc tune=zerolatency speed-preset=ultrafast "
                    "pass=cbr bitrate=25000 key-int-max=45 bframes=0"
                )

            # option-string is x264-specific and would fail to parse on any
            # other encoder, so it is appended conditionally rather than
            # carried in the props dict that to_pipeline_fragment() renders.
            if encoder_fragment.startswith("x264enc"):
                encoder_fragment += (
                    ' option-string="colorprim=bt709:transfer=bt709:'
                    'colormatrix=bt709:fullrange=off"'
                )

            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                # This caps filter is what determines the virtual monitor's
                # size — Mutter's RecordVirtual takes no size arguments, the
                # size comes from PipeWire format negotiation. The JPEG branch
                # always had this; the H.264 branch did not, which is why it
                # got Mutter's default and then rescaled.
                f"! video/x-raw,width={width},height={height},max-framerate={fps}/1 "
                # Dropping RAW frames is safe — it only lowers framerate.
                f"! queue leaky=downstream max-size-buffers={H264_LEAKY_QUEUE_MAX_BUFFERS} max-size-bytes=0 max-size-time=0 "
                # Mutter's ScreenCast capture is strictly damage-driven: on a
                # static screen (no mouse movement, no video, no pixel
                # changes at all) it stops producing frames entirely —
                # measured in isolation with tools/diagnose_capture_stall.py,
                # which saw 21 frames on an idle virtual display and then
                # zero for the rest of the run. No caps request fixes this:
                # asking pipewiresrc for a FIXED framerate={fps}/1 instead of
                # max-framerate={fps}/1 was tried and still produced zero
                # frames once the display went idle, so the constant rate
                # has to be manufactured downstream, not requested upstream.
                # compositor aggregates its input on its OWN clock and keeps
                # emitting even when that input goes quiet, so it is what
                # actually supplies the missing constant rate; the caps
                # filter right after it is what makes that rate concrete
                # rather than "whatever came in". latency=0 was measured to
                # hold a steady {fps}fps with no added latency, which matters
                # because the buffering on this path was deliberately shrunk
                # from ~265ms to ~133ms to fix pointer lag — any latency
                # given back here would undo that fix. videorate is
                # deliberately NOT used here: with the compositor already
                # fixing the output rate, a second element trying to
                # normalise it too would be redundant and confusing. Remove
                # this element only if Mutter's ScreenCast stops being
                # damage-driven — until then the stream is 1fps-when-idle
                # and cold-on-resume without it, papered over only by the
                # idle-IDR keepalive below (see last_keyframe()).
                f"! compositor name=c background=black latency=0 "
                f"! video/x-raw,framerate={fps}/1 "
                f"! videoconvert "
                f"! video/x-raw,format=NV12,colorimetry=bt709 "
                f"! {encoder_fragment} "
                f"! h264parse config-interval=-1 "
                f"! video/x-h264,stream-format=byte-stream,alignment=au,profile=high "
                # drop=false: everything downstream of the encoder is lossless.
                # Dropping an encoded inter-frame breaks the decoder's
                # reference chain and corrupts every frame until the next
                # keyframe, which is why dropping is confined to the leaky
                # queue upstream of the encoder.
                f"! appsink name=sink emit-signals=true "
                f"  max-buffers={H264_APPSINK_MAX_BUFFERS} drop=false sync=false"
            )
        else:
            flip_method = "clockwise" if flip_orientation else "none"
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                f"! video/x-raw,width={width},height={height},max-framerate={fps}/1 "
                f"! videoconvert "
                f"! video/x-raw,format=YUY2 "
                f"! videoflip name=flip method={flip_method} "
                f"! jpegenc name=jpegenc quality={quality} "
                f"! appsink name=sink emit-signals=true "
                f"  max-buffers={APPSINK_MAX_BUFFERS} drop=true sync=false"
            )

        log.info("GStreamer: %s", pipeline_str)
        self._pipeline = Gst.parse_launch(pipeline_str)

        if codec == CODEC_JPEG:
            self._jpegenc = self._pipeline.get_by_name("jpegenc")
            self._flip    = self._pipeline.get_by_name("flip")

        sink = self._pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)

        # Watch pipeline bus for errors/EOS — fires when PipeWire node is
        # destroyed (e.g. Mutter reconfigures displays). We release the
        # pipeline immediately so Mutter isn't blocked waiting for PipeWire.
        gst_bus = self._pipeline.get_bus()
        gst_bus.add_signal_watch()
        gst_bus.connect("message::error", self._on_bus_error)
        gst_bus.connect("message::eos",   self._on_bus_eos)

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer pipeline failed")
        log.info("GStreamer pipeline playing (codec=%s)",
                 "H.264" if codec == CODEC_H264 else "JPEG")
        threading.Thread(target=self._loop.run, daemon=True).start()

    def last_keyframe(self) -> Optional[bytes]:
        """The most recent IDR access unit seen by the appsink, or None.

        Exists for the send loop's idle keepalive: when PipeWire stops
        delivering frames (a virtual display only generates damage when
        something repaints on it, so an idle one produces nothing at all),
        the encoder has no input and therefore emits nothing — no keyframe
        request can conjure a frame that does not exist. Retransmitting
        this cached IDR is what keeps the client's read timeout satisfied.

        Safe by H.264 semantics in a way a delta frame is not: an
        instantaneous decoder refresh resets the decoder's reference state
        by definition, so re-feeding one cannot corrupt it, and it paints
        the same picture the client is already showing.

        Guarded by the same lock `_on_sample` writes under, so the caller
        always sees a whole access unit.
        """
        with self._lock:
            return self._last_keyframe

    def require_keyframe_gate(self) -> None:
        """Reject delta frames until a fresh IDR arrives.

        Delegates to the frame queue's existing post-overflow gate. The
        send loop calls this after retransmitting a cached IDR: the decoder
        is then at "just decoded that IDR" while the encoder's reference
        state is wherever it left off, so the next delta frame it emits
        would decode against the wrong picture. No-op on the JPEG path,
        whose frames are independently decodable and need no gating.
        """
        if self.is_inter_frame:
            self._sink_buffer.require_keyframe()

    def request_keyframe(self, reason: str = "keepalive resync") -> None:
        """Public entry point for forcing an IDR.

        Thin wrapper around `_request_keyframe()` so callers outside this
        class (the send loop's idle keepalive) don't reach into a private
        method. Same operation as the overflow handler uses; there is only
        ever one legitimate way to ask the encoder for a fresh IDR.

        Logs at DEBUG rather than WARNING: an idle stream calls this once
        per keepalive interval by design, and a warning per second would
        drown the log for what is the normal state of an unattended
        extended monitor.
        """
        self._request_keyframe(reason=reason, log_level=logging.DEBUG)

    def _request_keyframe(self, reason: str = "frame queue overflow",
                          log_level: int = logging.WARNING) -> None:
        """Force an IDR after a queue overflow.

        The backlog was discarded, so the decoder's reference chain is broken;
        only a keyframe restores it. The force-key-unit event is an upstream
        event, so it must be pushed (not sent) from the appsink's sink pad —
        gst_pad_send_event() on a sink pad only accepts downstream events and
        silently drops upstream ones; gst_pad_push_event() is what actually
        propagates upstream to the peer (the encoder).
        """
        try:
            sink = self._pipeline.get_by_name("sink")
            if sink is None:
                return
            pad = sink.get_static_pad("sink")
            if pad is None:
                return

            if GstVideo is not None:
                event = GstVideo.video_event_new_upstream_force_key_unit(
                    Gst.CLOCK_TIME_NONE, True, 0
                )
            else:
                structure = Gst.Structure.new_from_string(
                    "GstForceKeyUnit, all-headers=(boolean)true"
                )
                event = Gst.Event.new_custom(
                    Gst.EventType.CUSTOM_UPSTREAM, structure
                )

            accepted = pad.push_event(event)
            if accepted:
                self.metrics.incr("keyframe_requests")
                log.log(log_level, "Forced keyframe resync (%s)", reason)
            else:
                log.log(
                    log_level,
                    "Keyframe request rejected by the pipeline (%s); decoder "
                    "may stay corrupted until the next scheduled keyframe",
                    reason,
                )
        except Exception as e:
            log.debug("Force-keyframe request failed: %s", e)

    def _on_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.ERROR
        buf    = sample.get_buffer()
        ok, mi = buf.map(Gst.MapFlags.READ)
        if ok:
            data = bytes(mi.data)
            buf.unmap(mi)
            if self.is_inter_frame:
                is_key = not buf.has_flags(Gst.BufferFlags.DELTA_UNIT)
                try:
                    caps = sample.get_caps().get_structure(0)
                    with self._lock:
                        self._fw = caps.get_value("width")
                        self._fh = caps.get_value("height")
                except Exception:
                    pass
                if is_key:
                    # h264parse runs with config-interval=-1, so every IDR
                    # access unit carries its own SPS/PPS — which is what
                    # makes a cached copy independently decodable and safe
                    # to retransmit later.
                    with self._lock:
                        self._last_keyframe = data
                self._sink_buffer.put(data, is_keyframe=is_key)
            else:
                self._sink_buffer.put(data)
            self._first_sample.set()
        return Gst.FlowReturn.OK

    def set_quality(self, quality: int) -> None:
        """Hot-reload JPEG quality. No-op for H.264."""
        if self._jpegenc is not None:
            self._jpegenc.set_property("quality", quality)

    def set_orientation(self, portrait: bool) -> None:
        """Hot-reload orientation via videoflip. Swaps reported frame dims. No-op for H.264."""
        if self._flip is not None:
            method = "clockwise" if portrait else "none"
            self._flip.set_property("method", method)
            # After rotation the output is transposed — update reported dims so
            # the server sends the correct stream size to the client.
            with self._lock:
                if portrait:
                    self._fw, self._fh = self.height, self.width
                else:
                    self._fw, self._fh = self.width, self.height

    def get_frame(self):
        """JPEG path: latest frame wins. None when nothing new has arrived.

        JPEG-only by contract. On the H.264 path the buffer is a lossless
        queue whose only legitimate consumer is the send loop: popping from
        anywhere else consumes an access unit (the first one carries
        SPS/PPS/IDR). Callers that only want the frame size must use
        wait_for_frame_dimensions(); callers that want frames must use
        get_encoded_frame(). Fail loudly rather than corrupt the stream.
        """
        if self.is_inter_frame:
            raise RuntimeError(
                "get_frame() is JPEG-only — use get_encoded_frame() from the "
                "send loop, or wait_for_frame_dimensions() for dimensions"
            )
        data = self._sink_buffer.get()
        if data is None:
            return None
        with self._lock:
            return (data, self._fw, self._fh)

    def peek_frame(self):
        """JPEG path: report the current frame WITHOUT consuming it.

        For the handshake's dimension probe. On an idle capture (a static
        virtual display, which PipeWire only repaints on damage) the latest
        frame may be the only one the encoder ever produces — a consuming
        read here (get_frame()) would leave the slot empty forever and the
        send loop would have nothing to send. Mirrors the non-destructive
        contract wait_for_frame_dimensions() gives the H.264 path.

        JPEG-only, same contract as get_frame(). Returns None if nothing has
        arrived yet.
        """
        if self.is_inter_frame:
            raise RuntimeError(
                "peek_frame() is JPEG-only — use wait_for_frame_dimensions() "
                "for the H.264 dimension probe"
            )
        data = self._sink_buffer.peek()
        if data is None:
            return None
        with self._lock:
            return (data, self._fw, self._fh)

    def wait_for_frame_dimensions(self, timeout: float):
        """Report the ACTUAL frame dimensions without consuming a frame.

        The H.264 pipeline may scale, so the caps dimensions can differ from
        the requested ones and the client must be told the real ones in the
        handshake. `_on_sample` caches them from the sample caps, so waiting
        for the first sample to be observed is enough — and it must be, since
        popping here would consume the first access unit (SPS/PPS/IDR) and
        permanently break decoder initialisation on the client.

        Returns (width, height), or None if no sample arrived in time.
        """
        if not self._first_sample.wait(timeout):
            return None
        with self._lock:
            return (self._fw, self._fh)

    def get_encoded_frame(self, timeout: float):
        """H.264 path: exactly one access unit, in order, never repeated."""
        frame = self._sink_buffer.get(timeout)
        if frame is None:
            return None
        with self._lock:
            return (frame.data, self._fw, self._fh)

    def _on_bus_error(self, _, message):
        err, _ = message.parse_error()
        log.warning("GStreamer pipeline error: %s — scheduling release", err)
        # Must NOT call set_state() from inside a GStreamer signal handler —
        # the state machine is not reentrant and will crash.  Schedule it on
        # the next GLib iteration so the signal handler returns first.
        GLib.idle_add(self._release_and_notify)

    def _on_bus_eos(self, *_):
        log.info("GStreamer EOS — scheduling release")
        GLib.idle_add(self._release_and_notify)

    def _release_and_notify(self):
        """Called via GLib.idle_add — safe to change GStreamer state here."""
        self._release_pipeline()
        if self._on_error:
            self._on_error()
        return False  # don't repeat

    def _release_pipeline(self):
        """Set pipeline to NULL to free the PipeWire node."""
        if not self._closed:
            self._closed = True
            self._pipeline.set_state(Gst.State.NULL)
            if self.is_inter_frame:
                # Wake any consumer blocked in get(), or shutdown hangs
                # until the timeout expires.
                self._sink_buffer.close()

    def close(self):
        self._release_pipeline()
        self._loop.quit()


# ── X11 capture (mss + XFixes cursor overlay) ─────────────────────────────────

class X11MssCapture:
    """
    Screen capture for X11 / Xvfb sessions using mss.
    Produces JPEG frames with cursor composited via mss's built-in XFixes support.
    Interface mirrors PipeWireCapture so the streaming loop is agnostic.

    monitor: mss monitor dict (left/top/width/height). May include a 'display'
             key (str) when targeting an Xvfb virtual display.

    mss stores its X display handle in thread-local storage, so the mss instance
    must be created and used in the same thread — that is done inside _capture_loop.
    """

    def __init__(self, width: int, height: int, fps: int, quality: int = 90,
                 on_error: Optional[Callable] = None,
                 monitor: Optional[dict] = None):
        self.width    = width
        self.height   = height
        self._fps     = fps
        self._quality = quality
        self._on_error = on_error
        self._monitor_cfg = monitor  # may include 'display' key for Xvfb
        self._frame: Optional[bytes] = None
        # The mss path always produces JPEG, so latest-wins is correct here.
        self.metrics        = StreamMetrics()
        self.is_inter_frame = False
        self._lock  = threading.Lock()
        self._stop  = threading.Event()
        self._mss   = None           # created inside capture thread

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self) -> None:
        import mss as _mss_lib
        from PIL import Image
        from io import BytesIO

        xvfb_display = (self._monitor_cfg or {}).get("display")
        _saved = None
        if xvfb_display:
            _saved = os.environ.get("DISPLAY")
            os.environ["DISPLAY"] = xvfb_display

        try:
            self._mss = _mss_lib.mss()
            if self._monitor_cfg:
                monitor = {k: v for k, v in self._monitor_cfg.items()
                           if k != "display"}
            else:
                monitor = self._mss.monitors[1]
            log.info("X11 capture: monitor %s", monitor)
        except Exception as e:
            log.warning("X11 mss init failed: %s", e)
            if self._on_error:
                self._on_error()
            return
        finally:
            if _saved is not None:
                os.environ["DISPLAY"] = _saved

        interval = 1.0 / self._fps
        try:
            while not self._stop.is_set():
                t = time.monotonic()
                img = self._mss.grab(monitor)
                pil = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                if pil.size != (self.width, self.height):
                    pil = pil.resize((self.width, self.height), Image.LANCZOS)
                pil = self._overlay_cursor(pil, monitor)
                buf = BytesIO()
                pil.save(buf, format="JPEG", quality=self._quality)
                with self._lock:
                    self._frame = buf.getvalue()
                elapsed = time.monotonic() - t
                sleep = interval - elapsed
                if sleep > 0:
                    time.sleep(sleep)
        except Exception as e:
            log.warning("X11 capture error: %s", e)
            if self._on_error:
                self._on_error()
        finally:
            if self._mss:
                try:
                    self._mss.close()
                except Exception:
                    pass

    def _overlay_cursor(self, pil_img: "Image", monitor: dict) -> "Image":
        """Composite the hardware cursor onto pil_img using mss XFixes support."""
        try:
            from PIL import Image as _PILImage
            cs = self._mss._cursor_impl()  # mss built-in XFixes cursor capture

            mon_left = monitor.get("left", 0)
            mon_top  = monitor.get("top",  0)
            mon_w    = monitor["width"]
            mon_h    = monitor["height"]

            # cs.left/top already account for the hotspot offset
            paste_x = cs.left - mon_left
            paste_y = cs.top  - mon_top

            # mss returns cursor pixels in BGRA format; convert to RGBA for PIL
            bgra = cs.raw
            rgba = bytearray(len(bgra))
            rgba[0::4] = bgra[2::4]  # R ← B slot
            rgba[1::4] = bgra[1::4]  # G
            rgba[2::4] = bgra[0::4]  # B ← R slot
            rgba[3::4] = bgra[3::4]  # A
            cursor_pil = _PILImage.frombytes("RGBA", (cs.width, cs.height), bytes(rgba))

            if pil_img.size != (mon_w, mon_h):
                sx = pil_img.width  / mon_w
                sy = pil_img.height / mon_h
                paste_x = int(paste_x * sx)
                paste_y = int(paste_y * sy)
                cursor_pil = cursor_pil.resize(
                    (max(1, int(cs.width * sx)), max(1, int(cs.height * sy))),
                    _PILImage.LANCZOS,
                )

            pil_img.paste(cursor_pil, (paste_x, paste_y), cursor_pil)
            return pil_img
        except Exception:
            return pil_img

    def set_quality(self, quality: int) -> None:
        self._quality = quality

    def set_orientation(self, portrait: bool) -> None:
        pass

    def get_frame(self):
        with self._lock:
            return (self._frame, self.width, self.height) if self._frame else None

    def peek_frame(self):
        """Mirrors get_frame(): this capture has no fresh/consumed tracking
        at all — `_frame` is just "whatever was last grabbed" — so reading it
        is already non-destructive. This method exists purely so callers on
        the shared intra-only path (resolve_stream_dimensions) can use one
        accessor name across both JPEG capture classes."""
        return self.get_frame()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)  # wait for mss to close before caller tears down display


def resolve_stream_dimensions(capture,
                              attempts: int = FRAME_WAIT_ATTEMPTS,
                              sleep_s: float = FRAME_WAIT_SLEEP_S,
                              sleep: Callable[[float], None] = time.sleep):
    """Discover the dimensions to advertise in the handshake.

    The pipeline may scale, so the encoded frames' real size can differ from
    the requested one. Each codec needs a different way to find that out:

    * inter-frame (H.264): wait until the first sample has been OBSERVED and
      read the cached caps dimensions. Nothing may be popped from the
      lossless queue here — the first access unit carries SPS/PPS/IDR and
      consuming it would permanently break decoder init on the client.
    * intra-only (JPEG): the slot is latest-value-wins, but polling it must
      still be non-consuming. On an idle capture (a static virtual display,
      which PipeWire only repaints on damage) the frame observed here may be
      the ONLY frame the encoder ever produces for the whole session — it is
      NOT superseded by anything. Popping it would leave the send loop with
      nothing to send and no way to ever bootstrap `last_sent_payload`,
      which is exactly the regression this function must not reintroduce.

    Falls back to the capture's configured dimensions if no frame arrives
    within `attempts` × `sleep_s`.
    """
    if capture.is_inter_frame:
        dims = capture.wait_for_frame_dimensions(attempts * sleep_s)
        if dims is not None:
            return dims
    else:
        for _ in range(attempts):
            r = capture.peek_frame()
            if r:
                _, w, h = r
                return (w, h)
            sleep(sleep_s)
    return (capture.width, capture.height)


# ── ServerCore ────────────────────────────────────────────────────────────────

class ServerCore:
    """
    Manages the full TethrLink server lifecycle.
    All public methods are thread-safe.
    on_log: callable(str) — called with log messages (use GLib.idle_add in UI context)
    """

    def __init__(self, config: ServerConfig, state: ServerState,
                 on_log: Callable[[str], None],
                 on_external_stop: Optional[Callable] = None):
        self._config   = config
        self._state    = state
        self._on_log   = on_log
        self._on_external_stop = on_external_stop  # called when GNOME indicator stops the cast
        self._display: Optional[MutterVirtualDisplay] = None
        self._capture: Optional[PipeWireCapture]      = None
        self._conn:    Optional[socket.socket]        = None  # active client socket
        self._input_negotiated: bool = False  # set per-connection in
                                               # _handle_client; whether this
                                               # client both advertised input
                                               # support and had it accepted
        self._broadcaster: Optional[DiscoveryBroadcaster] = None
        self._srv: Optional[socket.socket]            = None
        self._bus                                     = None
        self._shutdown = threading.Event()
        self._accept_thread: Optional[threading.Thread] = None
        self._client_lock = threading.Lock()
        self._live_fps          = config.fps
        self._live_quality      = config.quality
        log_gstreamer_preflight()

    def _log(self, msg: str) -> None:
        log.info(msg)
        self._on_log(msg)

    @property
    def running(self) -> bool:
        """True if the server is currently running."""
        return self._state.running

    def start(self) -> None:
        """Start server. Raises RuntimeError on setup failure."""
        self._shutdown.clear()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SessionBus()

        try:
            self._srv, actual_port = bind_server_socket(self._config.port)
        except OSError as e:
            self._log(f"Could not bind port: {e}")
            raise RuntimeError(str(e))

        self._broadcaster = DiscoveryBroadcaster(actual_port, 0, 0)
        self._broadcaster.start()

        # Subscribe to display config changes so we pick up the new resolution
        # the next time a client connects (after the user rearranges monitors).
        try:
            dc_proxy = self._bus.get_object(
                "org.gnome.Mutter.DisplayConfig",
                "/org/gnome/Mutter/DisplayConfig",
            )
            dc_proxy.connect_to_signal(
                "MonitorsChanged",
                self._on_monitors_changed,
                dbus_interface="org.gnome.Mutter.DisplayConfig",
            )
        except Exception as e:
            log.debug("Could not subscribe to MonitorsChanged: %s", e)

        self._state.update(running=True, port=actual_port)
        self._log(f"Waiting for device on port {actual_port}…")

        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True
        )
        self._accept_thread.start()

    def stop(self) -> None:
        """Stop server and clean up. Blocks until accept loop exits."""
        self._shutdown.set()
        if self._accept_thread and self._accept_thread.is_alive():
            self._accept_thread.join(timeout=3)
        if self._broadcaster:
            self._broadcaster.stop()
            self._broadcaster = None
        if self._srv:
            self._srv.close()
            self._srv = None
        # Display and capture are torn down by _handle_client's finally block
        # when the client disconnects. If stop() is called mid-stream, the
        # shutdown event causes the stream loop to exit and finally runs.
        # Nothing to do here for display/capture.
        self._state.update(running=False, connected=False,
                           client_name="", fps=0)
        self._log("Server stopped")

    def set_quality(self, quality: int) -> None:
        """Hot-reload JPEG quality via jpegenc element property (no pipeline restart)."""
        self._live_quality = quality
        self._config.quality = quality
        if self._capture:
            self._capture.set_quality(quality)

    def set_orientation(self, orientation: str) -> None:
        """Hot-reload orientation via videoflip. No pipeline restart needed."""
        self._config.orientation = orientation
        if self._capture:
            self._capture.set_orientation(orientation == "portrait")

    def _on_monitors_changed(self):
        """
        Fires when GNOME Display Settings applies a new monitor configuration.
        We do NOT release the stream here — for layout-only changes (e.g. mirror,
        position) Mutter doesn't destroy the PipeWire node, so the stream survives
        and the user's GNOME layout choice is preserved. If Mutter does destroy the
        node (e.g. resolution change), GStreamer's error/EOS handler cleans up.
        """
        if not self._bus or not self._state.running:
            return
        if self._config.width == 0 and self._config.height == 0:
            detected = detect_primary_resolution_mutter(self._bus)
            if detected:
                self._log(f"Monitor config updated — next stream: {detected[0]}×{detected[1]}")


    def _accept_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                conn, addr = self._srv.accept()
                if not is_tether_peer(addr[0]):
                    log.warning("Rejected connection from non-tether peer %s", addr[0])
                    conn.close()
                    continue
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, conn: socket.socket, _: tuple) -> None:
        conn.settimeout(HANDSHAKE_TIMEOUT_S)
        try:
            hello = conn.recv(6 + 16 + 8 + 64)
        except socket.timeout:
            conn.close()
            return

        if len(hello) < 30 or hello[:6] != MAGIC_HELLO:
            conn.close()
            return

        device_id          = hello[6:22].hex()
        screen_w, screen_h = struct.unpack(">II", hello[22:30])
        device_name        = (
            hello[30:].decode("utf-8", errors="replace").strip("\x00")[:64]
            or f"Android-{device_id[:8]}"
        )
        self._log(f"Incoming: {device_name} — screen {screen_w}×{screen_h}")

        if not self._client_lock.acquire(blocking=False):
            conn.sendall(MAGIC_BUSY)
            conn.close()
            return

        self._conn = conn  # store so _on_session_closed can abort the stream

        # ── Optional input-capability extension ─────────────────────────────
        # See the EXT_MARKER block comment near the top of this file for the
        # wire layout. Absence is the common case today (every released
        # client is legacy) and must be entirely non-fatal: a timeout here
        # just means "no extension", never a dropped connection.
        client_wants_input = False
        conn.settimeout(EXTENSION_READ_TIMEOUT_S)
        try:
            ext_header = conn.recv(EXT_HEADER_LEN)
        except (socket.timeout, OSError):
            ext_header = b""

        # Extract capability byte (if present) to determine if client wants input.
        cap_payload = b""
        if len(ext_header) == EXT_HEADER_LEN and ext_header[:len(EXT_MARKER)] == EXT_MARKER:
            cap_len = ext_header[len(EXT_MARKER)]
            if cap_len > 0:
                # Read exactly the length the client declared — never more,
                # never fewer — so nothing is left on the wire for the input
                # reader (Task 5) to misinterpret as the first message.
                try:
                    cap_payload = conn.recv(cap_len)
                except (socket.timeout, OSError):
                    cap_payload = b""

        # Use the pure function to parse the extension safely.
        client_wants_input = parse_input_extension(ext_header, cap_payload)
        conn.settimeout(HANDSHAKE_TIMEOUT_S)  # restore for the rest of the handshake

        # `touch_enabled` does not exist on ServerConfig yet (a later task
        # adds the field and its UI toggle) — default to False, which is the
        # deliberate ship default, so this negotiation works standalone.
        touch_enabled = getattr(self._config, "touch_enabled", False)
        self._input_negotiated = client_wants_input and touch_enabled

        # Explicit per-session stop signal. Capture teardown (pipeline error,
        # display reconfiguration) used to be signalled only indirectly, by
        # shutting the socket down and relying on the send loop's next
        # sendall() raising. That depended on there always being a frame to
        # send — true only of the old stale-frame slot. Both current buffers
        # correctly return None once drained/closed, so with no frames left
        # sendall() would never be reached, the loop would never raise, and
        # _client_lock would be held until server shutdown, answering every
        # reconnect with MAGIC_BUSY. The send loop now checks this event.
        session_stop = threading.Event()
        # Separate from session_stop: this one only ever tells the input
        # reader thread (Task 5, started below once the handshake reply is
        # sent) to stop, so the send loop's own stop condition is never
        # coupled to input's lifecycle by accident.
        input_stop = threading.Event()

        # ── Save device dims: on the H.264 path these now drive the capture
        # resolution itself (see resolve_capture_size below), not just the UI
        # canvas. JPEG deliberately still ignores them — see below for why. ──
        if screen_w > 0 and screen_h > 0:
            self._config.device_width  = screen_w
            self._config.device_height = screen_h

        # ── Resolve capture resolution ────────────────────────────────────────
        # The two codec paths deliberately resolve this differently.
        #
        # H.264 matches the connected device's own dimensions — capture,
        # encode, decode and render then all agree on one size and nothing is
        # rescaled. Inter-frame compression is what makes the extra pixels
        # affordable: only what changed since the last frame is transmitted.
        #
        # JPEG keeps its previous behaviour: explicit config override, else the
        # PC's primary monitor. Every JPEG frame is whole and independent —
        # there is no inter-frame compression at all — so resolution costs it
        # far more. Feeding it the device's size would take 1920×1080 to
        # 2960×1848: 2.6x the pixels through jpegenc and 2.6x the bytes on the
        # wire, every single frame. Measured JPEG throughput at 1920×1080 was
        # already only ~20 fps and the larger size has never been measured on
        # hardware. JPEG is the shipping default, so it does not get
        # device-matched geometry until someone has measured what it costs.
        session_type = detect_session_type()
        codec = self._config.codec
        # The X11 branch below captures with mss, which always produces JPEG
        # regardless of the configured codec (it reassigns `codec` after
        # building the capture). So H.264 only ever runs on Wayland, and the
        # device-matched geometry must be gated on that too.
        encodes_h264 = (codec == CODEC_H264 and session_type == "wayland")

        mon_w, mon_h = resolve_resolution(self._bus, 0, 0)
        if encodes_h264:
            width, height = resolve_capture_size(
                device_w=screen_w, device_h=screen_h,
                config_w=self._config.width, config_h=self._config.height,
                monitor_w=mon_w, monitor_h=mon_h,
                orientation=self._config.orientation,
            )
            if self._config.width > 0 and self._config.height > 0:
                self._log(f"Using configured resolution: {width}×{height}")
            elif is_plausible_size(screen_w, screen_h):
                self._log(f"Matching device resolution: {width}×{height}")
            else:
                self._log(f"Using primary monitor resolution: {width}×{height}")
        elif self._config.width > 0 and self._config.height > 0:
            width, height = self._config.width, self._config.height
            self._log(f"Using configured resolution: {width}×{height}")
        else:
            width, height = mon_w, mon_h
            self._log(f"Using primary monitor resolution: {width}×{height}")
        self._state.update_direct(active_width=width, active_height=height)

        if self._config.orientation == "portrait" and width > height:
            width, height = height, width

        self._log(f"Starting — {width}×{height} @ {self._live_fps} FPS "
                  f"({'H.264' if codec == CODEC_H264 else 'JPEG'})")

        # ── Remember this device ────────────────────────────────────────────
        # Keyed by the 16-byte device id the handshake already carries — no
        # protocol change. Recorded here, after resolution is resolved and
        # logged, so the stored record reflects what was actually used rather
        # than what was requested. This is a convenience, never a
        # precondition for streaming: no failure here may stop a client from
        # connecting.
        try:
            store = ProfileStore()
            store.load()
            known = store.get_device(device_id)
            # Treat a wrongly-typed count as absent rather than adding to it.
            # Arithmetic on a string or null would raise, and because the raise
            # would land before set_device(), the bad record would never be
            # overwritten — the failure would repeat on every connection
            # instead of healing itself on the next one.
            previous = (known or {}).get("connections")
            store.set_device(device_id, {
                "name": device_name,
                "width": screen_w,
                "height": screen_h,
                "last_capture_width": width,
                "last_capture_height": height,
                "connections": (previous if isinstance(previous, int) else 0) + 1,
            })
            # Only claim it was saved if it actually was: save() reports
            # failure rather than raising, so on a read-only cache directory
            # this would otherwise announce a first connection every time.
            if store.save() and known is None:
                self._log(f"First connection from {device_name} — profile saved")
        except Exception as e:
            # Recording a profile is a convenience, never a precondition for
            # streaming.
            log.debug("Could not record device profile: %s", e)

        # ── Set up capture (Wayland: Mutter virtual display, X11: mss) ─────────
        display = None
        capture = None
        remote_input = None            # set below when input is negotiated
                                        # and pairing succeeds (Task 5)
        input_reader_thread = None
        self._log(f"Session type: {session_type.upper()}")
        try:
            if session_type == "wayland":
                remote_input_session_id = None
                if self._input_negotiated:
                    candidate = RemoteInput(
                        width, height, self._bus,
                        frame_interval_s=1.0 / self._live_fps,
                    )
                    candidate_session_id = candidate.create()
                    if candidate_session_id is not None:
                        remote_input = candidate
                        remote_input_session_id = candidate_session_id
                    # else: create() already logged why — continue
                    # video-only, same as every other "no input this
                    # session" outcome remote_input.py documents as a
                    # normal, supported result, not an error.

                display = MutterVirtualDisplay(
                    width, height, self._bus,
                    remote_input=remote_input,
                    remote_input_session_id=remote_input_session_id,
                )
                try:
                    node_id = display.setup()
                except Exception as e:
                    if remote_input is not None:
                        # Mutter's own coupling means a paired session's
                        # Start() failure takes the ScreenCast side down
                        # with it (verified live — see MutterVirtualDisplay's
                        # docstring note). Video is the product; input is
                        # an addition — retry once, unpaired, before
                        # failing the whole connection over what may be an
                        # input-only problem.
                        log.warning(
                            "Virtual display setup failed with input "
                            "paired (%s) — retrying video-only", e,
                        )
                        try:
                            remote_input.stop()
                        except Exception:
                            pass
                        remote_input = None
                        display.close()
                        display = MutterVirtualDisplay(width, height, self._bus)
                        try:
                            node_id = display.setup()
                        except Exception as e2:
                            self._log(f"Virtual display failed: {e2}")
                            display.close()
                            self._client_lock.release()
                            conn.close()
                            return
                    else:
                        self._log(f"Virtual display failed: {e}")
                        display.close()
                        self._client_lock.release()
                        conn.close()
                        return

                self._log("Virtual display ready — arrange windows using System Settings > Displays")

                def _on_session_closed():
                    GLib.idle_add(_do_session_cleanup)

                def _do_session_cleanup():
                    session_stop.set()
                    if self._capture:
                        try:
                            self._capture.close()
                        except Exception:
                            pass
                        self._capture = None
                    if self._conn:
                        try:
                            self._conn.shutdown(socket.SHUT_RDWR)
                        except Exception:
                            pass
                    self._log("Display config changed — stream stopped, waiting for reconnect")
                    return False
                display.on_closed = _on_session_closed
                self._display = display

                def _on_capture_error():
                    self._log("PipeWire stream ended — display config likely changed")
                    session_stop.set()
                    if self._conn:
                        try:
                            self._conn.shutdown(socket.SHUT_RDWR)
                        except Exception:
                            pass

                enc_spec = None
                if codec == CODEC_H264:
                    bitrate_kbps = (
                        self._config.bitrate
                        if self._config.bitrate > 0
                        else default_bitrate_kbps(width, height, self._live_fps)
                    )
                    bitrate_source = (
                        "explicitly configured" if self._config.bitrate > 0
                        else "derived from resolution/fps"
                    )
                    enc_spec = select_encoder(
                        EncoderConfig(
                            bitrate_kbps=bitrate_kbps,
                            gop_length=self._live_fps,
                            rate_control=RateControl.CBR,
                        ),
                        width, height,
                    )
                    # Logged AFTER selection, and describing the encoder that
                    # was actually chosen. This used to log the requested
                    # bitrate before select_encoder() ran, so it reported a
                    # number the chosen encoder might never receive — actively
                    # misleading on any encoder that degraded to CQP (which has
                    # no bitrate target at all) or when no encoder was usable
                    # and the hardcoded x264 fallback took over.
                    self._log(describe_h264_encoding(
                        enc_spec, bitrate_kbps, bitrate_source))

                capture = PipeWireCapture(
                    node_id, width, height, codec,
                    self._live_fps, self._config.bitrate, self._config.h264_width,
                    self._live_quality,
                    flip_orientation=(self._config.orientation == "portrait"),
                    on_error=_on_capture_error,
                    encoder_spec=enc_spec,
                )
            else:
                # X11: try xrandr VIRTUAL output only (no Xvfb — it starts blank
                # and its termination triggers a fatal Xlib I/O error).
                # If no VIRTUAL output is available, mirror the primary screen.
                x11_virt = X11VirtualDisplay(width, height)
                mon = x11_virt._try_xrandr_virtual()
                if mon is not None:
                    display = x11_virt
                    self._log(f"X11 virtual display ready ({width}×{height}) — "
                              "drag windows to the second monitor")
                else:
                    x11_virt.close()
                    display = None
                    self._log("X11: mirroring primary screen (cursor overlaid automatically)")
                    self._log("Arrange your windows, then connect the device.")

                def _on_capture_error_x11():
                    self._log("X11 capture error — stream stopped")
                    session_stop.set()
                    if self._conn:
                        try:
                            self._conn.shutdown(socket.SHUT_RDWR)
                        except Exception:
                            pass

                capture = X11MssCapture(
                    width, height,
                    fps=self._live_fps,
                    quality=self._live_quality,
                    on_error=_on_capture_error_x11,
                    monitor=mon,
                )
                codec = CODEC_JPEG  # mss path always produces JPEG

            self._capture = capture
            time.sleep(CAPTURE_INIT_SLEEP_S)

            self._state.update(
                resolution=f"{width}×{height}",
                codec_name="H.264" if codec == CODEC_H264 else "JPEG",
            )


            # ── Stream ────────────────────────────────────────────────────────
            # Use a short timeout so sendall() raises OSError within ~5s when
            # USB is unplugged (TCP retransmissions would otherwise hold the
            # _client_lock for minutes before the kernel gives up).
            conn.settimeout(5.0)
            # Keepalives catch dead connections during idle stretches (no frames).
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 2)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            w, h = resolve_stream_dimensions(capture)

            # Only the magic differs — the (w, h, codec) struct that follows
            # is byte-identical either way, since the Android client reads
            # it at a fixed offset regardless of which magic preceded it.
            reply_magic = MAGIC_OK2 if self._input_negotiated else MAGIC_OK
            conn.sendall(reply_magic + struct.pack(">IIB", w, h, codec))
            self._log(f"Streaming → {device_name}"
                      + (" (input enabled)" if self._input_negotiated else ""))

            # ── Start the input reader (Task 5) ─────────────────────────────
            # Only when input was negotiated AND a RemoteInput session
            # actually exists — pairing may have failed and fallen back to
            # video-only above, in which case remote_input is None here and
            # no reader starts. See run_input_reader()'s docstring for why
            # this never touches conn's timeout, which the send loop below
            # also relies on.
            if self._input_negotiated and remote_input is not None:
                input_reader_thread = threading.Thread(
                    target=run_input_reader,
                    args=(conn, remote_input, capture.metrics, input_stop.is_set),
                    daemon=True,
                    name="tethrlink-input-reader",
                )
                input_reader_thread.start()
                log.info("Input reader started for %s", device_name)
            if capture.is_inter_frame:
                # The H.264 framerate lives in the pipeline caps, fixed when
                # the pipeline was built, and the send loop is now driven by
                # the encoder rather than by a sleep — so changing the live
                # FPS setting cannot take effect on an open H.264 session.
                self._log(
                    f"H.264 framerate is fixed at {self._live_fps} FPS by the "
                    "pipeline — live FPS changes apply after reconnecting"
                )
            self._state.update(connected=True, client_name=device_name,
                               active_width=width, active_height=height,
                               device_width=self._config.device_width,
                               device_height=self._config.device_height)

            frame_count  = 0
            fps_deadline = time.monotonic() + 1.0

            # Idle keepalive bookkeeping. `last_sent_monotonic` starts at "now"
            # (stream start), not at zero, so a session that goes idle
            # immediately still gets a full keepalive interval of grace before
            # the first keepalive action — matching the grace any frame send
            # would reset it to. `last_sent_payload` stays None until the
            # first real frame is actually written to the socket, which is
            # what keeps the JPEG keepalive from ever resending nothing.
            last_sent_monotonic = time.monotonic()
            last_sent_payload: Optional[bytes] = None
            # Counts idle IDR retransmissions on the H.264 path, purely so the
            # log can carry an occasional summary instead of a line per second.
            idle_keepalives = 0

            while not self._shutdown.is_set() and not session_stop.is_set():
                if capture.is_inter_frame:
                    # The encoder's framerate cap is the single rate authority.
                    # Blocking here means one clock instead of two; the old
                    # sleep-pacing raced the encoder and corrupted the stream.
                    # The timeout is tied to KEEPALIVE_INTERVAL_S so an idle
                    # stream is guaranteed to reach the keepalive check below
                    # roughly once per interval without any extra sleep.
                    r = capture.get_encoded_frame(timeout=KEEPALIVE_INTERVAL_S)
                    if r:
                        raw, _, _ = r
                        conn.sendall(struct.pack(">I", len(raw)) + raw)
                        capture.metrics.incr("frames_sent")
                        frame_count += 1
                        last_sent_monotonic = time.monotonic()
                        idle_keepalives = 0
                    elif time.monotonic() - last_sent_monotonic >= KEEPALIVE_INTERVAL_S:
                        # PipeWire delivers frames only on damage, so a virtual
                        # display that never repaints leaves pipewiresrc silent
                        # and the encoder with nothing to encode. Asking for a
                        # keyframe here is useless on its own — you cannot
                        # encode a frame that does not exist — which is why the
                        # old request-only keepalive let the stream die and the
                        # client reconnect-storm.
                        #
                        # The compositor upstream now supplies a constant rate,
                        # so this should rarely fire. It remains the safety net
                        # for the case where that path stalls anyway.
                        #
                        # Retransmit the last cached IDR instead. That is safe
                        # by H.264 semantics in a way retransmitting a delta
                        # frame is not: an instantaneous decoder refresh resets
                        # the decoder's reference state by definition, and the
                        # picture is the one already on screen, so it is a
                        # visual no-op that satisfies the client's read timeout.
                        cached = capture.last_keyframe()
                        if cached is not None:
                            conn.sendall(struct.pack(">I", len(cached)) + cached)
                            capture.metrics.incr("frames_sent")
                            frame_count += 1
                            last_sent_monotonic = time.monotonic()
                            idle_keepalives += 1
                            # Correctness, not belt-and-braces: the decoder now
                            # sits at "just decoded that IDR", while the
                            # encoder's own reference state is wherever it
                            # actually left off — so the first delta frame to
                            # arrive when the display wakes up would reference a
                            # picture the decoder no longer holds. Arm the same
                            # gate the overflow path uses so such frames are
                            # rejected, and ask for a fresh IDR so the gate
                            # clears promptly once frames resume.
                            capture.require_keyframe_gate()
                            capture.request_keyframe(reason="idle keepalive")
                            log.debug(
                                "Idle source — retransmitted cached IDR "
                                "(%d bytes, %d consecutive)",
                                len(cached), idle_keepalives,
                            )
                            if idle_keepalives % IDLE_KEEPALIVE_LOG_EVERY == 0:
                                self._log(
                                    f"Source idle — holding the link with "
                                    f"retransmitted keyframes "
                                    f"({idle_keepalives} so far)"
                                )
                        else:
                            # Nothing has ever been encoded on this session, so
                            # there is nothing to retransmit; requesting a
                            # keyframe is the only available action. Retried
                            # every interval — the old once-only guard is
                            # precisely what made the stall permanent.
                            # No timer reset: nothing was sent, so
                            # `last_sent_monotonic` must keep meaning what it
                            # says. The blocking get_encoded_frame() above
                            # already paces this branch to roughly one attempt
                            # per KEEPALIVE_INTERVAL_S.
                            capture.request_keyframe(reason="no frame yet")
                else:
                    start = time.monotonic()
                    r = capture.get_frame()
                    if r:
                        raw, _, _ = r
                        conn.sendall(struct.pack(">I", len(raw)) + raw)
                        capture.metrics.incr("frames_sent")
                        frame_count += 1
                        last_sent_monotonic = start
                        last_sent_payload = raw
                    elif (
                        last_sent_payload is not None
                        and start - last_sent_monotonic >= KEEPALIVE_INTERVAL_S
                    ):
                        # JPEG frames are independently decodable, so
                        # retransmitting the last one sent is harmless — this
                        # is the deliberate, throttled reintroduction of the
                        # retransmission LatestFrameSlot otherwise eliminates.
                        conn.sendall(
                            struct.pack(">I", len(last_sent_payload)) + last_sent_payload
                        )
                        capture.metrics.incr("frames_sent")
                        frame_count += 1
                        last_sent_monotonic = start
                    elif (
                        last_sent_payload is None
                        and start - last_sent_monotonic >= KEEPALIVE_INTERVAL_S
                    ):
                        # Defence in depth: nothing has ever been sent on this
                        # connection, but a frame may already be sitting in
                        # the slot (e.g. left there by the non-consuming
                        # handshake dimension probe on an idle capture that
                        # never produces a second frame). Without this, the
                        # keepalive above could never bootstrap — it only
                        # fires once last_sent_payload is set — and an idle
                        # session would send literally nothing until the
                        # client's read timeout fires and it reconnect-storms.
                        # Use the non-consuming peek_frame(), not get_frame():
                        # this is a best-effort bootstrap, not the normal
                        # frame path, so it must not race/steal from it.
                        boot = capture.peek_frame()
                        if boot:
                            raw, _, _ = boot
                            conn.sendall(
                                struct.pack(">I", len(raw)) + raw
                            )
                            capture.metrics.incr("frames_sent")
                            frame_count += 1
                            last_sent_monotonic = start
                            last_sent_payload = raw
                    elapsed = time.monotonic() - start
                    sleep   = (1.0 / self._live_fps) - elapsed
                    if sleep > 0:
                        time.sleep(sleep)

                if time.monotonic() >= fps_deadline:
                    snap = capture.metrics.snapshot()
                    self._state.update(fps=frame_count)
                    log.info(
                        "fps=%d encoded=%d sent=%d dropped=%d dup_suppressed=%d "
                        "overflows=%d keyframe_reqs=%d",
                        frame_count,
                        snap["frames_encoded"], snap["frames_sent"],
                        snap["frames_dropped"], snap["duplicates_suppressed"],
                        snap["queue_overflows"], snap["keyframe_requests"],
                    )
                    frame_count  = 0
                    fps_deadline = time.monotonic() + 1.0

            if session_stop.is_set():
                # Distinct from "Client disconnected": the socket may still be
                # perfectly healthy here — it is the capture that went away.
                self._log(f"Capture stopped — ending session with {device_name}")

        except (BrokenPipeError, ConnectionResetError, OSError):
            self._log(f"Client disconnected: {device_name}")
        except Exception as e:
            self._log(f"Stream error ({device_name}): {e}")
        finally:
            self._conn = None
            self._state.update(connected=False, client_name="", fps=0)
            # Signal the input reader (if one was started) to stop and give
            # it a bounded window to actually exit — its own finally clause
            # calls RemoteInput.release_all(), and running that before
            # display.close() tears the paired session down is what
            # guarantees a button is never left held on any exit path from
            # this method.
            input_stop.set()
            if input_reader_thread is not None:
                input_reader_thread.join(timeout=2.0)
            if capture and self._capture is not None:
                # _on_session_closed may have already closed this — guard against double-close
                try:
                    capture.close()
                except Exception:
                    pass
                self._capture = None
            if display:
                display.close()
                self._display = None
            # Mandatory cooldown to prevent Mutter crashes on immediate reconnect
            time.sleep(1.0)
            self._client_lock.release()
            conn.close()
