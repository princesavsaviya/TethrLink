import os
import subprocess
import time
from tests.lib.mock_client import MockClient
from tests.lib.result import TestResult, passed, failed, warned, skipped
from tests.lib import server_manager

NESTED_DISPLAY  = "wayland-tl-test"
NESTED_PORT     = 18081
NESTED_TIMEOUT  = 20   # seconds to wait for nested session socket


def run(report_dir: str) -> TestResult:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    socket_path = os.path.join(runtime_dir, NESTED_DISPLAY)

    # Launch nested gnome-shell
    env = os.environ.copy()
    env["WAYLAND_DISPLAY"] = NESTED_DISPLAY
    nested_log = open(os.path.join(report_dir, "nested_shell.log"), "w")
    try:
        nested_proc = subprocess.Popen(
            ["dbus-run-session", "--", "gnome-shell", "--nested", "--wayland"],
            env=env, stdout=nested_log, stderr=nested_log,
        )
    except FileNotFoundError:
        nested_log.close()
        return skipped("Nested Session", "gnome-shell not found — install gnome-shell")

    # Wait for socket
    deadline = time.monotonic() + NESTED_TIMEOUT
    while time.monotonic() < deadline:
        if os.path.exists(socket_path):
            break
        if nested_proc.poll() is not None:
            nested_log.close()
            return warned(
                "Nested Session",
                "gnome-shell --nested exited immediately — display driver may not support nested Wayland",
            )
        time.sleep(0.5)
    else:
        nested_proc.terminate()
        nested_log.close()
        return warned(
            "Nested Session",
            f"Nested Wayland socket '{NESTED_DISPLAY}' did not appear within {NESTED_TIMEOUT}s",
        )

    time.sleep(2)   # let compositor settle

    # Start server inside nested session
    server_proc = None
    try:
        server_proc = server_manager.start(NESTED_PORT, report_dir, wayland_display=NESTED_DISPLAY)
    except RuntimeError as e:
        nested_proc.terminate()
        nested_log.close()
        return warned("Nested Session", f"Server failed to start in nested session: {e}")

    # Connect mock client
    result = None
    c = MockClient()
    try:
        c.connect("127.0.0.1", NESTED_PORT, "NestedTest", 1920, 1080, timeout=15.0)
        status, _ = c.read_response()
        if status != "ok":
            result = failed("Nested Session", f"Got {status} instead of TLOK inside nested session")
        else:
            c._sock.settimeout(10.0)
            frame = c.read_frame()
            if frame and frame[:2] == b"\xff\xd8":
                result = passed("Nested Session", "Full stack works inside gnome-shell --nested")
            else:
                result = failed("Nested Session", "No valid JPEG frame received inside nested session")
    except Exception as e:
        result = warned("Nested Session", f"Exception in nested session test: {e}")
    finally:
        c.close()
        if server_proc:
            server_manager.stop(server_proc)
        nested_proc.terminate()
        nested_log.close()

    return result
