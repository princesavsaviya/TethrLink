"""
TetherLink Server - v0.9.5
Wayland + PipeWire virtual display with H.264 / JPEG encoding.

Usage:
    ./server/run_server.sh
    ./server/run_server.sh --fps 60 --quality 90
    ./server/run_server.sh --codec jpeg
    ./server/run_server.sh --codec h264
    ./server/run_server.sh --width 1920 --height 1080
    ./server/run_server.sh --port 51137
"""

import argparse
import logging
import os
import socket
import struct
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path

import dbus
import dbus.mainloop.glib
import gi
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst
from PIL import Image
from tray import TrayState, start_tray
from discovery import DiscoveryBroadcaster

# ── Version ───────────────────────────────────────────────────────────────────
VERSION = "0.9.5"

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="TetherLink Server")
parser.add_argument("--width",      type=int, default=0,
                    help="Virtual display width (default: auto-detect from primary monitor)")
parser.add_argument("--height",     type=int, default=0,
                    help="Virtual display height (default: auto-detect from primary monitor)")
parser.add_argument("--fps",        type=int, default=60)
parser.add_argument("--quality",    type=int, default=90)
parser.add_argument("--port",       type=int, default=51137,
                    help="Stream port (default 51137, auto-scans up if taken)")
parser.add_argument("--codec",      type=str, default="jpeg",
                    choices=["auto", "h264", "jpeg"])
parser.add_argument("--bitrate",    type=int, default=3000,
                    help="H.264 bitrate in kbps (default 3000)")
parser.add_argument("--h264-width", type=int, default=1280,
                    help="H.264 encode width in pixels (default 1280)")
args = parser.parse_args()

FPS            = args.fps
JPEG_QUALITY   = args.quality
FRAME_INTERVAL = 1.0 / FPS
H264_BITRATE   = args.bitrate
H264_WIDTH     = args.h264_width

# ── Timing constants ──────────────────────────────────────────────────────────
HANDSHAKE_TIMEOUT_S     = 10.0
FRAME_WAIT_ATTEMPTS     = 100       # × FRAME_WAIT_SLEEP_S = 5s max wait
FRAME_WAIT_SLEEP_S      = 0.05
CAPTURE_INIT_SLEEP_S    = 0.5       # settle time after GStreamer starts
MUTTER_SETUP_TIMEOUT_MS = 10_000

# ── Network constants ─────────────────────────────────────────────────────────
PORT_SCAN_RANGE     = 10            # try default .. default+9
APPSINK_MAX_BUFFERS = 2

CODEC_H264 = 1
CODEC_JPEG = 2
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TetherLink")


# ── Resolution detection ──────────────────────────────────────────────────────

def detect_primary_resolution_mutter(bus) -> tuple[int, int] | None:
    """
    Query GNOME Mutter DisplayConfig over D-Bus for the primary monitor's
    current resolution. Most reliable method on GNOME Wayland.
    """
    try:
        proxy = bus.get_object(
            "org.gnome.Mutter.DisplayConfig",
            "/org/gnome/Mutter/DisplayConfig"
        )
        iface = dbus.Interface(proxy, "org.gnome.Mutter.DisplayConfig")
        _serial, monitors, logical_monitors, _props = iface.GetCurrentState()

        # Find the primary logical monitor
        for lm in logical_monitors:
            # lm layout: (x, y, scale, transform, is_primary, monitors, props)
            is_primary = bool(lm[4])
            if not is_primary:
                continue
            monitor_connector = lm[5][0][0]
            for monitor in monitors:
                if monitor[0][0] != monitor_connector:
                    continue
                for mode in monitor[1]:
                    # mode layout: (id, width, height, refresh, preferred_scale,
                    #               supported_scales, props)
                    if mode[6].get("is-current", False):
                        w, h = int(mode[1]), int(mode[2])
                        log.info("Detected primary monitor: %dx%d (Mutter DisplayConfig)", w, h)
                        return w, h
    except Exception as e:
        log.debug("Mutter DisplayConfig detection failed: %s", e)
    return None


