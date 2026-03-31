"""
TetherLink Server - v0.9.0
Wayland + PipeWire virtual display with H.264 encoding.

Usage:
    ./server/run_server.sh
    ./server/run_server.sh --fps 60 --quality 90
    ./server/run_server.sh --codec jpeg
    ./server/run_server.sh --pair
    ./server/run_server.sh --reset
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import secrets
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

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="TetherLink Server")
parser.add_argument("--width",   type=int,  default=2960)
parser.add_argument("--height",  type=int,  default=1848)
parser.add_argument("--fps",     type=int,  default=60)
parser.add_argument("--quality", type=int,  default=90)
parser.add_argument("--port",    type=int,  default=8080)
parser.add_argument("--codec",   type=str,  default="h264",
                    choices=["auto", "h264", "jpeg"])
parser.add_argument("--pair",    action="store_true")
parser.add_argument("--reset",   action="store_true")
args = parser.parse_args()

WIDTH          = args.width
HEIGHT         = args.height
FPS            = args.fps
JPEG_QUALITY   = args.quality
PORT           = args.port
FRAME_INTERVAL = 1.0 / FPS
CONFIG_DIR     = Path.home() / ".config" / "tetherlink"
SECRET_FILE    = CONFIG_DIR / "secret.key"
DEVICES_FILE   = CONFIG_DIR / "paired_devices.json"

CODEC_H264 = 1
CODEC_JPEG = 2
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TetherLink")


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


# ── Secret key + pairing ──────────────────────────────────────────────────────

def load_or_create_secret() -> bytes:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if args.reset and SECRET_FILE.exists():
        SECRET_FILE.unlink()
        DEVICES_FILE.unlink(missing_ok=True)
        log.info("Secret reset — all paired devices removed")
    if SECRET_FILE.exists():
        return SECRET_FILE.read_bytes()
    secret = secrets.token_bytes(32)
    SECRET_FILE.write_bytes(secret)
    SECRET_FILE.chmod(0o600)
    log.info("New secret key generated")
    return secret


def load_paired_devices() -> dict:
    if DEVICES_FILE.exists():
        return json.loads(DEVICES_FILE.read_text())
    return {}


def save_paired_device(device_id: str, name: str):
    devices = load_paired_devices()
    devices[device_id] = {"name": name, "paired_at": time.time()}
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEVICES_FILE.write_text(json.dumps(devices, indent=2))
    log.info("Paired device: %s (%s)", name, device_id)


def show_qr_code(secret: bytes):
    try:
        import qrcode, base64
        payload = base64.b64encode(secret).decode()
        qr = qrcode.QRCode(border=1)
        qr.add_data(f"tetherlink://pair?key={payload}")
        qr.make(fit=True)
        print("\n" + "="*50)
        print("  TetherLink — Scan to pair your Android tablet")
        print("="*50)
        qr.print_ascii(invert=True)
        print("="*50 + "\n")
    except ImportError:
        import base64
        payload = base64.b64encode(secret).decode()
        print(f"\nPairing URL: tetherlink://pair?key={payload}\n")


# ── HMAC handshake ────────────────────────────────────────────────────────────

MAGIC_HELLO     = b"TLHELO"
MAGIC_CHALLENGE = b"TLCHAL"
MAGIC_RESPONSE  = b"TLRESP"
MAGIC_OK        = b"TLOK__"
MAGIC_REJECT    = b"TLREJ_"

_active_client_lock = threading.Lock()


def authenticate_client(conn, addr, secret) -> tuple[bool, str, str, int, int]:
    conn.settimeout(10.0)
    try:
        hello = conn.recv(6 + 16 + 8 + 64)
    except socket.timeout:
        return False, "", "", 0, 0

    if len(hello) < 30 or hello[:6] != MAGIC_HELLO:
        conn.sendall(MAGIC_REJECT)
        return False, "", "", 0, 0

    device_id          = hello[6:22].hex()
    screen_w, screen_h = struct.unpack(">II", hello[22:30])
    device_name        = hello[30:].decode("utf-8", errors="replace").strip("\x00") or f"Android-{device_id[:8]}"
    log.info("Device screen: %dx%d", screen_w, screen_h)

    if not _active_client_lock.acquire(blocking=False):
        conn.sendall(MAGIC_REJECT + b"BUSY")
        return False, "", "", 0, 0

    nonce = secrets.token_bytes(16)
    conn.sendall(MAGIC_CHALLENGE + nonce)

    try:
        response = conn.recv(6 + 32)
    except socket.timeout:
        _active_client_lock.release()
        return False, "", "", 0, 0

    if len(response) < 38 or response[:6] != MAGIC_RESPONSE:
        conn.sendall(MAGIC_REJECT)
        _active_client_lock.release()
        return False, "", "", 0, 0

    client_hmac   = response[6:38]
    expected_hmac = hmac.new(secret, nonce, hashlib.sha256).digest()

    if not hmac.compare_digest(client_hmac, expected_hmac):
        conn.sendall(MAGIC_REJECT)
        _active_client_lock.release()
        return False, "", "", 0, 0

    conn.settimeout(None)
    devices = load_paired_devices()
    if device_id not in devices:
        save_paired_device(device_id, device_name)

    return True, device_id, device_name, screen_w, screen_h


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
    def __init__(self, width: int, height: int):
        self.width    = width
        self.height   = height
        self._node_id = None
        self._error   = None
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus  = dbus.SessionBus()
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
        GLib.timeout_add(10_000, lambda: (
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
            h264_w = 1280
            h264_h = int(h264_w / aspect)
            if h264_h % 2 != 0:
                h264_h += 1
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                f"! videoconvert ! videoscale "
                f"! video/x-raw,format=I420,width={h264_w},height={h264_h} "
                f"! x264enc tune=zerolatency speed-preset=ultrafast "
                f"  bitrate=3000 key-int-max={FPS} "
                f"! h264parse config-interval=-1 "
                f"! video/x-h264,stream-format=avc,alignment=au "
                f"! appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false"
            )
        else:
            pipeline_str = (
                f"pipewiresrc path={node_id} always-copy=true "
                f"! videoconvert "
                f"! video/x-raw,format=BGR "
                f"! appsink name=sink emit-signals=true max-buffers=2 drop=true sync=false"
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
                     capture: PipeWireCapture, secret: bytes,
                     codec: int, tray: TrayState) -> None:
    log.info("Client connected: %s:%d", *addr)

    ok, device_id, device_name, screen_w, screen_h = authenticate_client(conn, addr, secret)
    if not ok:
        conn.close()
        return

    try:
        for _ in range(100):
            r = capture.get_frame()
            if r:
                _, w, h = r
                break
            time.sleep(0.05)
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
    secret = load_or_create_secret()
    if args.pair or not DEVICES_FILE.exists():
        show_qr_code(secret)

    codec = detect_codec()

    log.info("TetherLink v0.9.0 — %s encoding %dx%d @ %d FPS",
             "H.264" if codec == CODEC_H264 else "JPEG", WIDTH, HEIGHT, FPS)
    log.info("Paired devices: %d", len(load_paired_devices()))

    display = MutterVirtualDisplay(WIDTH, HEIGHT)
    try:
        node_id = display.setup()
    except Exception as e:
        log.error("Virtual display failed: %s", e)
        display.close()
        raise SystemExit(1)

    log.info("Virtual display ready — drag windows onto it!")
    capture = PipeWireCapture(node_id, WIDTH, HEIGHT, codec)
    time.sleep(0.5)

    tray_state     = TrayState()
    tray_state.update(resolution=f"{WIDTH}×{HEIGHT}")
    shutdown_event = threading.Event()

    broadcaster = DiscoveryBroadcaster(PORT, WIDTH, HEIGHT)
    broadcaster.start()

    tray = start_tray(tray_state, on_quit=lambda: shutdown_event.set())

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", PORT))
        srv.listen(5)
        srv.settimeout(1.0)
        log.info("Server ready on port %d", PORT)

        try:
            while not shutdown_event.is_set():
                try:
                    conn, addr = srv.accept()
                    threading.Thread(
                        target=stream_to_client,
                        args=(conn, addr, capture, secret, codec, tray_state),
                        daemon=True,
                    ).start()
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            tray.quit()
            broadcaster.stop()
            capture.close()
            display.close()
            import signal
            os.kill(os.getpid(), signal.SIGTERM)


if __name__ == "__main__":
    run_server()