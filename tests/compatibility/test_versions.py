import re
import subprocess
import sys
from tests.lib.result import TestResult, passed, failed, warned


def _run(cmd: list) -> str:
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=10).decode().strip()
    except Exception as e:
        return f"ERROR: {e}"


def _parse_version(text: str) -> tuple:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return (0, 0, 0)
    return tuple(int(x or 0) for x in m.groups())


def run(report_dir: str) -> TestResult:
    failures = []
    warnings = []
    info     = []

    # Python
    py = sys.version_info
    if (py.major, py.minor) < (3, 10):
        failures.append(f"Python {py.major}.{py.minor} < 3.10")
    else:
        info.append(f"Python {py.major}.{py.minor}.{py.micro} ✓")

    # GStreamer
    gst_out = _run(["gst-inspect-1.0", "--version"])
    gst_ver = _parse_version(gst_out)
    if gst_ver < (1, 20, 0):
        failures.append(f"GStreamer {gst_out} < 1.20")
    else:
        info.append(f"GStreamer {'.'.join(str(x) for x in gst_ver)} ✓")

    # PipeWire
    pw_out = _run(["pw-cli", "--version"])
    pw_ver = _parse_version(pw_out)
    if pw_ver < (0, 3, 48):
        failures.append(f"PipeWire {pw_out} < 0.3.48")
    else:
        info.append(f"PipeWire {'.'.join(str(x) for x in pw_ver)} ✓")

    # GTK4
    gtk_out = _run(["python3", "-c",
        "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk; "
        "print(f'{Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}.{Gtk.MICRO_VERSION}')"])
    if "ERROR" in gtk_out or "4." not in gtk_out:
        failures.append(f"GTK4 not available: {gtk_out}")
    else:
        info.append(f"GTK4 {gtk_out} ✓")

    # gstreamer1.0-pipewire plugin
    pw_plugin = _run(["gst-inspect-1.0", "pipewiresrc"])
    if "ERROR" in pw_plugin or "No such element" in pw_plugin:
        failures.append("gstreamer1.0-pipewire plugin (pipewiresrc) not found")
    else:
        info.append("gstreamer1.0-pipewire (pipewiresrc) ✓")

    details = "\n".join(info + (["FAILURES:"] + failures if failures else []))
    if failures:
        return failed("Version Check", "; ".join(failures), details)
    return passed("Version Check", f"{len(info)} dependencies OK")
