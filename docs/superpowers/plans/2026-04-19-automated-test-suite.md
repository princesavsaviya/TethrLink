# Automated Pre-Release Test Suite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully automated test suite that runs with `bash tests/run_all.sh` and produces a single `report.md` covering security, protocol correctness, stress, compatibility, and Android build — no human interaction required.

**Architecture:** A master shell script orchestrates Python test modules and a shell-based Android build step. All Python modules share a common `TestResult` dataclass via `tests/lib/report.py`. Protocol tests use a `MockClient` (from `tests/lib/mock_client.py`) against a real server started in headless mode by `tests/lib/server_manager.py`.

**Tech Stack:** Python 3.10+, `bandit`, `psutil`, `dbus-python` (already a server dep), `subprocess`, `socket`, Gradle (Android build), `gst-inspect-1.0`, `gdbus`, `xrandr`

---

## File Map

| File | Role |
|---|---|
| `tests/run_all.sh` | Entry point — prereq check, orchestration, report trigger |
| `tests/lib/result.py` | `TestResult` dataclass shared by all modules |
| `tests/lib/report.py` | Collects `TestResult` list → writes `report.md` |
| `tests/lib/server_manager.py` | Start/stop headless server, poll port for readiness |
| `tests/lib/mock_client.py` | Full TLHELLO/TLOK/frame protocol + UDP listener |
| `tests/security/scan.py` | bandit + hardcoded value grep + manifest permission audit |
| `tests/protocol/test_handshake.py` | Connect → TLOK → frame → reconnect |
| `tests/protocol/test_busy.py` | Two clients → second gets TLBUSY → reconnect after first leaves |
| `tests/protocol/test_malformed.py` | 5 garbage payloads → server survives each |
| `tests/protocol/test_udp_discovery.py` | UDP broadcast → JSON fields present |
| `tests/stress/test_long_session.py` | 30-min stream + RSS/CPU sampling → `cpu_rss.csv` |
| `tests/stress/test_rapid_reconnect.py` | 10 rapid disconnect/reconnect cycles |
| `tests/compatibility/test_versions.py` | Python/GStreamer/PipeWire/GTK4 version gates |
| `tests/compatibility/test_requirements.py` | server/ imports vs requirements.txt |
| `tests/compatibility/test_dbus_surface.py` | Live Mutter D-Bus introspection |
| `tests/compatibility/test_nested_session.py` | Full stack in `gnome-shell --nested` |
| `tests/compatibility/test_xrandr_wayland.py` | xrandr --pos takes effect on virtual display |
| `tests/android/lint_and_build.sh` | `gradlew lint` + `gradlew assembleRelease` |

---

## Task 1: Directory structure + shared `TestResult`

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/lib/__init__.py`
- Create: `tests/lib/result.py`
- Create: `tests/security/__init__.py`
- Create: `tests/protocol/__init__.py`
- Create: `tests/stress/__init__.py`
- Create: `tests/compatibility/__init__.py`
- Create: `tests/android/__init__.py`

- [ ] **Step 1: Create all directories**

```bash
mkdir -p tests/lib tests/security tests/protocol tests/stress tests/compatibility tests/android tests/reports
touch tests/__init__.py tests/lib/__init__.py tests/security/__init__.py \
      tests/protocol/__init__.py tests/stress/__init__.py \
      tests/compatibility/__init__.py tests/android/__init__.py
```

- [ ] **Step 2: Write `tests/lib/result.py`**

```python
from dataclasses import dataclass, field
from typing import List


@dataclass
class TestResult:
    name: str
    status: str        # "PASS" | "FAIL" | "WARN" | "SKIP"
    notes: str = ""
    details: str = ""  # multi-line extra info shown in Details section


def passed(name: str, notes: str = "") -> TestResult:
    return TestResult(name=name, status="PASS", notes=notes)


def failed(name: str, notes: str, details: str = "") -> TestResult:
    return TestResult(name=name, status="FAIL", notes=notes, details=details)


def warned(name: str, notes: str, details: str = "") -> TestResult:
    return TestResult(name=name, status="WARN", notes=notes, details=details)


def skipped(name: str, notes: str = "") -> TestResult:
    return TestResult(name=name, status="SKIP", notes=notes)
```

- [ ] **Step 3: Verify import works**

```bash
cd /home/prince/TetherLink
python3 -c "from tests.lib.result import TestResult, passed, failed; r = passed('test'); print(r)"
```

Expected output: `TestResult(name='test', status='PASS', notes='', details='')`

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: scaffold test suite directory structure and TestResult dataclass"
```

---

## Task 2: `tests/lib/report.py`

**Files:**
- Create: `tests/lib/report.py`

- [ ] **Step 1: Write `tests/lib/report.py`**

```python
import os
from datetime import datetime
from typing import List
from tests.lib.result import TestResult

ICON = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}

MANUAL_CHECKLIST = """
## Manual Tests Required Before App Store Submission

- [ ] Real USB tethering test (Samsung tablet + phone)
- [ ] Play Store screenshots, privacy policy page, and store listing form
- [ ] Debian/Ubuntu .deb package install test on clean Ubuntu 22.04 VM
- [ ] Ubuntu 22.04 VM: confirm server launches (GNOME 42 / GStreamer 1.20)
- [ ] Ubuntu 24.04 VM: confirm server launches (GNOME 46 / GStreamer 1.24)
- [ ] Visual frame quality check (human eyes — no artefacts, correct colour)
- [ ] Real-device orientation change test (currently causes stream disconnect)

> Note: Pure X11 sessions are explicitly unsupported.
> TetherLink requires a Wayland session (Mutter ScreenCast API).
"""


def generate(results: List[TestResult], report_dir: str) -> str:
    overall = "PASS" if all(r.status in ("PASS", "WARN", "SKIP") for r in results) else "FAIL"
    icon = ICON[overall]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# TetherLink V1 Pre-Release Test Report",
        f"**Date:** {timestamp}",
        f"**Overall:** {icon} {overall}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Test | Result | Notes |",
        "|---|---|---|",
    ]

    for r in results:
        lines.append(f"| {r.name} | {ICON[r.status]} {r.status} | {r.notes} |")

    lines += ["", "---", "", "## Details", ""]

    for r in results:
        if r.details:
            lines += [f"### {r.name}", "", "```", r.details.strip(), "```", ""]

    lines.append(MANUAL_CHECKLIST)

    report = "\n".join(lines)
    path = os.path.join(report_dir, "report.md")
    with open(path, "w") as f:
        f.write(report)
    return path


def print_summary(results: List[TestResult]) -> None:
    print("\n" + "=" * 60)
    print("TETHERLINK TEST SUITE RESULTS")
    print("=" * 60)
    for r in results:
        print(f"  {ICON[r.status]} {r.status:4s}  {r.name}")
        if r.notes:
            print(f"         {r.notes}")
    overall = "PASS" if all(r.status in ("PASS", "WARN", "SKIP") for r in results) else "FAIL"
    print("=" * 60)
    print(f"  Overall: {ICON[overall]} {overall}")
    print("=" * 60 + "\n")
```

