"""
TetherLink Server Core — v0.9.5
Importable server logic. No argparse, no __main__.
Used by both app.py (GUI) and the CLI shim (tetherlink_server.py).
"""

import logging
import os
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import dbus
import dbus.mainloop.glib
import gi
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

from discovery import DiscoveryBroadcaster

log = logging.getLogger("TetherLink")

VERSION = "0.9.5"

# ── Codec constants ───────────────────────────────────────────────────────────
CODEC_H264 = 1
CODEC_JPEG = 2

# ── Timing constants ──────────────────────────────────────────────────────────
HANDSHAKE_TIMEOUT_S     = 10.0
FRAME_WAIT_ATTEMPTS     = 100
FRAME_WAIT_SLEEP_S      = 0.05
CAPTURE_INIT_SLEEP_S    = 0.5
MUTTER_SETUP_TIMEOUT_MS = 10_000

# ── Network constants ─────────────────────────────────────────────────────────
PORT_SCAN_RANGE     = 10
APPSINK_MAX_BUFFERS = 2

# ── Mutter constants ──────────────────────────────────────────────────────────
MUTTER_BUS    = "org.gnome.Mutter.ScreenCast"
MUTTER_PATH   = "/org/gnome/Mutter/ScreenCast"
MUTTER_SC_IF  = "org.gnome.Mutter.ScreenCast"
MUTTER_SES_IF = "org.gnome.Mutter.ScreenCast.Session"
MUTTER_STR_IF = "org.gnome.Mutter.ScreenCast.Stream"

MAGIC_HELLO = b"TLHELO"
MAGIC_OK    = b"TLOK__"
MAGIC_BUSY  = b"TLBUSY"

FALLBACK_WIDTH  = 1920
FALLBACK_HEIGHT = 1080


# ── Config & State ────────────────────────────────────────────────────────────

@dataclass
class ServerConfig:
    fps: int = 60
    quality: int = 90
    codec: int = CODEC_JPEG
    bitrate: int = 3000
    h264_width: int = 1280
    port: int = 51137
    width: int = 0          # 0 = use device dims (auto)
    height: int = 0
    device_width: int = 0   # saved dims from last connected device
    device_height: int = 0
    orientation: str = "landscape"      # "landscape" | "portrait"
    monitor_position: str = "right"     # "left" | "right" | "above" | "below"
    display_mode: str = "extend"        # "extend" | "mirror" | "second_only"
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
        _serial, monitors, logical_monitors, _props = iface.GetCurrentState()
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


def _get_virtual_output_name() -> Optional[str]:
    """Find the xrandr output name for the Mutter virtual display."""
    try:
        result = subprocess.run(["xrandr", "--query"], capture_output=True,
                                text=True, timeout=3)
        # Pass 1: case-insensitive "virtual" + connected
        for line in result.stdout.splitlines():
            lower = line.lower()
            if "virtual" in lower and " connected" in lower:
                return line.split()[0]
        # Pass 2: any non-primary connected output (catches unusual names)
        for line in result.stdout.splitlines():
            if " connected" in line and " primary" not in line:
                name = line.split()[0]
                log.debug("Using non-primary output as virtual: %s", name)
                return name
        log.debug("xrandr outputs:\n%s", result.stdout[:800])
    except Exception as e:
        log.debug("xrandr query failed: %s", e)
    return None