def detect_primary_resolution_xrandr() -> tuple[int, int] | None:
    """
    Fallback: parse xrandr output for the primary (or first connected)
    display resolution. Works in XWayland environments.
    """
    try:
        result = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=3
        )
        lines = result.stdout.splitlines()
        # Prefer line with "connected primary", fall back to first "connected"
        candidates = [l for l in lines if " connected primary" in l]
        if not candidates:
            candidates = [l for l in lines if " connected" in l]
        for line in candidates:
            for part in line.split():
                if "x" in part and "+" in part:
                    try:
                        w, h = part.split("+")[0].split("x")
                        log.info("Detected primary monitor: %dx%d (xrandr)", int(w), int(h))
                        return int(w), int(h)
                    except ValueError:
                        continue
    except Exception as e:
        log.debug("xrandr detection failed: %s", e)
    return None


FALLBACK_WIDTH  = 1920
FALLBACK_HEIGHT = 1080

def resolve_resolution(bus, requested_w: int, requested_h: int) -> tuple[int, int]:
    """
    Return the display resolution to use for the virtual monitor.
    Priority:
      1. --width / --height CLI args (both must be non-zero to override)
      2. Mutter DisplayConfig D-Bus query
      3. xrandr fallback
      4. Hardcoded fallback (1920x1080)
    """
    if requested_w > 0 and requested_h > 0:
        log.info("Resolution: %dx%d (from CLI args)", requested_w, requested_h)
        return requested_w, requested_h

    detected = detect_primary_resolution_mutter(bus)
    if detected:
        return detected

    detected = detect_primary_resolution_xrandr()
    if detected:
        return detected

    log.warning(
        "Could not detect display resolution — "
        "falling back to %dx%d. Use --width/--height to override.",
        FALLBACK_WIDTH, FALLBACK_HEIGHT
    )
    return FALLBACK_WIDTH, FALLBACK_HEIGHT




def bind_server_socket(preferred_port: int) -> tuple[socket.socket, int]:
    """
    Try to bind to preferred_port. If taken, scan upward by PORT_SCAN_RANGE.
    Returns (bound_socket, actual_port).
    Raises OSError if no port in range is available.
    """
    for port in range(preferred_port, preferred_port + PORT_SCAN_RANGE):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.listen(5)
            srv.settimeout(1.0)
            if port != preferred_port:
                log.warning(
                    "Port %d was taken — bound to %d instead", preferred_port, port
                )
            else:
                log.info("Server ready on port %d", port)
            return srv, port
        except OSError:
            log.debug("Port %d unavailable, trying next…", port)
            try:
                srv.close()
            except Exception:
                pass
    raise OSError(
        f"No available port in range {preferred_port}–"
        f"{preferred_port + PORT_SCAN_RANGE - 1}"
    )


# ── Codec detection ───────────────────────────────────────────────────────────

def detect_codec() -> int:
    if args.codec == "jpeg":
        log.info("Codec: JPEG (forced)")
        return CODEC_JPEG
    if args.codec == "h264":
        log.info("Codec: H.264 (forced)")
        return CODEC_H264
    result = subprocess.run(
        ["gst-inspect-1.0", "x264enc"],
        capture_output=True, text=True
    )
    if "x264 H.264 Encoder" in result.stdout:
        log.info("Codec: H.264 (auto-detected)")
        return CODEC_H264
    log.info("Codec: JPEG (x264enc not found)")
    return CODEC_JPEG


# ── Handshake ─────────────────────────────────────────────────────────────────

MAGIC_HELLO = b"TLHELO"
MAGIC_OK    = b"TLOK__"
MAGIC_BUSY  = b"TLBUSY"

_active_client_lock = threading.Lock()