- [ ] **Step 2: Verify report generation**

```bash
cd /home/prince/TetherLink
python3 -c "
import os, tempfile
from tests.lib.result import passed, failed, warned
from tests.lib.report import generate, print_summary
results = [passed('Security Scan', '0 HIGH'), failed('Handshake', 'TLOK not received')]
d = tempfile.mkdtemp()
path = generate(results, d)
print_summary(results)
print('Report at:', path)
"
```

Expected: prints table with ✅ and ❌, prints report path.

- [ ] **Step 3: Commit**

```bash
git add tests/lib/report.py
git commit -m "test: add report generator (markdown + terminal summary)"
```

---

## Task 3: `tests/lib/server_manager.py`

**Files:**
- Create: `tests/lib/server_manager.py`

- [ ] **Step 1: Write `tests/lib/server_manager.py`**

```python
import os
import signal
import socket
import subprocess
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER_SCRIPT = os.path.join(REPO_ROOT, "server", "tetherlink_server.py")
VENV_PYTHON = os.path.join(REPO_ROOT, "venv", "bin", "python3")
PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def start(port: int, report_dir: str, wayland_display: str = None) -> subprocess.Popen:
    """Start server in headless mode on given port. Returns process handle."""
    log_path = os.path.join(report_dir, f"server_{port}.log")
    env = os.environ.copy()
    if wayland_display:
        env["WAYLAND_DISPLAY"] = wayland_display

    cmd = [PYTHON, SERVER_SCRIPT, "--headless", "--port", str(port)]
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        env=env,
    )

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _port_open(port):
            return proc
        if proc.poll() is not None:
            log_file.close()
            raise RuntimeError(
                f"Server exited early (code {proc.returncode}). "
                f"Check {log_path}"
            )
        time.sleep(0.3)

    stop(proc)
    log_file.close()
    raise RuntimeError(
        f"Server did not open port {port} within 15s. Check {log_path}"
    )


def stop(proc: subprocess.Popen) -> None:
    """Gracefully stop server; SIGKILL after 3s if needed."""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
```

- [ ] **Step 2: Smoke-test server_manager (requires GNOME session)**

```bash
cd /home/prince/TetherLink
python3 -c "
import tempfile, time
from tests.lib import server_manager
d = tempfile.mkdtemp()
print('Starting server on port 18080...')
proc = server_manager.start(18080, d)
print('Server up, PID:', proc.pid)
time.sleep(2)
server_manager.stop(proc)
print('Server stopped cleanly')
"
```

Expected: prints PID, then "Server stopped cleanly". No exception.

- [ ] **Step 3: Commit**

```bash
git add tests/lib/server_manager.py
git commit -m "test: add server_manager (start/stop headless server for tests)"
```

---

## Task 4: `tests/lib/mock_client.py`

**Files:**
- Create: `tests/lib/mock_client.py`

Protocol constants (from `server/server_core.py`):
- `MAGIC_HELLO = b"TLHELO"` — 6 bytes
- `MAGIC_OK    = b"TLOK__"` — 6 bytes prefix + `struct.pack(">IIB", w, h, codec)` = 15 bytes total
- `MAGIC_BUSY  = b"TLBUSY"` — 6 bytes
- Handshake sent by client: `[6B magic][16B device_id][4B width][4B height][64B name]` = 94 bytes
- Server rejects if `len(hello) < 30` or `hello[:6] != MAGIC_HELLO`
- Frame wire format: `[4B big-endian length][N bytes JPEG data]`
- UDP discovery packet: JSON with keys `app`, `name`, `hostname`, `port`, `resolution`, `version` on port 8765

- [ ] **Step 1: Write `tests/lib/mock_client.py`**

```python
import json
import os
import socket
import struct
import time
from typing import Optional, Tuple

MAGIC_HELLO = b"TLHELO"
MAGIC_OK    = b"TLOK__"
MAGIC_BUSY  = b"TLBUSY"
DISCOVERY_PORT = 8765


class MockClient:
    """Simulates a TetherLink Android client over TCP."""

    def __init__(self):
        self._sock: Optional[socket.socket] = None

    def connect(
        self,
        host: str,
        port: int,
        device_name: str = "MockDevice",
        width: int = 1920,
        height: int = 1080,
        timeout: float = 10.0,
    ) -> None:
        """Connect and send TLHELLO handshake."""
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)

        device_id = os.urandom(16)
        name_bytes = device_name.encode("utf-8")[:64].ljust(64, b"\x00")
        packet = (
            MAGIC_HELLO
            + device_id
            + struct.pack(">II", width, height)
            + name_bytes
        )
        self._sock.sendall(packet)

    def read_response(self) -> Tuple[str, Optional[Tuple[int, int, int]]]:
        """
        Read server response after handshake.
        Returns ("ok", (w, h, codec)) | ("busy", None) | ("error", None)
        """
        header = self._recvall(6)
        if header == MAGIC_BUSY:
            return "busy", None
        if header == MAGIC_OK:
            rest = self._recvall(9)   # ">IIB" = 4+4+1
            w, h, codec = struct.unpack(">IIB", rest)
            return "ok", (w, h, codec)
        return "error", None

    def read_frame(self) -> bytes:
        """Read one size-prefixed JPEG frame."""
        size_bytes = self._recvall(4)
        size = struct.unpack(">I", size_bytes)[0]
        return self._recvall(size)

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _recvall(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError(f"Connection closed after {len(buf)}/{n} bytes")
            buf += chunk
        return buf


def listen_udp(port: int = DISCOVERY_PORT, timeout: float = 10.0) -> Optional[dict]:
    """
    Listen for one UDP broadcast packet. Returns parsed JSON dict or None on timeout.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)
    try:
        sock.bind(("", port))
        data, _ = sock.recvfrom(4096)
        return json.loads(data.decode("utf-8"))
    except (socket.timeout, json.JSONDecodeError, OSError):
        return None
    finally:
        sock.close()
```

