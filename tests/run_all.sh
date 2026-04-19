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
lint_s  = "SKIP"
build_s = "SKIP"
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