# ── Mutter virtual display ────────────────────────────────────────────────────

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
        self.width       = width
        self.height      = height
        self._node_id    = None
        self._error      = None
        self._in_setup   = True   # True until setup() returns
        self.on_closed   = None   # callable(); set after setup() for external close events
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus  = bus or dbus.SessionBus()
        self._loop = GLib.MainLoop()
        cleanup_orphaned_sessions(self._bus)
        sc_obj    = self._bus.get_object(MUTTER_BUS, MUTTER_PATH)
        self._sc  = dbus.Interface(sc_obj, MUTTER_SC_IF)
        self._session_path = None

    def setup(self) -> int:
        log.info("Creating Mutter ScreenCast session...")

        # Try to suppress GNOME screen-recording indicator (Mutter ≥ 44 honours this)
        session_props: dict = {}
        for props in [{"disable-notifications": dbus.Boolean(True)}, {}]:
            try:
                self._session_path = str(self._sc.CreateSession(
                    dbus.Dictionary(props, signature="sv")
                ))
                break
            except Exception as e:
                if not props:
                    raise
                log.debug("CreateSession with disable-notifications failed (%s), retrying plain", e)

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
        self._in_setup = False
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
    def __init__(self, node_id: int, width: int, height: int, codec: int,
                 fps: int, bitrate: int, h264_width: int, quality: int = 90,
                 flip_orientation: bool = False):
        self.width  = width
        self.height = height
        self.codec  = codec
        self._frame = None
        self._fw    = width
        self._fh    = height
        self._lock  = threading.Lock()
        self._jpegenc = None  # reference for live quality changes
        self._flip    = None  # reference for live orientation changes

        Gst.init(None)
        self._loop = GLib.MainLoop()

        if codec == CODEC_H264:
            aspect = width / height
            h264_h = int(h264_width / aspect)
            if h264_h % 2 != 0:
                h264_h += 1
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                f"! videoconvert ! videoscale "
                f"! video/x-raw,format=I420,width={h264_width},height={h264_h} "
                f"! x264enc tune=zerolatency speed-preset=ultrafast "
                f"  bitrate={bitrate} key-int-max={fps} "
                f"! h264parse config-interval=-1 "
                f"! video/x-h264,stream-format=avc,alignment=au "
                f"! appsink name=sink emit-signals=true "
                f"  max-buffers={APPSINK_MAX_BUFFERS} drop=true sync=false"
            )
        else:
            flip_method = "clockwise" if flip_orientation else "none"
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                f"! video/x-raw,width={width},height={height},max-framerate={fps}/1 "
                f"! videoconvert "
                f"! video/x-raw,format=I420 "
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
                # image/jpeg caps have no width/height fields — use values from init.
                # For H.264, read actual dims in case pipeline scaled.
                if self.codec == CODEC_H264:
                    try:
                        caps = sample.get_caps().get_structure(0)
                        self._fw = caps.get_value("width")
                        self._fh = caps.get_value("height")
                    except Exception:
                        pass
            buf.unmap(mi)
        return Gst.FlowReturn.OK

    def set_quality(self, quality: int) -> None:
        """Hot-reload JPEG quality. No-op for H.264."""
        if self._jpegenc is not None:
            self._jpegenc.set_property("quality", quality)

    def set_orientation(self, portrait: bool) -> None:
        """Hot-reload orientation via videoflip. No-op for H.264."""
        if self._flip is not None:
            method = "clockwise" if portrait else "none"
            self._flip.set_property("method", method)

    def get_frame(self):
        with self._lock:
            return (self._frame, self._fw, self._fh) if self._frame else None

    def close(self):
        self._pipeline.set_state(Gst.State.NULL)
        self._loop.quit()




# ── ServerCore ────────────────────────────────────────────────────────────────