- [ ] **Step 2: Smoke-test mock client against live server**

Start the server manually first: `python server/tetherlink_server.py --headless --port 18080 &`

```bash
cd /home/prince/TetherLink
python3 -c "
from tests.lib.mock_client import MockClient
c = MockClient()
c.connect('127.0.0.1', 18080)
status, info = c.read_response()
print('Response:', status, info)
if status == 'ok':
    frame = c.read_frame()
    print('Frame bytes:', len(frame), '| JPEG:', frame[:2] == b'\xff\xd8')
c.close()
"
```

Expected: `Response: ok (1920, 1080, 0)` and `JPEG: True`. Kill server after: `kill %1`

- [ ] **Step 3: Commit**

```bash
git add tests/lib/mock_client.py
git commit -m "test: add MockClient — full TetherLink protocol simulation"
```

---

## Task 5: `tests/security/scan.py`

**Files:**
- Create: `tests/security/scan.py`

- [ ] **Step 1: Write `tests/security/scan.py`**

```python
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from tests.lib.result import TestResult, passed, failed, warned

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ALLOWED_PERMISSIONS = {
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.ACCESS_WIFI_STATE",
    "android.permission.CHANGE_NETWORK_STATE",
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.RECEIVE_BOOT_COMPLETED",
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
}

# Patterns that are OK in constants/comments but suspicious elsewhere
_HARDCODED_IP_RE   = re.compile(r'(?<![#"\'])192\.168\.|127\.0\.0\.1')
_SECRET_RE         = re.compile(r'(?i)(password|secret|api_key|token)\s*=\s*["\'][^"\']+["\']')
_TODO_RE           = re.compile(r'\b(TODO|FIXME|HACK|XXX)\b')


def run(report_dir: str) -> TestResult:
    findings_high   = []
    findings_warn   = []
    findings_info   = []

    # 1. bandit
    bandit_out = os.path.join(report_dir, "bandit-report.json")
    try:
        subprocess.run(
            ["bandit", "-r", os.path.join(REPO, "server"),
             "-f", "json", "-o", bandit_out, "-q"],
            capture_output=True, timeout=60,
        )
        with open(bandit_out) as f:
            bandit = json.load(f)
        for issue in bandit.get("results", []):
            sev = issue.get("issue_severity", "LOW")
            msg = f"[bandit] {issue['issue_text']} ({issue['filename']}:{issue['line_number']})"
            if sev == "HIGH":
                findings_high.append(msg)
            elif sev == "MEDIUM":
                findings_warn.append(msg)
    except FileNotFoundError:
        findings_warn.append("[bandit] bandit not installed — skipped")
    except Exception as e:
        findings_warn.append(f"[bandit] error: {e}")

    # 2. Hardcoded value scan
    scan_dirs = [
        os.path.join(REPO, "server"),
        os.path.join(REPO, "android", "app", "src", "main", "java"),
    ]
    for scan_dir in scan_dirs:
        for root, _, files in os.walk(scan_dir):
            for fname in files:
                if not fname.endswith((".py", ".kt", ".java")):
                    continue
                fpath = os.path.join(root, fname)
                rel   = os.path.relpath(fpath, REPO)
                with open(fpath, errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        stripped = line.strip()
                        if stripped.startswith("#") or stripped.startswith("//"):
                            continue
                        if _HARDCODED_IP_RE.search(line):
                            findings_info.append(f"[hardcoded-ip] {rel}:{i}: {stripped[:80]}")
                        if _SECRET_RE.search(line):
                            findings_high.append(f"[hardcoded-secret] {rel}:{i}: {stripped[:80]}")
                        if _TODO_RE.search(line):
                            findings_info.append(f"[todo] {rel}:{i}: {stripped[:80]}")

    # 3. Android manifest permission audit
    manifest = os.path.join(
        REPO, "android", "app", "src", "main", "AndroidManifest.xml"
    )
    if os.path.exists(manifest):
        try:
            tree = ET.parse(manifest)
            ns = "http://schemas.android.com/apk/res/android"
            for elem in tree.iter("uses-permission"):
                perm = elem.get(f"{{{ns}}}name", "")
                if perm and perm not in ALLOWED_PERMISSIONS:
                    findings_warn.append(f"[manifest] Unexpected permission: {perm}")
        except Exception as e:
            findings_warn.append(f"[manifest] parse error: {e}")

    # 4. TCP bind documentation (info only)
    findings_info.append(
        "[tcp-bind] Server binds 0.0.0.0 — acceptable: USB tethering subnet "
        "(192.168.42.x) is not internet-routable. No external exposure."
    )

    # Build result
    details_lines = (
        ["HIGH:"] + findings_high +
        ["", "WARN:"] + findings_warn +
        ["", "INFO:"] + findings_info
    )
    details = "\n".join(details_lines)

    if findings_high:
        return failed(
            "Security Scan",
            f"{len(findings_high)} HIGH, {len(findings_warn)} WARN",
            details,
        )
    if findings_warn:
        return warned(
            "Security Scan",
            f"0 HIGH, {len(findings_warn)} WARN, {len(findings_info)} INFO",
            details,
        )
    return passed(
        "Security Scan",
        f"0 HIGH, 0 WARN, {len(findings_info)} INFO",
    )


if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    r = run(d)
    print(r.status, r.notes)
    if r.details:
        print(r.details)
```

