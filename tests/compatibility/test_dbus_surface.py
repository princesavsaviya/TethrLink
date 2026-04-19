import subprocess
from tests.lib.result import TestResult, passed, failed, warned

# Each tuple: (interface, method_or_signal, is_signal)
REQUIRED_APIS = [
    ("org.gnome.Mutter.ScreenCast",         "CreateSession",       False),
    ("org.gnome.Mutter.ScreenCast.Session", "RecordVirtual",       False),
    ("org.gnome.Mutter.ScreenCast.Session", "Start",               False),
    ("org.gnome.Mutter.ScreenCast.Session", "Stop",                False),
    ("org.gnome.Mutter.ScreenCast.Stream",  "PipeWireStreamAdded", True),
    ("org.gnome.Mutter.DisplayConfig",      "GetCurrentState",     False),
    ("org.gnome.Mutter.DisplayConfig",      "MonitorsChanged",     True),
]

KNOWN_GNOME_VERSIONS = {"42", "44", "46", "47", "48"}


def _introspect(interface: str) -> str:
    obj_path = "/" + interface.replace(".", "/")
    try:
        out = subprocess.check_output(
            ["gdbus", "introspect", "--session",
             "--dest", "org.gnome.Mutter.ScreenCast",
             "--object-path", obj_path],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode()
        return out
    except Exception:
        pass
    # Try DisplayConfig path
    try:
        out = subprocess.check_output(
            ["gdbus", "introspect", "--session",
             "--dest", "org.gnome.Mutter.DisplayConfig",
             "--object-path", "/org/gnome/Mutter/DisplayConfig"],
            stderr=subprocess.DEVNULL, timeout=10,
        ).decode()
        return out
    except Exception:
        return ""


def _gnome_version() -> str:
    try:
        return subprocess.check_output(
            ["gnome-shell", "--version"], stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
    except Exception:
        return "unknown"


def run(report_dir: str) -> TestResult:
    missing  = []
    present  = []

    for interface, name, is_signal in REQUIRED_APIS:
        xml = _introspect(interface)
        kind = "signal" if is_signal else "method"
        if name in xml:
            present.append(f"  ✓ {interface}.{name} ({kind})")
        else:
            missing.append(f"  ✗ {interface}.{name} ({kind}) — NOT FOUND")

    gnome_ver = _gnome_version()
    ver_num = gnome_ver.split()[-1].split(".")[0] if gnome_ver != "unknown" else "?"
    ver_warn = ver_num not in KNOWN_GNOME_VERSIONS

    details = "\n".join([f"GNOME: {gnome_ver}"] + present + (missing if missing else []))

    if missing:
        return failed(
            "D-Bus API Surface",
            f"{len(missing)} required APIs missing — TetherLink will not work on this GNOME version",
            details,
        )
    if ver_warn:
        return warned(
            "D-Bus API Surface",
            f"All APIs present but GNOME {ver_num} is untested",
            details,
        )
    return passed("D-Bus API Surface", f"All {len(present)} APIs present (GNOME {ver_num})")