class ServerCore:
    """
    Manages the full TetherLink server lifecycle.
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
        self._broadcaster: Optional[DiscoveryBroadcaster] = None
        self._srv: Optional[socket.socket]            = None
        self._bus                                     = None
        self._shutdown = threading.Event()
        self._accept_thread: Optional[threading.Thread] = None
        self._client_lock = threading.Lock()
        self._live_fps          = config.fps
        self._live_quality      = config.quality
        self._primary_was_hidden = False  # True when display_mode == second_only

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

    def set_fps(self, fps: int) -> None:
        """Hot-reload FPS. Takes effect on next frame cycle.
        CPython GIL makes integer attribute writes atomic — safe to call from GTK thread
        while _handle_client reads _live_fps from a daemon thread."""
        self._live_fps = fps
        self._config.fps = fps

    def set_quality(self, quality: int) -> None:
        """Hot-reload JPEG quality via jpegenc element property (no pipeline restart)."""
        self._live_quality = quality
        self._config.quality = quality
        if self._capture:
            self._capture.set_quality(quality)

    def _apply_display_layout(self) -> None:
        """Apply monitor position, display mode, and orientation via xrandr after display setup."""
        virtual = _get_virtual_output_name()
        if not virtual:
            log.debug("Virtual output not found in xrandr — layout not applied")
            return

        mode = self._config.display_mode
        if mode == "mirror":
            primary_out = self._get_primary_output_name()
            if primary_out:
                subprocess.run(["xrandr", "--output", virtual,
                                "--same-as", primary_out], check=False, capture_output=True)
                self._log(f"Display mode: mirror ({virtual} → {primary_out})")
        elif mode == "second_only":
            primary_out = self._get_primary_output_name()
            if primary_out:
                subprocess.run(["xrandr",
                                "--output", primary_out, "--off",
                                "--output", virtual, "--auto"],
                               check=False, capture_output=True)
                self._primary_was_hidden = True
                self._log(f"Display mode: tablet only ({primary_out} hidden)")
        else:  # extend
            self._apply_position(virtual)

        # Portrait rotation (xrandr rotate on top of dimension-swapped virtual display)
        if self._config.orientation == "portrait":
            subprocess.run(["xrandr", "--output", virtual, "--rotate", "left"],
                           check=False, capture_output=True)
            self._log("Orientation: portrait (rotated left)")

    def _apply_position(self, virtual: str) -> None:
        """Position virtual display relative to primary via xrandr."""
        if not self._bus:
            return
        primary = detect_primary_resolution_mutter(self._bus) or (FALLBACK_WIDTH, FALLBACK_HEIGHT)
        pw, ph   = primary
        position = self._config.monitor_position
        primary_out = self._get_primary_output_name()
        vw = self._display.width  if self._display else FALLBACK_WIDTH
        vh = self._display.height if self._display else FALLBACK_HEIGHT

        if position == "right":
            subprocess.run(["xrandr", "--output", virtual, "--pos", f"{pw}x0"],
                           check=False, capture_output=True)
        elif position == "left":
            subprocess.run(["xrandr", "--output", virtual, "--pos", "0x0"],
                           check=False, capture_output=True)
            if primary_out:
                subprocess.run(["xrandr", "--output", primary_out, "--pos", f"{vw}x0"],
                               check=False, capture_output=True)
        elif position == "above":
            subprocess.run(["xrandr", "--output", virtual, "--pos", "0x0"],
                           check=False, capture_output=True)
            if primary_out:
                subprocess.run(["xrandr", "--output", primary_out, "--pos", f"0x{vh}"],
                               check=False, capture_output=True)
        elif position == "below":
            subprocess.run(["xrandr", "--output", virtual, "--pos", f"0x{ph}"],
                           check=False, capture_output=True)
        self._log(f"Monitor position: {position}")

    def set_orientation(self, orientation: str) -> None:
        """Hot-reload orientation via videoflip. No pipeline restart needed."""
        self._config.orientation = orientation
        if self._capture:
            self._capture.set_orientation(orientation == "portrait")

    def set_monitor_position(self, position: str) -> None:
        """Update position in config. Applied at next server start."""
        self._config.monitor_position = position

    def _get_primary_output_name(self) -> Optional[str]:
        try:
            result = subprocess.run(["xrandr", "--query"], capture_output=True,
                                    text=True, timeout=3)
            for line in result.stdout.splitlines():
                if " connected primary" in line:
                    return line.split()[0]
        except Exception:
            pass
        return None

    def restart_with_config(self, config: ServerConfig) -> None:
        """Stop, apply new config, restart. Used for codec/resolution/port changes."""
        self._log("Restarting with new configuration…")
        self._state.update(running=False, connected=False)
        self.stop()
        self._config       = config
        self._live_fps     = config.fps
        self._live_quality = config.quality
        self.start()

    def _accept_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                conn, addr = self._srv.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, addr),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, conn: socket.socket, addr: tuple) -> None:
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

        # ── Save device dims whenever received ────────────────────────────────
        if screen_w > 0 and screen_h > 0:
            self._config.device_width  = screen_w
            self._config.device_height = screen_h

        # ── Resolve resolution: user config > device dims > auto-detect ──────
        if self._config.width > 0 and self._config.height > 0:
            width, height = self._config.width, self._config.height
            self._log(f"Using configured resolution: {width}×{height}")
        elif screen_w > 0 and screen_h > 0:
            width, height = screen_w, screen_h
            self._log(f"Using device resolution: {width}×{height}")
        else:
            width, height = resolve_resolution(self._bus, 0, 0)
            self._log(f"Using detected resolution: {width}×{height}")
        self._state.update_direct(active_width=width, active_height=height)

        if self._config.orientation == "portrait" and width > height:
            width, height = height, width

        codec = self._config.codec
        self._log(f"Starting — {width}×{height} @ {self._live_fps} FPS "
                  f"({'H.264' if codec == CODEC_H264 else 'JPEG'})")

        # ── Set up virtual display ────────────────────────────────────────────
        display = None
        capture = None
        try:
            display = MutterVirtualDisplay(width, height, self._bus)
            try:
                node_id = display.setup()
            except Exception as e:
                self._log(f"Virtual display failed: {e}")
                display.close()
                self._client_lock.release()
                conn.close()
                return

            self._log("Virtual display ready — drag windows onto it!")

            def _on_session_closed():
                if self._state.running:
                    self._log("Screen cast session closed externally — stopping server")
                    threading.Thread(target=self.stop, daemon=True).start()
                    if self._on_external_stop:
                        GLib.idle_add(self._on_external_stop)
            display.on_closed = _on_session_closed
            self._display = display

            self._apply_display_layout()

            capture = PipeWireCapture(
                node_id, width, height, codec,
                self._live_fps, self._config.bitrate, self._config.h264_width,
                self._live_quality,
                flip_orientation=(self._config.orientation == "portrait"),
            )
            self._capture = capture
            time.sleep(CAPTURE_INIT_SLEEP_S)

            self._state.update(
                resolution=f"{width}×{height}",
                codec_name="H.264" if codec == CODEC_H264 else "JPEG",
            )

            # ── Stream ────────────────────────────────────────────────────────
            conn.settimeout(None)
            for _ in range(FRAME_WAIT_ATTEMPTS):
                r = capture.get_frame()
                if r:
                    _, w, h = r
                    break
                time.sleep(FRAME_WAIT_SLEEP_S)
            else:
                w, h = capture.width, capture.height

            conn.sendall(MAGIC_OK + struct.pack(">IIB", w, h, codec))
            self._log(f"Streaming → {device_name}")
            self._state.update(connected=True, client_name=device_name,
                               active_width=width, active_height=height,
                               device_width=self._config.device_width,
                               device_height=self._config.device_height)

            frame_count  = 0
            fps_deadline = time.monotonic() + 1.0

            while not self._shutdown.is_set():
                start = time.monotonic()
                r = capture.get_frame()
                if r:
                    raw, fw, fh = r
                    conn.sendall(struct.pack(">I", len(raw)) + raw)
                    frame_count += 1

                if time.monotonic() >= fps_deadline:
                    self._state.update(fps=frame_count)
                    frame_count  = 0
                    fps_deadline = time.monotonic() + 1.0

                elapsed = time.monotonic() - start
                sleep   = (1.0 / self._live_fps) - elapsed
                if sleep > 0:
                    time.sleep(sleep)

        except (BrokenPipeError, ConnectionResetError):
            self._log(f"Client disconnected: {device_name}")
        except Exception as e:
            self._log(f"Stream error ({device_name}): {e}")
        finally:
            self._state.update(connected=False, client_name="", fps=0)
            if capture:
                capture.close()
                self._capture = None
            if self._primary_was_hidden:
                primary_out = self._get_primary_output_name()
                if primary_out:
                    subprocess.run(["xrandr", "--output", primary_out, "--auto"],
                                   check=False, capture_output=True)
                self._primary_was_hidden = False
            if display:
                display.close()
                self._display = None
            self._client_lock.release()
            conn.close()
