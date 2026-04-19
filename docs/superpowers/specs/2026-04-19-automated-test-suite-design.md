# TetherLink V1 — Automated Pre-Release Test Suite

**Date:** 2026-04-19  
**Target:** GitHub V1 release + Google Play submission + Ubuntu PPA  
**Minimum supported OS:** Ubuntu 22.04 LTS (GNOME 42, GStreamer 1.20, PipeWire 0.3.48)  
**Test runner:** Single shell script — run once, read report on wake-up

---

## Goals

1. Catch security vulnerabilities before public release
2. Validate the full server↔Android protocol without a real device
3. Stress-test reconnect reliability and memory stability
4. Confirm Android APK builds and passes lint
5. Produce a single human-readable report: PASS / FAIL / WARN per test

---

## What Is Automated

| Category | Tool / Approach |
|---|---|
| Security scan | `bandit`, `grep` patterns, manifest parser |
| Protocol correctness | Python mock Android client (speaks full TLHELLO/TLOK protocol) |
| TLBUSY enforcement | Two mock clients simultaneously |
| Malformed input resilience | Garbage TCP/UDP injector |
| Long session stability | 30-min stream + RSS/CPU sampler |
| Rapid reconnect | 10 disconnect/reconnect cycles in 2 min |
| Android Lint | `./gradlew lint` |
| APK build | `./gradlew assembleRelease` |
| Version consistency | Cross-check build.gradle, git tag, server version string |
| Requirements accuracy | Parse imports vs `requirements.txt` |

## What Requires Manual Follow-Up (flagged in report)

- Real USB tethering test with physical Android devices
- Play Store form, screenshots, privacy policy upload
- Debian/Ubuntu package creation and install test
- Visual frame quality check (human eyes)

---

## Architecture

```
tests/
  run_all.sh                  ← master script, entry point
  lib/
    server_manager.py         ← starts/stops server in headless mode, waits for ready
    mock_client.py            ← full TetherLink protocol client (TLHELLO→TLOK→frames)
    report.py                 ← collects results from all runners, writes report.md
  security/
    scan.py                   ← bandit + hardcoded value grep + manifest permission audit
  protocol/
    test_handshake.py         ← connect, verify TLOK, verify frame arrives
    test_busy.py              ← two clients, second must receive TLBUSY
    test_malformed.py         ← 5 malformed TCP payloads, server must survive each
    test_udp_discovery.py     ← listen for UDP broadcast, verify fields present
  stress/
    test_long_session.py      ← 30-min stream, sample RSS+CPU every 30s
    test_rapid_reconnect.py   ← 10 connect/disconnect cycles, verify lock released each time
  compatibility/
    test_versions.py          ← Python ≥3.10, GStreamer ≥1.20, PipeWire present
    test_requirements.py      ← all server/ imports resolvable from requirements.txt
  android/
    lint_and_build.sh         ← gradlew lint + gradlew assembleRelease
  reports/                    ← auto-created, one folder per run
    YYYY-MM-DD-HH-MM/
      report.md
      lint-report.html        ← copy of Android lint output
      bandit-report.json
      cpu_rss.csv             ← long session samples
```

---

## Component Specifications

### `run_all.sh`

- Creates timestamped report directory under `tests/reports/`
- Checks prerequisites: Python 3.10+, `bandit`, `gst-inspect-1.0`, Java/Gradle
- Installs missing Python test deps into a temp venv (`pip install bandit psutil`)
- Starts server in headless mode (`python server/tetherlink_server.py --headless --port 18080`) on a test port (18080) to avoid colliding with a running server
- Waits up to 15s for port 18080 to open before running protocol tests
- Runs all test modules sequentially (protocol tests need server; security/compatibility tests do not)
- Kills server after protocol + stress tests
- Runs Android lint + build last (longest, no server needed)
- Calls `report.py` to generate final `report.md`
- Prints path to report and overall PASS/FAIL summary to stdout