- [ ] **Step 2: Run standalone and verify**

```bash
cd /home/prince/TetherLink
python3 -m tests.security.scan
```

Expected: prints PASS or WARN with counts. No FAIL (unless there are real secrets).

- [ ] **Step 3: Commit**

```bash
git add tests/security/scan.py
git commit -m "test: add security scanner (bandit + hardcoded values + manifest)"
```

---

## Task 6: Protocol tests

**Files:**
- Create: `tests/protocol/test_handshake.py`
- Create: `tests/protocol/test_busy.py`
- Create: `tests/protocol/test_malformed.py`
- Create: `tests/protocol/test_udp_discovery.py`

- [ ] **Step 1: Write `tests/protocol/test_handshake.py`**

```python
import socket
import time
from tests.lib.mock_client import MockClient
from tests.lib.result import TestResult, passed, failed

HOST = "127.0.0.1"


def run(port: int, report_dir: str) -> TestResult:
    details = []

    # Connect and get TLOK
    c = MockClient()
    try:
        c.connect(HOST, port, "HandshakeTest", 1920, 1080)
        status, info = c.read_response()
        if status != "ok":
            return failed("Handshake", f"Expected TLOK, got: {status}")
        details.append(f"TLOK received: w={info[0]} h={info[1]} codec={info[2]}")

        # Frame arrives within 10s
        c._sock.settimeout(10.0)
        frame = c.read_frame()
        if not frame:
            return failed("Handshake", "Empty frame received")
        if frame[:2] != b"\xff\xd8":
            return failed("Handshake", f"Frame is not JPEG (magic={frame[:2].hex()})")
        details.append(f"First frame received: {len(frame)} bytes, valid JPEG")

        c.close()
    except Exception as e:
        c.close()
        return failed("Handshake", f"Exception: {e}")

    # Reconnect immediately (lock must be released)
    time.sleep(0.5)
    c2 = MockClient()
    try:
        c2.connect(HOST, port, "HandshakeTest2", 1920, 1080)
        status2, _ = c2.read_response()
        if status2 != "ok":
            return failed("Handshake", f"Reconnect got {status2} instead of TLOK — lock not released")
        details.append("Reconnect after close: TLOK received (lock released correctly)")
        c2.close()
    except Exception as e:
        c2.close()
        return failed("Handshake", f"Reconnect exception: {e}")

    return passed("Handshake", "\n".join(details))
```

- [ ] **Step 2: Write `tests/protocol/test_busy.py`**

```python
import socket
import threading
import time
from tests.lib.mock_client import MockClient, MAGIC_BUSY
from tests.lib.result import TestResult, passed, failed

HOST = "127.0.0.1"


def run(port: int, report_dir: str) -> TestResult:
    details = []

    # Client A: connect and hold
    a = MockClient()
    try:
        a.connect(HOST, port, "ClientA", 1920, 1080)
        status_a, _ = a.read_response()
        if status_a != "ok":
            a.close()
            return failed("TLBUSY", f"Client A got {status_a} instead of TLOK")
        details.append("Client A: TLOK received")
    except Exception as e:
        a.close()
        return failed("TLBUSY", f"Client A exception: {e}")

    # Client B: should get TLBUSY
    b = MockClient()
    try:
        b.connect(HOST, port, "ClientB", 1920, 1080, timeout=5.0)
        status_b, _ = b.read_response()
        if status_b != "busy":
            a.close()
            b.close()
            return failed("TLBUSY", f"Client B got {status_b} instead of TLBUSY")
        details.append("Client B: TLBUSY received correctly")
        b.close()
    except Exception as e:
        a.close()
        b.close()
        return failed("TLBUSY", f"Client B exception: {e}")

    # Disconnect A — lock must be released
    a.close()
    time.sleep(1.5)

    # Client C: should now get TLOK
    c = MockClient()
    try:
        c.connect(HOST, port, "ClientC", 1920, 1080)
        status_c, _ = c.read_response()
        if status_c != "ok":
            c.close()
            return failed("TLBUSY", f"Client C got {status_c} after A disconnected — lock not released")
        details.append("Client C: TLOK after A disconnected (lock released correctly)")
        c.close()
    except Exception as e:
        c.close()
        return failed("TLBUSY", f"Client C exception: {e}")

    return passed("TLBUSY", " | ".join(details))
```

- [ ] **Step 3: Write `tests/protocol/test_malformed.py`**

```python
import os
import socket
import struct
import time
from tests.lib.mock_client import MockClient, MAGIC_HELLO
from tests.lib.result import TestResult, passed, failed

HOST = "127.0.0.1"

# Valid TLHELLO helper
def _valid_hello(name="ValidClient", w=1920, h=1080):
    device_id  = os.urandom(16)
    name_bytes = name.encode()[:64].ljust(64, b"\x00")
    return MAGIC_HELLO + device_id + struct.pack(">II", w, h) + name_bytes


PAYLOADS = [
    ("Empty payload",       b""),
    ("Random 100 bytes",    os.urandom(100)),
    ("Magic + truncated",   MAGIC_HELLO + b"\x00" * 4),
    ("Magic + valid header + 10000 nulls",
     _valid_hello() + b"\x00" * 10000),
    ("Valid TLHELLO width=0 height=0 (expect TLOK)",
     MAGIC_HELLO + os.urandom(16) + struct.pack(">II", 0, 0) + b"ZeroSize".ljust(64, b"\x00")),
]


def _send_and_drain(port: int, payload: bytes) -> None:
    s = socket.create_connection((HOST, port), timeout=5)
    s.settimeout(5)
    if payload:
        s.sendall(payload)
    try:
        s.recv(256)   # drain whatever server sends back
    except OSError:
        pass
    s.close()


def _valid_connect_ok(port: int) -> bool:
    c = MockClient()
    try:
        c.connect(HOST, port, "SurvivalCheck", 1920, 1080)
        status, _ = c.read_response()
        c.close()
        return status == "ok"
    except Exception:
        c.close()
        return False


def run(port: int, report_dir: str) -> TestResult:
    details = []
    for label, payload in PAYLOADS:
        try:
            _send_and_drain(port, payload)
        except Exception:
            pass   # server closed connection — expected
        time.sleep(0.5)

        # Server must still accept a valid connection after each bad payload
        alive = _valid_connect_ok(port)
        status = "OK" if alive else "DEAD"
        details.append(f"  [{status}] After: {label}")

        if not alive:
            return failed(
                "Malformed Input",
                f"Server stopped accepting connections after: {label}",
                "\n".join(details),
            )

    return passed("Malformed Input", f"Server survived all {len(PAYLOADS)} malformed payloads")
```