def accept_client(conn, addr) -> tuple[bool, str, int, int]:
    """
    Simple handshake — reads device info, enforces one-connection limit.
    Returns (accepted, device_name, screen_w, screen_h).
    """
    conn.settimeout(HANDSHAKE_TIMEOUT_S)
    try:
        hello = conn.recv(6 + 16 + 8 + 64)
    except socket.timeout:
        return False, "", 0, 0

    if len(hello) < 30 or hello[:6] != MAGIC_HELLO:
        conn.close()
        return False, "", 0, 0

    device_id          = hello[6:22].hex()
    screen_w, screen_h = struct.unpack(">II", hello[22:30])
    device_name        = (
        hello[30:].decode("utf-8", errors="replace").strip("\x00")[:64]
        or f"Android-{device_id[:8]}"
    )
    log.info("Incoming connection: %s — screen %dx%d", device_name, screen_w, screen_h)

    if not _active_client_lock.acquire(blocking=False):
        conn.sendall(MAGIC_BUSY)
        conn.close()
        return False, "", 0, 0

    conn.settimeout(None)
    return True, device_name, screen_w, screen_h


# ── Mutter ScreenCast ─────────────────────────────────────────────────────────

MUTTER_BUS    = "org.gnome.Mutter.ScreenCast"
MUTTER_PATH   = "/org/gnome/Mutter/ScreenCast"
MUTTER_SC_IF  = "org.gnome.Mutter.ScreenCast"
MUTTER_SES_IF = "org.gnome.Mutter.ScreenCast.Session"
MUTTER_STR_IF = "org.gnome.Mutter.ScreenCast.Stream"


def cleanup_orphaned_sessions(bus):
    try:
        sc_obj = bus.get_object(MUTTER_BUS, MUTTER_PATH)
        intro  = dbus.Interface(sc_obj, "org.freedesktop.DBus.Introspectable")
        import xml.etree.ElementTree as ET
        root = ET.fromstring(intro.Introspect())
        for node in root.findall("node"):
            name = node.get("name", "")
            if name:
                try:
                    obj = bus.get_object(MUTTER_BUS, f"{MUTTER_PATH}/{name}")
                    dbus.Interface(obj, MUTTER_SES_IF).Stop()
                    log.info("Cleaned orphaned session: %s", name)
                except Exception:
                    pass
    except Exception as e:
        log.debug("Session cleanup: %s", e)


class MutterVirtualDisplay:
    def __init__(self, width: int, height: int, bus=None):
        self.width    = width
        self.height   = height
        self._node_id = None
        self._error   = None
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        # Reuse the bus passed in from run_server (already initialised for
        # resolution detection) — avoids creating a second DBus main loop.
        self._bus  = bus or dbus.SessionBus()
        self._loop = GLib.MainLoop()
        cleanup_orphaned_sessions(self._bus)
        sc_obj    = self._bus.get_object(MUTTER_BUS, MUTTER_PATH)
        self._sc  = dbus.Interface(sc_obj, MUTTER_SC_IF)
        self._session_path = None

    def setup(self) -> int:
        log.info("Creating Mutter ScreenCast session...")
        self._session_path = str(self._sc.CreateSession(
            dbus.Dictionary({}, signature="sv")
        ))
        session_obj = self._bus.get_object(MUTTER_BUS, self._session_path)
        session     = dbus.Interface(session_obj, MUTTER_SES_IF)
        session_obj.connect_to_signal("Closed",
            lambda: (setattr(self, "_error", "Session closed"), self._loop.quit()),
            dbus_interface=MUTTER_SES_IF)
        stream_path = str(session.RecordVirtual(
            dbus.Dictionary({"cursor-mode": dbus.UInt32(1)}, signature="sv")
        ))
        stream_obj = self._bus.get_object(MUTTER_BUS, stream_path)
        stream_obj.connect_to_signal("PipeWireStreamAdded",
            lambda node_id: (setattr(self, "_node_id", int(node_id)),
                             log.info("PipeWire node: %d", int(node_id)),
                             self._loop.quit()),
            dbus_interface=MUTTER_STR_IF)
        session.Start()
        GLib.timeout_add(MUTTER_SETUP_TIMEOUT_MS, lambda: (
            setattr(self, "_error", "Timeout"), self._loop.quit()
        ))
        self._loop.run()
        if self._error:
            raise RuntimeError(self._error)
        return self._node_id

    def close(self):
        if self._session_path:
            try:
                obj = self._bus.get_object(MUTTER_BUS, self._session_path)
                dbus.Interface(obj, MUTTER_SES_IF).Stop()
            except Exception:
                pass