### `lib/server_manager.py`

- `start(port)` → spawns headless server subprocess, polls TCP port until open (timeout 15s), returns process handle
- `stop(proc)` → sends SIGTERM, waits 3s, SIGKILL if still alive
- Captures server stdout/stderr to `reports/<run>/server.log`

### `lib/mock_client.py`

Full TetherLink protocol implementation:

```
MAGIC_HELLO = b"TLHELO"          # 6 bytes
MAGIC_OK    = b"TLOK__"          # 6 bytes prefix (followed by w, h, codec = 9 bytes total)
MAGIC_BUSY  = b"TLBUSY"          # 6 bytes
```

**Handshake packet (sent by mock client):**
```
[6B magic][16B device_id][4B width big-endian][4B height big-endian][64B name utf-8 null-padded]
```

**MockClient API:**
- `connect(host, port, device_name, width, height)` → connects TCP, sends TLHELLO
- `read_response()` → returns `"ok"`, `"busy"`, or `"error"` + (w, h, codec) on ok
- `read_frame()` → reads `[4B length][frame bytes]`, returns frame bytes
- `close()` → closes socket

**Discovery:**
- `listen_udp(port=8765, timeout=5)` → listens for UDP broadcast, returns parsed dict or None

### `security/scan.py`

1. **bandit scan** — runs `bandit -r server/ -f json -o reports/<run>/bandit-report.json`, flags HIGH/MEDIUM severity issues
2. **Hardcoded value scan** — grep server/ and android/ for:
   - IP patterns `192\.168\.`, `127\.0\.0\.1` outside constants/comments
   - `password`, `secret`, `api_key`, `token` (case-insensitive)
   - Numeric port literals outside `constants` section of server_core.py
3. **Manifest permission audit** — parse `AndroidManifest.xml`, flag any permission beyond:
   - `INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE`, `CHANGE_NETWORK_STATE`, `FOREGROUND_SERVICE`, `RECEIVE_BOOT_COMPLETED`
4. **TCP bind check** — confirm server binds `0.0.0.0` (acceptable — USB subnet only), document in report
5. **TODO/FIXME scan** — grep all source files, list in report as WARN (not FAIL)

Each finding: FAIL (high severity), WARN (medium), INFO (low).

### `protocol/test_handshake.py`

1. Connect mock client (1920×1080, "TestDevice")
2. Assert response is `"ok"`
3. Assert frame arrives within 10s
4. Assert frame is valid JPEG (`bytes[:2] == b'\xff\xd8'`)
5. Close connection
6. Reconnect immediately — assert second connection succeeds (lock released)

PASS if all assertions hold.

### `protocol/test_busy.py`

1. Connect client A, wait for TLOK
2. Connect client B (different port) simultaneously
3. Assert client B receives TLBUSY within 3s
4. Disconnect client A
5. Connect client C — assert TLOK (lock released after A disconnected)

PASS if TLBUSY received for B and TLOK for C.

### `protocol/test_malformed.py`

Send 5 malformed payloads, after each check server is still accepting connections:

| # | Payload |
|---|---|
| 1 | Empty bytes `b""` |
| 2 | Random 100 bytes |
| 3 | Correct magic `TLHELO` + truncated (10 bytes total) |
| 4 | Correct magic + valid header + 10000 null bytes appended |
| 5 | Valid TLHELLO but width=0, height=0 (server uses primary res — expect TLOK, not error) |

After each: connect a valid mock client and assert TLOK received.  
PASS if server survives all 5 and valid client connects after each.

### `protocol/test_udp_discovery.py`

- Listen on UDP port 8765 for 10s
- Assert broadcast received
- Assert packet contains: `ip`, `port`, `name` fields (JSON or custom format)
- Assert IP in received packet is reachable (TCP connect test)

PASS if broadcast received and fields present.

### `stress/test_long_session.py`

