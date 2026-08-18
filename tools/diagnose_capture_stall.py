#!/usr/bin/env python3
"""Isolate whether the capture source stalls, independent of everything else.

Creates a virtual monitor at the given size and runs the SAME capture front
end the H.264 path uses — pipewiresrc with the same caps filter — into a
counting sink. No encoder, no socket, no Android device.

That isolation is the point. If frames stop arriving here, the stall is in
Mutter/PipeWire and nothing downstream can be blamed. If frames keep arriving,
the freeze lives in the encoder, the USB link or the tablet's decoder.

Usage:
    ./venv/bin/python tools/diagnose_capture_stall.py [WIDTH HEIGHT FPS]

Then move a window onto the new virtual display and play a video in it.
Watch the per-second output. Ctrl-C to stop.

Set TETHRLINK_GST_DEBUG=1 to also write GStreamer negotiation logs to
/tmp/tethrlink-capture-debug.log for diagnosing format renegotiation.
"""

import os
import functools
import sys
import threading
import time

import dbus
import dbus.mainloop.glib
import gi

# Unbuffered prints: this tool is watched live and killed with Ctrl-C,
# so buffered output would be lost exactly when it matters.
print = functools.partial(print, flush=True)  # noqa: A001

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

MUTTER_BUS = "org.gnome.Mutter.ScreenCast"
MUTTER_PATH = "/org/gnome/Mutter/ScreenCast"
SC_IF = "org.gnome.Mutter.ScreenCast"
SES_IF = "org.gnome.Mutter.ScreenCast.Session"
STR_IF = "org.gnome.Mutter.ScreenCast.Stream"

WIDTH = int(sys.argv[1]) if len(sys.argv) > 3 else 2960
HEIGHT = int(sys.argv[2]) if len(sys.argv) > 3 else 1848
FPS = int(sys.argv[3]) if len(sys.argv) > 3 else 30

if os.environ.get("TETHRLINK_GST_DEBUG") == "1":
    # Only the categories that matter for negotiation and buffer flow, so the
    # log stays readable rather than gigabytes of everything.
    os.environ["GST_DEBUG"] = "pipewiresrc:5,GST_CAPS:4,GST_PADS:4,videorate:4"
    os.environ["GST_DEBUG_FILE"] = "/tmp/tethrlink-capture-debug.log"
    print("GStreamer debug -> /tmp/tethrlink-capture-debug.log")

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
Gst.init(None)


def main() -> int:
    bus = dbus.SessionBus()
    sc = dbus.Interface(bus.get_object(MUTTER_BUS, MUTTER_PATH), SC_IF)
    session_path = str(sc.CreateSession(dbus.Dictionary({}, signature="sv")))
    session = dbus.Interface(bus.get_object(MUTTER_BUS, session_path), SES_IF)
    stream_path = str(session.RecordVirtual(
        dbus.Dictionary({"cursor-mode": dbus.UInt32(1)}, signature="sv")))

    node = {}
    setup_loop = GLib.MainLoop()
    bus.get_object(MUTTER_BUS, stream_path).connect_to_signal(
        "PipeWireStreamAdded",
        lambda nid: (node.__setitem__("id", int(nid)), setup_loop.quit()),
        dbus_interface=STR_IF)
    session.Start()
    GLib.timeout_add(5000, lambda: (setup_loop.quit(), False)[1])
    setup_loop.run()

    if "id" not in node:
        print("FAILED: Mutter never delivered a PipeWire node")
        return 1

    node_id = node["id"]

    # Deliberately mirrors the production H.264 front end, including the caps
    # filter on pipewiresrc that is what actually sets the virtual monitor's
    # size (RecordVirtual takes no size arguments).
    desc = (
        f"pipewiresrc path={node_id} always-copy=true "
        f"! video/x-raw,width={WIDTH},height={HEIGHT},max-framerate={FPS}/1 "
        f"! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
        f"! videorate "
        f"! videoconvert "
        f"! video/x-raw,format=NV12,framerate={FPS}/1,colorimetry=bt709 "
        f"! appsink name=sink emit-signals=true max-buffers=1 drop=false sync=false"
    )
    print(f"virtual display: {WIDTH}x{HEIGHT} @ max {FPS} fps  (PipeWire node {node_id})")
    print(f"pipeline: {desc}\n")

    pipeline = Gst.parse_launch(desc)
    state = {"frames": 0, "bytes": 0, "last_frame_at": time.monotonic()}
    lock = threading.Lock()

    def on_sample(sink):
        sample = sink.emit("pull-sample")
        if sample:
            buf = sample.get_buffer()
            with lock:
                state["frames"] += 1
                state["bytes"] += buf.get_size()
                state["last_frame_at"] = time.monotonic()
        return Gst.FlowReturn.OK

    pipeline.get_by_name("sink").connect("new-sample", on_sample)

    gst_bus = pipeline.get_bus()
    gst_bus.add_signal_watch()

    def on_error(_b, msg):
        err, dbg = msg.parse_error()
        print(f"\n!!! GSTREAMER ERROR: {err}\n    {dbg}")

    def on_eos(_b, _msg):
        print("\n!!! GSTREAMER EOS — the source ended the stream")

    gst_bus.connect("message::error", on_error)
    gst_bus.connect("message::eos", on_eos)

    if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
        print("FAILED: pipeline could not reach PLAYING")
        return 1

    print("Move a window onto the new virtual display and play a video in it.")
    print("Watching frame delivery — Ctrl-C to stop.\n")

    threading.Thread(target=GLib.MainLoop().run, daemon=True).start()

    prev = 0
    stalled_for = 0.0
    try:
        while True:
            time.sleep(1.0)
            with lock:
                total = state["frames"]
                total_bytes = state["bytes"]
                idle = time.monotonic() - state["last_frame_at"]
            delta = total - prev
            prev = total
            mbps = (total_bytes * 8) / 1e6
            state["bytes"] = 0

            if delta == 0:
                stalled_for += 1.0
                print(f"  fps=0   total={total:<7} "
                      f"** NO FRAMES for {stalled_for:.0f}s "
                      f"(last frame {idle:.1f}s ago) **")
            else:
                if stalled_for:
                    print(f"  --- recovered after {stalled_for:.0f}s of no frames ---")
                stalled_for = 0.0
                print(f"  fps={delta:<4} total={total:<7} raw={mbps:6.1f} Mbit/s")
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        pipeline.set_state(Gst.State.NULL)
        try:
            session.Stop()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