# ── GStreamer capture ─────────────────────────────────────────────────────────

class PipeWireCapture:
    def __init__(self, node_id: int, width: int, height: int, codec: int):
        self.width  = width
        self.height = height
        self.codec  = codec
        self._frame = None
        self._fw    = width
        self._fh    = height
        self._lock  = threading.Lock()

        Gst.init(None)
        self._loop = GLib.MainLoop()

        if codec == CODEC_H264:
            aspect = width / height
            h264_h = int(H264_WIDTH / aspect)
            if h264_h % 2 != 0:
                h264_h += 1
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                f"! videoconvert ! videoscale "
                f"! video/x-raw,format=I420,width={H264_WIDTH},height={h264_h} "
                f"! x264enc tune=zerolatency speed-preset=ultrafast "
                f"  bitrate={H264_BITRATE} key-int-max={FPS} "
                f"! h264parse config-interval=-1 "
                f"! video/x-h264,stream-format=avc,alignment=au "
                f"! appsink name=sink emit-signals=true "
                f"  max-buffers={APPSINK_MAX_BUFFERS} drop=true sync=false"
            )
        else:
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                # Caps filter forces PipeWire to negotiate the exact resolution
                # with Mutter. Without this, pipewiresrc defaults to 1280x720
                # regardless of the virtual display's actual size.
                f"! video/x-raw,width={self.width},height={self.height},"
                f"max-framerate={FPS}/1 "
                f"! videoconvert "
                f"! video/x-raw,format=BGR "
                f"! appsink name=sink emit-signals=true "
                f"  max-buffers={APPSINK_MAX_BUFFERS} drop=true sync=false"
            )

        log.info("GStreamer: %s", pipeline_str)
        self._pipeline = Gst.parse_launch(pipeline_str)
        sink = self._pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer pipeline failed")
        log.info("GStreamer pipeline playing (codec=%s)",
                 "H.264" if codec == CODEC_H264 else "JPEG")
        threading.Thread(target=self._loop.run, daemon=True).start()

    def _on_sample(self, sink) -> Gst.FlowReturn:
        sample = sink.emit("pull-sample")
        if not sample:
            return Gst.FlowReturn.ERROR
        buf    = sample.get_buffer()
        ok, mi = buf.map(Gst.MapFlags.READ)
        if ok:
            with self._lock:
                self._frame = bytes(mi.data)
                try:
                    caps = sample.get_caps().get_structure(0)
                    self._fw = caps.get_value("width")
                    self._fh = caps.get_value("height")
                except Exception:
                    pass
            buf.unmap(mi)
        return Gst.FlowReturn.OK

    def get_frame(self):
        with self._lock:
            return (self._frame, self._fw, self._fh) if self._frame else None

    def close(self):
        self._pipeline.set_state(Gst.State.NULL)
        self._loop.quit()


# ── JPEG encode ───────────────────────────────────────────────────────────────

def to_jpeg(raw: bytes, w: int, h: int) -> bytes:
    img = Image.frombytes("RGB", (w, h), raw, "raw", "BGR")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


# ── TCP streaming ─────────────────────────────────────────────────────────────