- Connect mock client
- Stream for 1800s (30 min), reading frames in loop
- Every 30s: sample server process RSS (MB) and CPU% via `psutil`
- Write samples to `reports/<run>/cpu_rss.csv`
- PASS criteria:
  - No crash or disconnect during 30 min
  - RSS does not grow by more than 50MB over baseline (no memory leak)
  - Average CPU < 80% sustained
  - At least 1 frame received per second on average

### `stress/test_rapid_reconnect.py`

- Loop 10 times:
  - Connect mock client
  - Receive 1 frame (confirms stream is live)
  - Close connection abruptly (no graceful close)
  - Wait 2s
- Assert each iteration completes in < 10s (lock released, new connection accepted)
- PASS if all 10 iterations succeed.

### `compatibility/test_versions.py`

Check minimum version requirements:

| Dependency | Minimum | How checked |
|---|---|---|
| Python | 3.10 | `sys.version_info` |
| GStreamer | 1.20 | `gst-inspect-1.0 --version` |
| PipeWire | 0.3.48 | `pw-cli --version` |
| GTK4 | 4.6 | `python3 -c "import gi; gi.require_version('Gtk','4.0')"` |
| gstreamer1.0-pipewire | present | `gst-inspect-1.0 pipewiresrc` |

PASS if all present and at or above minimum.

### `compatibility/test_requirements.py`

1. Parse all `import` statements in `server/*.py`
2. Map to PyPI package names
3. Check each is listed in `server/requirements.txt`
4. WARN for any import not in requirements (might be stdlib — cross-check against stdlib list)

PASS if all non-stdlib imports are in requirements.txt.

### `android/lint_and_build.sh`

```bash
cd android
./gradlew lint 2>&1 | tee reports/<run>/lint-output.txt
./gradlew assembleRelease 2>&1 | tee reports/<run>/build-output.txt
```

- PASS if both exit 0
- Copy `app/build/outputs/apk/release/app-release-unsigned.apk` to `reports/<run>/`
- Copy `app/build/reports/lint-results-release.html` to `reports/<run>/`
- WARN if lint issues > 0 (not FAIL — lint warnings are not blockers for release)
- FAIL if build fails

### `lib/report.py`

Generates `reports/<run>/report.md` with:

```markdown
# TetherLink V1 Pre-Release Test Report
Date: <timestamp>
Overall: ✅ PASS / ❌ FAIL

## Summary
| Test | Result | Notes |
|------|--------|-------|
| Security Scan | ✅ PASS | 0 HIGH, 2 WARN |
| Handshake | ✅ PASS | |
| TLBUSY | ✅ PASS | |
...

## Details
[per-test output]

## Manual Tests Required Before App Store Submission
- [ ] Real USB tethering test (Samsung tablet + phone)
- [ ] Play Store screenshots and form
- [ ] Privacy policy page live
- [ ] Debian .deb package install test on clean Ubuntu 22.04 VM
- [ ] Visual frame quality check
```

Overall result: FAIL if any single test is FAIL. WARN is noted but does not fail the suite.

---

## Prerequisites Check (run_all.sh performs this before starting)

```bash
python3 --version          # must be 3.10+
pip install bandit psutil  # test dependencies
gst-inspect-1.0 --version  # GStreamer present
which pw-cli               # PipeWire present
java --version             # for Gradle/Android build
```

If any prerequisite missing: print clear error message and exit before starting tests.

---

## Known Limitations

- Protocol tests run against real GStreamer/PipeWire/Mutter — requires an active GNOME Wayland session (the user's normal desktop)
- Long session test (30 min) runs server on test port 18080 — if a server is already running on 8080, tests are unaffected
- Android build requires `ANDROID_HOME` environment variable or `local.properties` set
- Nested GNOME session testing is not included in this automated suite — remains a manual step for future CI

---

## Report Location

`tests/reports/YYYY-MM-DD-HH-MM/report.md`

All supporting files (logs, CSVs, APK, lint HTML) in the same directory.