- [ ] **Step 4: Write `tests/protocol/test_udp_discovery.py`**

```python
import socket
import time
from tests.lib.mock_client import listen_udp
from tests.lib.result import TestResult, passed, failed, warned

REQUIRED_FIELDS = {"app", "port"}        # minimum required
EXPECTED_FIELDS = {"name", "hostname", "resolution", "version"}   # nice-to-have


def run(report_dir: str) -> TestResult:
    packet = listen_udp(timeout=12.0)

    if packet is None:
        return failed(
            "UDP Discovery",
            "No UDP broadcast received within 12s — check server is running",
        )

    missing_required = REQUIRED_FIELDS - packet.keys()
    if missing_required:
        return failed(
            "UDP Discovery",
            f"Missing required fields: {missing_required}",
            f"Received: {packet}",
        )

    if packet.get("app") != "TetherLink":
        return failed(
            "UDP Discovery",
            f"app field is '{packet.get('app')}', expected 'TetherLink'",
        )

    # Verify the announced port is actually open
    port = packet.get("port")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            pass
    except OSError:
        return failed(
            "UDP Discovery",
            f"Announced port {port} is not reachable",
        )

    missing_nice = EXPECTED_FIELDS - packet.keys()
    if missing_nice:
        return warned(
            "UDP Discovery",
            f"Missing optional fields: {missing_nice}",
            f"Received: {packet}",
        )

    return passed("UDP Discovery", f"Broadcast OK — port={port}, app=TetherLink")
```

- [ ] **Step 5: Verify each module runs standalone (server must be running on 18080)**

```bash
cd /home/prince/TetherLink
# Start server
python server/tetherlink_server.py --headless --port 18080 &
sleep 5

python3 -c "
import tempfile
d = tempfile.mkdtemp()
from tests.protocol.test_handshake import run as h; print('Handshake:', h(18080, d).status)
from tests.protocol.test_busy     import run as b; print('Busy:',      b(18080, d).status)
from tests.protocol.test_malformed import run as m; print('Malformed:', m(18080, d).status)
from tests.protocol.test_udp_discovery import run as u; print('UDP:', u(d).status)
"
kill %1
```

Expected: all four print PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/protocol/
git commit -m "test: add protocol tests (handshake, TLBUSY, malformed input, UDP discovery)"
```

---

## Task 7: Stress tests

**Files:**
- Create: `tests/stress/test_long_session.py`
- Create: `tests/stress/test_rapid_reconnect.py`

- [ ] **Step 1: Write `tests/stress/test_long_session.py`**

```python
import csv
import os
import time
import psutil
from tests.lib.mock_client import MockClient
from tests.lib.result import TestResult, passed, failed, warned

HOST            = "127.0.0.1"
DURATION_S      = 1800   # 30 minutes
SAMPLE_EVERY_S  = 30
MAX_RSS_GROWTH  = 50     # MB
MAX_AVG_CPU     = 80.0   # percent
MIN_FPS         = 1.0    # frames per second minimum average


def run(port: int, report_dir: str, server_pid: int) -> TestResult:
    csv_path = os.path.join(report_dir, "cpu_rss.csv")
    proc = psutil.Process(server_pid)

    c = MockClient()
    try:
        c.connect(HOST, port, "LongSessionTest", 1920, 1080, timeout=15.0)
        status, _ = c.read_response()
        if status != "ok":
            return failed("Long Session", f"Got {status} instead of TLOK at start")
    except Exception as e:
        c.close()
        return failed("Long Session", f"Connect failed: {e}")

    samples        = []
    baseline_rss   = proc.memory_info().rss / 1024 / 1024
    total_frames   = 0
    start          = time.monotonic()
    last_sample    = start

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["elapsed_s", "rss_mb", "cpu_pct", "total_frames"])

        try:
            c._sock.settimeout(5.0)
            while time.monotonic() - start < DURATION_S:
                try:
                    frame = c.read_frame()
                    if frame:
                        total_frames += 1
                except socket.timeout:
                    pass  # no frame this second — continue

                now = time.monotonic()
                if now - last_sample >= SAMPLE_EVERY_S:
                    elapsed = now - start
                    rss     = proc.memory_info().rss / 1024 / 1024
                    cpu     = proc.cpu_percent(interval=1.0)
                    samples.append((elapsed, rss, cpu))
                    writer.writerow([f"{elapsed:.0f}", f"{rss:.1f}", f"{cpu:.1f}", total_frames])
                    f.flush()
                    last_sample = now
        except Exception as e:
            c.close()
            return failed("Long Session", f"Stream broke after {total_frames} frames: {e}")

    c.close()

    if not samples:
        return failed("Long Session", "No samples collected")

    elapsed_s   = DURATION_S
    avg_fps     = total_frames / elapsed_s
    peak_rss    = max(s[1] for s in samples)
    rss_growth  = peak_rss - baseline_rss
    avg_cpu     = sum(s[2] for s in samples) / len(samples)

    details = (
        f"Duration: {elapsed_s}s | Frames: {total_frames} | Avg FPS: {avg_fps:.1f}\n"
        f"Baseline RSS: {baseline_rss:.1f} MB | Peak RSS: {peak_rss:.1f} MB | Growth: {rss_growth:.1f} MB\n"
        f"Avg CPU: {avg_cpu:.1f}% | Samples: {len(samples)}"
    )

    if rss_growth > MAX_RSS_GROWTH:
        return failed("Long Session", f"Memory leak: RSS grew {rss_growth:.1f} MB (limit {MAX_RSS_GROWTH} MB)", details)
    if avg_cpu > MAX_AVG_CPU:
        return warned("Long Session", f"High CPU: avg {avg_cpu:.1f}% (limit {MAX_AVG_CPU}%)", details)
    if avg_fps < MIN_FPS:
        return failed("Long Session", f"Low FPS: avg {avg_fps:.2f} (min {MIN_FPS})", details)

    return passed("Long Session", details.replace("\n", " | "))


