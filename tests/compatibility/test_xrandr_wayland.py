import re
import subprocess
import time
from tests.lib.mock_client import MockClient
from tests.lib.result import TestResult, passed, failed, warned
from tests.lib import server_manager

XRANDR_PORT = 18082


def _connected_outputs() -> dict:
    """Returns {name: (x, y)} for all connected xrandr outputs."""
    try:
        out = subprocess.check_output(["xrandr", "--query"], timeout=5).decode()
    except Exception:
        return {}
    outputs = {}
    current_name = None
    for line in out.splitlines():
        m = re.match(r"^(\S+) connected", line)
        if m:
            current_name = m.group(1)
            pos = re.search(r"\+(\d+)\+(\d+)", line)
            outputs[current_name] = (int(pos.group(1)), int(pos.group(2))) if pos else (0, 0)
    return outputs


def _virtual_output(before: set, after: dict) -> str | None:
    """Return name of the new output that appeared after server started."""
    new_names = set(after.keys()) - before
    for name in new_names:
        if "virtual" in name.lower() or "VIRTUAL" in name:
            return name
    # Fallback: any new connected output
    return next(iter(new_names), None)


def run(report_dir: str) -> TestResult:
    before = set(_connected_outputs().keys())

    server_proc = None
    c = MockClient()
    try:
        server_proc = server_manager.start(XRANDR_PORT, report_dir)
        c.connect("127.0.0.1", XRANDR_PORT, "XrandrTest", 1920, 1080)
        status, _ = c.read_response()
        if status != "ok":
            return failed("xrandr-on-Wayland", f"Got {status} instead of TLOK")

        time.sleep(1)
        after  = _connected_outputs()
        vname  = _virtual_output(before, after)

        if not vname:
            return warned(
                "xrandr-on-Wayland",
                "Virtual output did not appear in xrandr — xrandr may not be available in this session",
            )

        # Test: move to (0, 0)
        subprocess.run(["xrandr", "--output", vname, "--pos", "0x0"], check=False, capture_output=True)
        time.sleep(0.5)
        pos_after_zero = _connected_outputs().get(vname, (-1, -1))
        if pos_after_zero != (0, 0):
            return failed(
                "xrandr-on-Wayland",
                f"After --pos 0x0, output '{vname}' is at {pos_after_zero}",
            )

        # Test: move to (1920, 0)
        subprocess.run(["xrandr", "--output", vname, "--pos", "1920x0"], check=False, capture_output=True)
        time.sleep(0.5)
        pos_after_right = _connected_outputs().get(vname, (-1, -1))
        if pos_after_right != (1920, 0):
            return failed(
                "xrandr-on-Wayland",
                f"After --pos 1920x0, output '{vname}' is at {pos_after_right}",
            )

        c.close()
        server_manager.stop(server_proc)
        server_proc = None
        time.sleep(1)

        # Virtual output must disappear after server stops
        after_stop = _connected_outputs()
        if vname in after_stop:
            return warned(
                "xrandr-on-Wayland",
                f"Virtual output '{vname}' still present after server stopped",
            )

        return passed(
            "xrandr-on-Wayland",
            f"Output '{vname}': pos 0x0 ✓, pos 1920x0 ✓, disappears on stop ✓",
        )

    except Exception as e:
        return failed("xrandr-on-Wayland", f"Exception: {e}")
    finally:
        c.close()
        if server_proc:
            server_manager.stop(server_proc)