def stream_to_client(conn: socket.socket, addr: tuple,
                     capture: PipeWireCapture, codec: int,
                     tray: TrayState) -> None:
    log.info("Client connected: %s:%d", *addr)

    accepted, device_name, screen_w, screen_h = accept_client(conn, addr)
    if not accepted:
        return

    try:
        for _ in range(FRAME_WAIT_ATTEMPTS):
            r = capture.get_frame()
            if r:
                _, w, h = r
                break
            time.sleep(FRAME_WAIT_SLEEP_S)
        else:
            w, h = capture.width, capture.height

        conn.sendall(MAGIC_OK + struct.pack(">IIB", w, h, codec))
        log.info("Streaming %dx%d %s @ %d FPS → %s",
                 w, h, "H.264" if codec == CODEC_H264 else "JPEG",
                 FPS, device_name)
        tray.update(connected=True, client_ip=f"{device_name} ({addr[0]})")

        frame_count  = 0
        fps_deadline = time.monotonic() + 1.0

        while True:
            start = time.monotonic()
            r = capture.get_frame()
            if r:
                raw, fw, fh = r
                frame_data = to_jpeg(raw, fw, fh) if codec == CODEC_JPEG else raw
                conn.sendall(struct.pack(">I", len(frame_data)) + frame_data)
                frame_count += 1

            if time.monotonic() >= fps_deadline:
                tray.update(fps=frame_count)
                frame_count  = 0
                fps_deadline = time.monotonic() + 1.0

            elapsed = time.monotonic() - start
            sleep   = FRAME_INTERVAL - elapsed
            if sleep > 0:
                time.sleep(sleep)

    except (BrokenPipeError, ConnectionResetError):
        log.info("Client disconnected: %s", device_name)
    except Exception as e:
        log.error("Stream error for %s — %s", device_name, e)
    finally:
        tray.update(connected=False, client_ip=None, fps=0)
        _active_client_lock.release()
        conn.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def run_server():
    codec = detect_codec()

    # Set up D-Bus before resolution detection — Mutter uses the same bus
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()

    WIDTH, HEIGHT = resolve_resolution(bus, args.width, args.height)

    log.info("TetherLink v%s — %s encoding %dx%d @ %d FPS",
             VERSION, "H.264" if codec == CODEC_H264 else "JPEG",
             WIDTH, HEIGHT, FPS)

    display = MutterVirtualDisplay(WIDTH, HEIGHT, bus)
    try:
        node_id = display.setup()
    except Exception as e:
        log.error("Virtual display failed: %s", e)
        display.close()
        raise SystemExit(1)

    log.info("Virtual display ready — drag windows onto it!")
    capture = PipeWireCapture(node_id, WIDTH, HEIGHT, codec)
    time.sleep(CAPTURE_INIT_SLEEP_S)

    tray_state = TrayState()
    tray_state.update(resolution=f"{WIDTH}×{HEIGHT}")
    shutdown_event = threading.Event()

    # Bind socket first — actual_port may differ from args.port if taken
    try:
        srv, actual_port = bind_server_socket(args.port)
    except OSError as e:
        log.error("Could not bind stream port: %s", e)
        capture.close()
        display.close()
        raise SystemExit(1)

    # Pass actual_port to broadcaster so discovery reflects the real port
    broadcaster = DiscoveryBroadcaster(actual_port, WIDTH, HEIGHT)
    broadcaster.start()

    tray = start_tray(tray_state, on_quit=lambda: shutdown_event.set())

    try:
        while not shutdown_event.is_set():
            try:
                conn, addr = srv.accept()
                threading.Thread(
                    target=stream_to_client,
                    args=(conn, addr, capture, codec, tray_state),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        tray.quit()
        broadcaster.stop()
        srv.close()
        capture.close()
        display.close()
        import signal
        os.kill(os.getpid(), signal.SIGTERM)


if __name__ == "__main__":
    run_server()