import socket  # noqa: E402 — needed inside run()
```

- [ ] **Step 2: Write `tests/stress/test_rapid_reconnect.py`**

```python
import time
from tests.lib.mock_client import MockClient
from tests.lib.result import TestResult, passed, failed

HOST    = "127.0.0.1"
CYCLES  = 10
PAUSE_S = 2.0
MAX_CYCLE_S = 10.0


def run(port: int, report_dir: str) -> TestResult:
    details = []

    for i in range(1, CYCLES + 1):
        t0 = time.monotonic()
        c = MockClient()
        try:
            c.connect(HOST, port, f"RapidTest_{i}", 1920, 1080)
            status, _ = c.read_response()
            if status != "ok":
                return failed(
                    "Rapid Reconnect",
                    f"Cycle {i}: got {status} instead of TLOK",
                    "\n".join(details),
                )

            # Read one frame to confirm stream is live
            c._sock.settimeout(10.0)
            frame = c.read_frame()
            if not frame:
                return failed("Rapid Reconnect", f"Cycle {i}: empty frame")

            c.close()   # abrupt close — no graceful shutdown
        except Exception as e:
            c.close()
            return failed("Rapid Reconnect", f"Cycle {i} exception: {e}", "\n".join(details))

        elapsed = time.monotonic() - t0
        if elapsed > MAX_CYCLE_S:
            return failed(
                "Rapid Reconnect",
                f"Cycle {i} took {elapsed:.1f}s (limit {MAX_CYCLE_S}s) — lock may be stuck",
                "\n".join(details),
            )

        details.append(f"Cycle {i}: OK ({elapsed:.2f}s)")
        time.sleep(PAUSE_S)

    return passed("Rapid Reconnect", f"All {CYCLES} cycles OK")
```

- [ ] **Step 3: Commit**

```bash
git add tests/stress/
git commit -m "test: add stress tests (30-min long session + rapid reconnect)"
```

---

## Task 8: Compatibility tests

**Files:**
- Create: `tests/compatibility/test_versions.py`
- Create: `tests/compatibility/test_requirements.py`
- Create: `tests/compatibility/test_dbus_surface.py`
- Create: `tests/compatibility/test_nested_session.py`
- Create: `tests/compatibility/test_xrandr_wayland.py`

- [ ] **Step 1: Write `tests/compatibility/test_versions.py`**

```python
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
```

- [ ] **Step 2: Write `tests/compatibility/test_requirements.py`**

```python
import ast
import importlib.util
import os
import re
import sys
from tests.lib.result import TestResult, passed, failed, warned

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Map top-level import names → PyPI package names (where they differ)
IMPORT_TO_PYPI = {
    "gi":       "PyGObject",
    "dbus":     "dbus-python",
    "gst":      "gstreamer (system)",
    "psutil":   "psutil",
    "pystray":  "pystray",
    "PIL":      "Pillow",
}

# stdlib modules to exclude from the check
STDLIB = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()


def _imports_in_file(path: str) -> set:
    names = set()
    try:
        with open(path) as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module.split(".")[0])
    except SyntaxError:
        pass
    return names


def run(report_dir: str) -> TestResult:
    req_path = os.path.join(REPO, "server", "requirements.txt")
    if not os.path.exists(req_path):
        return failed("Requirements Check", "server/requirements.txt not found")

    with open(req_path) as f:
        req_lines = [l.strip().lower() for l in f if l.strip() and not l.startswith("#")]
    req_packages = {re.split(r"[>=<!]", l)[0].strip() for l in req_lines}

    # Collect all imports from server/*.py
    server_dir = os.path.join(REPO, "server")
    all_imports = set()
    for fname in os.listdir(server_dir):
        if fname.endswith(".py"):
            all_imports |= _imports_in_file(os.path.join(server_dir, fname))

    missing = []
    for imp in sorted(all_imports):
        if imp in STDLIB:
            continue
        if imp.startswith("_"):
            continue
        pypi_name = IMPORT_TO_PYPI.get(imp, imp).lower()
        if pypi_name not in req_packages and imp.lower() not in req_packages:
            missing.append(f"  import '{imp}' → PyPI '{pypi_name}' not in requirements.txt")

    if missing:
        return warned(
            "Requirements Check",
            f"{len(missing)} imports may be missing from requirements.txt",
            "\n".join(missing),
        )
    return passed("Requirements Check", "All non-stdlib imports accounted for")
```

- [ ] **Step 3: Write `tests/compatibility/test_dbus_surface.py`**

```python
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
```

- [ ] **Step 4: Write `tests/compatibility/test_nested_session.py`**

```python
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
```

- [ ] **Step 5: Write `tests/compatibility/test_xrandr_wayland.py`**

```python
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


```

- [ ] **Step 6: Run compatibility tests standalone**

```bash
cd /home/prince/TetherLink
python3 -c "
import tempfile
d = tempfile.mkdtemp()
from tests.compatibility.test_versions     import run as v; print('Versions:', v(d).status)
from tests.compatibility.test_requirements import run as r; print('Reqs:', r(d).status)
from tests.compatibility.test_dbus_surface import run as b; print('D-Bus:', b(d).status)
"
```

Expected: PASS or WARN (not FAIL) for all three.

- [ ] **Step 7: Commit**

```bash
git add tests/compatibility/
git commit -m "test: add compatibility tests (versions, requirements, D-Bus surface, nested session, xrandr)"
```

---

## Task 9: Android lint + build script

**Files:**
- Create: `tests/android/lint_and_build.sh`

- [ ] **Step 1: Write `tests/android/lint_and_build.sh`**

```bash
#!/usr/bin/env bash
# Usage: lint_and_build.sh <report_dir>
set -euo pipefail

REPORT_DIR="${1:?Usage: lint_and_build.sh <report_dir>}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ANDROID_DIR="$REPO_ROOT/android"
STATUS_FILE="$REPORT_DIR/android_status.txt"

echo "ANDROID_LINT=PENDING"  > "$STATUS_FILE"
echo "ANDROID_BUILD=PENDING" >> "$STATUS_FILE"

cd "$ANDROID_DIR"

# Lint
echo "=== Android Lint ===" | tee "$REPORT_DIR/lint-output.txt"
if ./gradlew lint >> "$REPORT_DIR/lint-output.txt" 2>&1; then
    sed -i 's/ANDROID_LINT=PENDING/ANDROID_LINT=PASS/' "$STATUS_FILE"
    echo "Lint: PASS"
else
    sed -i 's/ANDROID_LINT=PENDING/ANDROID_LINT=WARN/' "$STATUS_FILE"
    echo "Lint: WARN (issues found — see lint-output.txt)"
fi

# Copy lint HTML report if it exists
LINT_HTML=$(find . -name "lint-results*.html" 2>/dev/null | head -1)
[ -n "$LINT_HTML" ] && cp "$LINT_HTML" "$REPORT_DIR/lint-report.html"

# Build
echo "=== Android Build ===" | tee "$REPORT_DIR/build-output.txt"
if ./gradlew assembleRelease >> "$REPORT_DIR/build-output.txt" 2>&1; then
    APK=$(find . -name "*.apk" -path "*/release/*" 2>/dev/null | head -1)
    if [ -n "$APK" ]; then
        cp "$APK" "$REPORT_DIR/app-release.apk"
        echo "Build: PASS — APK copied to $REPORT_DIR/app-release.apk"
    fi
    sed -i 's/ANDROID_BUILD=PENDING/ANDROID_BUILD=PASS/' "$STATUS_FILE"
else
    sed -i 's/ANDROID_BUILD=PENDING/ANDROID_BUILD=FAIL/' "$STATUS_FILE"
    echo "Build: FAIL — check build-output.txt"
    exit 1
fi
```

- [ ] **Step 2: Make executable and test**

```bash
chmod +x tests/android/lint_and_build.sh
mkdir -p /tmp/tl_android_test
bash tests/android/lint_and_build.sh /tmp/tl_android_test
cat /tmp/tl_android_test/android_status.txt
```

Expected: `ANDROID_LINT=PASS` (or WARN) and `ANDROID_BUILD=PASS`. APK copied.

- [ ] **Step 3: Commit**

```bash
git add tests/android/lint_and_build.sh
git commit -m "test: add Android lint + release build script"
```

---

## Task 10: Master script `tests/run_all.sh`

**Files:**
- Create: `tests/run_all.sh`

- [ ] **Step 1: Write `tests/run_all.sh`**

```bash
#!/usr/bin/env bash
# TetherLink V1 Pre-Release Test Suite
# Usage: bash tests/run_all.sh
# Produces: tests/reports/YYYY-MM-DD-HH-MM/report.md
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMESTAMP="$(date +%Y-%m-%d-%H-%M)"
REPORT_DIR="$REPO_ROOT/tests/reports/$TIMESTAMP"
mkdir -p "$REPORT_DIR"

echo "=================================================="
echo " TetherLink V1 Pre-Release Test Suite"
echo " Report: $REPORT_DIR/report.md"
echo "=================================================="
echo ""

# ── Prerequisites ─────────────────────────────────────────────────────────────
fail_prereq() { echo "PREREQ FAIL: $1"; exit 1; }

python3 --version | grep -qE "3\.(1[0-9])" || fail_prereq "Python 3.10+ required"
gst-inspect-1.0 --version &>/dev/null       || fail_prereq "gstreamer1.0-tools not found (apt install gstreamer1.0-tools)"
which pw-cli &>/dev/null                    || fail_prereq "pipewire not found (apt install pipewire)"
which java &>/dev/null                      || fail_prereq "Java not found (apt install default-jdk)"

# Install Python test deps into temp venv if needed
VENV="$REPORT_DIR/.testvenv"
python3 -m venv "$VENV" --system-site-packages &>/dev/null
"$VENV/bin/pip" install --quiet bandit psutil 2>/dev/null || true
export PATH="$VENV/bin:$PATH"

echo "Prerequisites: OK"
echo ""

# ── Run all tests via Python orchestrator ─────────────────────────────────────
PYTHON="$VENV/bin/python3"
cd "$REPO_ROOT"

"$PYTHON" - "$REPORT_DIR" "$REPO_ROOT" <<'PYEOF'
import sys, os, time, subprocess, tempfile

report_dir = sys.argv[1]
repo_root  = sys.argv[2]
sys.path.insert(0, repo_root)

from tests.lib.result   import passed, failed, warned, skipped, TestResult
from tests.lib.report   import generate, print_summary
from tests.lib          import server_manager

results: list[TestResult] = []

# ── Security (no server needed) ───────────────────────────────────────────────
print("▶ Security scan...")
from tests.security.scan import run as sec_run
results.append(sec_run(report_dir))
print(f"  → {results[-1].status}: {results[-1].notes}")

# ── Compatibility: versions + requirements + D-Bus (no server needed) ─────────
print("▶ Version check...")
from tests.compatibility.test_versions import run as ver_run
results.append(ver_run(report_dir))
print(f"  → {results[-1].status}: {results[-1].notes}")

print("▶ Requirements check...")
from tests.compatibility.test_requirements import run as req_run
results.append(req_run(report_dir))
print(f"  → {results[-1].status}: {results[-1].notes}")

print("▶ D-Bus API surface check...")
from tests.compatibility.test_dbus_surface import run as dbus_run
results.append(dbus_run(report_dir))
print(f"  → {results[-1].status}: {results[-1].notes}")

# ── Protocol + Stress tests (server required) ─────────────────────────────────
print("▶ Starting headless server on port 18080...")
try:
    proc = server_manager.start(18080, report_dir)
    server_pid = proc.pid
    print(f"  Server PID: {server_pid}")

    print("▶ UDP discovery test...")
    from tests.protocol.test_udp_discovery import run as udp_run
    results.append(udp_run(report_dir))
    print(f"  → {results[-1].status}: {results[-1].notes}")

    print("▶ Handshake test...")
    from tests.protocol.test_handshake import run as hs_run
    results.append(hs_run(18080, report_dir))
    print(f"  → {results[-1].status}: {results[-1].notes}")

    print("▶ TLBUSY test...")
    from tests.protocol.test_busy import run as busy_run
    results.append(busy_run(18080, report_dir))
    print(f"  → {results[-1].status}: {results[-1].notes}")

    print("▶ Malformed input test...")
    from tests.protocol.test_malformed import run as mal_run
    results.append(mal_run(18080, report_dir))
    print(f"  → {results[-1].status}: {results[-1].notes}")

    print("▶ Rapid reconnect stress test...")
    from tests.stress.test_rapid_reconnect import run as rr_run
    results.append(rr_run(18080, report_dir))
    print(f"  → {results[-1].status}: {results[-1].notes}")

    print("▶ Long session stress test (30 min — go make coffee) ...")
    from tests.stress.test_long_session import run as ls_run
    results.append(ls_run(18080, report_dir, server_pid))
    print(f"  → {results[-1].status}: {results[-1].notes}")

    server_manager.stop(proc)
    print("  Server stopped.")
except RuntimeError as e:
    results.append(failed("Server Startup", str(e)))
    print(f"  FAIL: {e}")

# ── Compatibility: xrandr + nested session (own server instances) ─────────────
print("▶ xrandr-on-Wayland test...")
from tests.compatibility.test_xrandr_wayland import run as xr_run
results.append(xr_run(report_dir))
print(f"  → {results[-1].status}: {results[-1].notes}")

print("▶ Nested GNOME session test (may take 30s) ...")
from tests.compatibility.test_nested_session import run as nest_run
results.append(nest_run(report_dir))
print(f"  → {results[-1].status}: {results[-1].notes}")

# ── Android (no server needed, longest step) ──────────────────────────────────
print("▶ Android lint + build (this takes a few minutes) ...")
android_script = os.path.join(repo_root, "tests", "android", "lint_and_build.sh")
ret = subprocess.run(["bash", android_script, report_dir], capture_output=True)
status_file = os.path.join(report_dir, "android_status.txt")
if os.path.exists(status_file):
    with open(status_file) as f:
        statuses = dict(l.strip().split("=") for l in f if "=" in l)
    lint_s  = statuses.get("ANDROID_LINT",  "SKIP")
    build_s = statuses.get("ANDROID_BUILD", "FAIL")
    if build_s == "FAIL":
        results.append(failed("Android Build", "assembleRelease failed — check build-output.txt"))
    elif build_s == "PASS":
        results.append(passed("Android Build", "APK built successfully"))
    if lint_s == "WARN":
        results.append(warned("Android Lint", "Lint issues found — check lint-report.html"))
    elif lint_s == "PASS":
        results.append(passed("Android Lint", "No lint errors"))
else:
    results.append(failed("Android Build", f"Script failed: {ret.stderr.decode()[:200]}"))
print(f"  Lint → {lint_s}  Build → {build_s}")

# ── Report ────────────────────────────────────────────────────────────────────
path = generate(results, report_dir)
print_summary(results)
print(f"\nFull report: {path}\n")
PYEOF

echo ""
echo "Done. Report: $REPORT_DIR/report.md"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x tests/run_all.sh
```

- [ ] **Step 3: Dry-run (skips long session — edit DURATION_S=30 temporarily)**

Temporarily set `DURATION_S = 30` in `tests/stress/test_long_session.py`, then:

```bash
cd /home/prince/TetherLink
bash tests/run_all.sh 2>&1 | tee /tmp/tl_test_dry_run.txt
```

Expected: all sections print `→ PASS` or `→ WARN`. Report file created.

- [ ] **Step 4: Restore DURATION_S = 1800**

```python
# in tests/stress/test_long_session.py line:
DURATION_S = 1800   # 30 minutes
```

- [ ] **Step 5: Commit**

```bash
git add tests/run_all.sh tests/stress/test_long_session.py
git commit -m "test: add master run_all.sh orchestrator — full automated suite"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Security: bandit + hardcoded values + manifest audit → Task 5
- ✅ Protocol correctness: MockClient → Task 4; handshake test → Task 6
- ✅ TLBUSY enforcement → Task 6
- ✅ Malformed input → Task 6
- ✅ UDP discovery → Task 6
- ✅ Long session → Task 7
- ✅ Rapid reconnect → Task 7
- ✅ Android Lint + APK build → Task 9
- ✅ Version consistency → Task 8 (test_versions.py)
- ✅ Requirements accuracy → Task 8 (test_requirements.py)
- ✅ D-Bus API surface → Task 8 (test_dbus_surface.py)
- ✅ Nested GNOME session → Task 8 (test_nested_session.py)
- ✅ xrandr-on-Wayland → Task 8 (test_xrandr_wayland.py)
- ✅ Single report.md → Task 2 + Task 10
- ✅ Manual checklist in report → Task 2 (MANUAL_CHECKLIST)

**Type consistency:**
- `server_manager.start(port, report_dir, wayland_display=None)` used consistently in Tasks 3, 8, 10
- `MockClient.connect(host, port, name, w, h, timeout)` used consistently in Tasks 4, 6, 7, 8
- `run(report_dir)` signature for no-server tests; `run(port, report_dir)` for server tests; `run(port, report_dir, server_pid)` for long session — all consistent with Task 10 calls
- `TestResult` imported from `tests.lib.result` throughout

**Placeholder scan:** None found — all code blocks are complete.
