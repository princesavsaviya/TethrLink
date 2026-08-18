# Persistent Encoder and Device Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the ~2 s encoder probe from every app launch by caching the result on disk, and give per-device settings a durable home keyed by the device ID the handshake already carries.

**Architecture:** One small JSON-backed store under the user's cache directory, holding two independent records: a machine-scoped encoder capability entry guarded by a fingerprint of the local GStreamer installation, and a map of device profiles keyed by the handshake's 16-byte device ID. Both are pure caches — deleting the file costs one slow start and nothing else. The store itself has no GStreamer dependency so it is fully unit-testable.

**Tech Stack:** Python 3.12, PyGObject (`gi`), GStreamer 1.24, pytest 9.

## Global Constraints

- Python 3.12; run tests with `./venv/bin/python -m pytest`, NEVER bare `pytest`.
- `server/core/profiles.py` MUST NOT import `gi` — the persistence and invalidation logic must be unit-testable with no GStreamer and no GPU.
- No protocol or wire-format changes. Released Android clients must keep working unmodified.
- Do not alter `queue leaky=downstream` upstream of the encoder or `appsink drop=false` downstream. Dropping a RAW frame is safe; dropping an ENCODED frame corrupts the stream until the next keyframe.
- Do not change encoder tuning, the geometry derivation, or `ServerConfig.fps` (currently 30).
- Valid metric counter names are exactly: `frames_encoded`, `frames_sent`, `frames_dropped`, `duplicates_suppressed`, `queue_overflows`, `keyframe_requests`.
- Never diagnose GStreamer with bare `gst-inspect-1.0` — Anaconda shadows it here with a 1.14.1 build reporting zero encoders. Use `/usr/bin/gst-inspect-1.0` or probe via PyGObject.
- **A cache must never be able to break streaming.** Every read is best-effort: a missing, unreadable, corrupt, or stale file must fall back to live probing without raising. This project has already shipped a defect where a diagnostic could prevent the GTK window from appearing — do not repeat it.
- Work happens on branch `work/video-quality-review`.

## Measured Baseline

| Operation | Cost |
|---|---|
| Rate-control probing, all 8 candidates | 1.66 s |
| `select_encoder` cold, short-circuiting at NVENC and verifying by encoding | 0.45 s |
| `select_encoder` warm (existing in-memory cache) | ~0 s |
| Observed handshake → "Encoder selected" on real hardware | 2.06 s |

The in-memory cache already makes reconnects free. The saving here is therefore
**per app launch**, not per connection — worth having, but the scope should not
be oversold.

## Environment Facts

- `XDG_CACHE_HOME`, `SNAP_USER_COMMON` and `SNAP_USER_DATA` are all unset on the dev machine, so the `~/.cache` fallback is the path that will actually be exercised.
- The handshake already parses a stable 16-byte device id: `device_id = hello[6:22].hex()` in `_handle_client`. No protocol change is needed to key device profiles.

## File Structure

- `server/core/profiles.py` — **new.** JSON-backed store, fingerprinting, and staleness rules. No `gi` import. Carries the bulk of the new tests.
- `server/core/server_core.py` — modified. `select_encoder` consults the store before probing and records successful results; `_handle_client` records a device profile after a successful handshake.
- `tests/test_profiles.py` — **new.**

---

### Task 1: Profile store

**Files:**
- Create: `server/core/profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `profile_path() -> pathlib.Path` — resolves the store location, honouring `SNAP_USER_COMMON`, then `XDG_CACHE_HOME`, then `~/.cache`, always under a `tethrlink` subdirectory and named `profiles.json`.
  - `ProfileStore(path=None)` with:
    - `load() -> None` — best-effort; never raises.
    - `save() -> bool` — best-effort; returns False on failure rather than raising.
    - `get_encoder(fingerprint) -> dict | None` — returns the cached encoder record only when the stored fingerprint matches.
    - `set_encoder(fingerprint, record) -> None`
    - `get_device(device_id) -> dict | None`
    - `set_device(device_id, record) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_profiles.py`:

```python
import json
import os

from server.core.profiles import ProfileStore, profile_path


# ── location resolution ──────────────────────────────────────────────────────

def test_prefers_snap_common_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("SNAP_USER_COMMON", str(tmp_path / "snap"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert str(profile_path()).startswith(str(tmp_path / "snap"))


def test_falls_back_to_xdg_cache_home(monkeypatch, tmp_path):
    monkeypatch.delenv("SNAP_USER_COMMON", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert str(profile_path()).startswith(str(tmp_path / "xdg"))


def test_falls_back_to_dot_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("SNAP_USER_COMMON", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = profile_path()
    assert str(p).startswith(str(tmp_path / ".cache"))


def test_path_is_namespaced_and_named():
    p = profile_path()
    assert "tethrlink" in str(p)
    assert p.name == "profiles.json"


# ── encoder record, guarded by fingerprint ───────────────────────────────────

def test_encoder_round_trips(tmp_path):
    store = ProfileStore(tmp_path / "p.json")
    store.set_encoder("fp-1", {"element": "nvh264enc", "rate_control": "cbr"})
    assert store.save() is True

    reloaded = ProfileStore(tmp_path / "p.json")
    reloaded.load()
    assert reloaded.get_encoder("fp-1")["element"] == "nvh264enc"


def test_encoder_is_ignored_when_fingerprint_differs(tmp_path):
    """A driver or GStreamer upgrade must force a re-probe."""
    store = ProfileStore(tmp_path / "p.json")
    store.set_encoder("fp-old", {"element": "nvh264enc"})
    store.save()

    reloaded = ProfileStore(tmp_path / "p.json")
    reloaded.load()
    assert reloaded.get_encoder("fp-new") is None


def test_get_encoder_on_empty_store_is_none(tmp_path):
    store = ProfileStore(tmp_path / "p.json")
    store.load()
    assert store.get_encoder("anything") is None


# ── device records ───────────────────────────────────────────────────────────

def test_device_round_trips(tmp_path):
    store = ProfileStore(tmp_path / "p.json")
    store.set_device("abc123", {"name": "SM-X920", "width": 2960, "height": 1848})
    store.save()

    reloaded = ProfileStore(tmp_path / "p.json")
    reloaded.load()
    assert reloaded.get_device("abc123")["width"] == 2960


def test_devices_are_kept_separate(tmp_path):
    store = ProfileStore(tmp_path / "p.json")
    store.set_device("aaa", {"name": "tablet"})
    store.set_device("bbb", {"name": "phone"})
    store.save()

    reloaded = ProfileStore(tmp_path / "p.json")
    reloaded.load()
    assert reloaded.get_device("aaa")["name"] == "tablet"
    assert reloaded.get_device("bbb")["name"] == "phone"


def test_unknown_device_is_none(tmp_path):
    store = ProfileStore(tmp_path / "p.json")
    store.load()
    assert store.get_device("nope") is None


def test_setting_a_device_does_not_disturb_the_encoder_record(tmp_path):
    store = ProfileStore(tmp_path / "p.json")
    store.set_encoder("fp", {"element": "nvh264enc"})
    store.set_device("dev", {"name": "tablet"})
    store.save()

    reloaded = ProfileStore(tmp_path / "p.json")
    reloaded.load()
    assert reloaded.get_encoder("fp")["element"] == "nvh264enc"
    assert reloaded.get_device("dev")["name"] == "tablet"


# ── robustness: a cache must never break the app ─────────────────────────────

def test_corrupt_file_loads_as_empty_rather_than_raising(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text("{ this is not valid json")
    store = ProfileStore(bad)
    store.load()
    assert store.get_encoder("fp") is None
    assert store.get_device("dev") is None


def test_missing_file_loads_as_empty(tmp_path):
    store = ProfileStore(tmp_path / "absent.json")
    store.load()
    assert store.get_encoder("fp") is None


def test_wrong_shape_file_loads_as_empty(tmp_path):
    """Valid JSON of the wrong type must not crash the accessors."""
    odd = tmp_path / "p.json"
    odd.write_text('["a list, not an object"]')
    store = ProfileStore(odd)
    store.load()
    assert store.get_device("dev") is None


def test_save_reports_failure_rather_than_raising(tmp_path):
    unwritable = tmp_path / "nodir" / "deeper" / "p.json"
    os.makedirs(tmp_path / "nodir", exist_ok=True)
    os.chmod(tmp_path / "nodir", 0o400)
    try:
        store = ProfileStore(unwritable)
        store.set_device("d", {"name": "x"})
        assert store.save() is False
    finally:
        os.chmod(tmp_path / "nodir", 0o700)


def test_save_writes_valid_json(tmp_path):
    store = ProfileStore(tmp_path / "p.json")
    store.set_device("d", {"name": "x"})
    store.save()
    parsed = json.loads((tmp_path / "p.json").read_text())
    assert isinstance(parsed, dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server.core.profiles'`

- [ ] **Step 3: Write minimal implementation**

Create `server/core/profiles.py`:

```python
"""Durable cache for encoder capability and per-device settings.

Two independent records live here:

* **encoder** — machine-scoped, guarded by a fingerprint of the local
  GStreamer installation. Probing encoders costs about two seconds because
  candidates are instantiated and actually made to encode; that is worth
  paying once per driver change rather than once per app launch.
* **devices** — keyed by the 16-byte device id the handshake already
  carries, so no protocol change is needed. Saves no time (dimensions arrive
  in the handshake anyway) but is the right home for per-device preferences.

Everything here is best-effort. Deleting or corrupting the file costs one
slow start and nothing else, so every read falls back to "no cached value"
rather than raising. No `gi` import: the invalidation rules are the part
worth testing, and they should be testable without GStreamer.
"""

import json
import logging
import os
import pathlib
from typing import Any, Dict, Optional

log = logging.getLogger("TethrLink")

_FILENAME = "profiles.json"
_APP_DIR = "tethrlink"


def profile_path() -> pathlib.Path:
    """Where the store lives.

    Snap confinement first (the host's real cache dir is not writable there),
    then the XDG cache location, then its documented default.
    """
    snap = os.environ.get("SNAP_USER_COMMON")
    if snap:
        return pathlib.Path(snap) / _APP_DIR / _FILENAME
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return pathlib.Path(xdg) / _APP_DIR / _FILENAME
    return pathlib.Path(os.path.expanduser("~")) / ".cache" / _APP_DIR / _FILENAME


class ProfileStore:
    def __init__(self, path: Optional[pathlib.Path] = None):
        self._path = pathlib.Path(path) if path is not None else profile_path()
        self._data: Dict[str, Any] = {"encoder": {}, "devices": {}}

    # ── persistence ──────────────────────────────────────────────────────

    def load(self) -> None:
        """Read the store. Never raises: a cache must not break the app."""
        try:
            raw = json.loads(self._path.read_text())
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            log.debug("Ignoring unreadable profile store %s: %s", self._path, e)
            return

        if not isinstance(raw, dict):
            log.debug("Ignoring profile store with unexpected shape: %s", self._path)
            return

        encoder = raw.get("encoder")
        devices = raw.get("devices")
        self._data = {
            "encoder": encoder if isinstance(encoder, dict) else {},
            "devices": devices if isinstance(devices, dict) else {},
        }

    def save(self) -> bool:
        """Write the store. Returns False on failure instead of raising."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Write via a temporary file so an interrupted save cannot leave a
            # half-written store behind for the next launch to choke on.
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2))
            tmp.replace(self._path)
            return True
        except OSError as e:
            log.debug("Could not write profile store %s: %s", self._path, e)
            return False

    # ── encoder record ───────────────────────────────────────────────────

    def get_encoder(self, fingerprint: str) -> Optional[dict]:
        """Cached encoder choice, but only if it was recorded against this
        exact GStreamer installation. A driver or plugin change invalidates
        it, because 'this encoder works' is a claim about the hardware and
        the drivers, not about the app."""
        entry = self._data.get("encoder") or {}
        if not isinstance(entry, dict):
            return None
        if entry.get("fingerprint") != fingerprint:
            return None
        record = entry.get("record")
        return record if isinstance(record, dict) else None

    def set_encoder(self, fingerprint: str, record: dict) -> None:
        self._data["encoder"] = {"fingerprint": fingerprint, "record": record}

    # ── device records ───────────────────────────────────────────────────

    def get_device(self, device_id: str) -> Optional[dict]:
        devices = self._data.get("devices") or {}
        if not isinstance(devices, dict):
            return None
        record = devices.get(device_id)
        return record if isinstance(record, dict) else None

    def set_device(self, device_id: str, record: dict) -> None:
        devices = self._data.get("devices")
        if not isinstance(devices, dict):
            devices = {}
            self._data["devices"] = devices
        devices[device_id] = record
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_profiles.py -v`
Expected: PASS, 16 passed

- [ ] **Step 5: Commit**

```bash
git add server/core/profiles.py tests/test_profiles.py
git commit -m "feat: add JSON profile store for encoder and device records

Best-effort throughout: a missing, corrupt or wrongly shaped file falls back
to no cached value rather than raising, so the cache can never break
streaming. Saves atomically via a temporary file so an interrupted write
cannot poison the next launch."
```

---

### Task 2: Persist the encoder choice

**Files:**
- Modify: `server/core/server_core.py` — imports, plus `select_encoder` and a new fingerprint helper.

**Interfaces:**
- Consumes: `ProfileStore`, `profile_path` from Task 1; `CANDIDATES`, `EncoderSpec`, `build_spec` from `server/core/encoder.py`.
- Produces: `gstreamer_fingerprint() -> str` module-level in `server_core.py`.

- [ ] **Step 1: Add the import**

With the other `server.core` imports in `server/core/server_core.py`:

```python
from server.core.profiles import ProfileStore
```

- [ ] **Step 2: Add the fingerprint helper**

Add immediately above `select_encoder`:

```python
def gstreamer_fingerprint() -> str:
    """Identify this GStreamer installation for cache invalidation.

    A cached "nvh264enc works here" is a claim about the drivers and plugins
    present, not about the app. Upgrading GStreamer, installing a VA driver,
    or losing access to a render node must all invalidate it — so the
    fingerprint is the version plus exactly which candidate encoders the
    registry can currently see.
    """
    try:
        Gst.init(None)
        present = [
            name for name in CANDIDATES
            if Gst.ElementFactory.find(name) is not None
        ]
        return f"{Gst.version_string()}|{','.join(sorted(present))}"
    except Exception as e:
        # An unfingerprintable environment simply never matches a stored
        # entry, so this degrades to always probing rather than to failing.
        log.debug("Could not fingerprint GStreamer: %s", e)
        return "unknown"
```

- [ ] **Step 3: Consult the store before probing**

At the start of `select_encoder`, after the existing in-memory cache check, add a disk lookup. Insert immediately before the loop that walks `CANDIDATES`:

```python
    fingerprint = gstreamer_fingerprint()
    store = ProfileStore()
    store.load()
    cached = store.get_encoder(fingerprint)
    if cached:
        spec = EncoderSpec(
            element=cached.get("element", ""),
            is_hardware=bool(cached.get("is_hardware")),
            rate_control=cached.get("rate_control", ""),
            props=dict(cached.get("props") or {}),
        )
        # Trust but verify: a cached encoder that no longer works — a GPU in
        # use by another session, a driver that loaded differently — must not
        # break the stream. Re-running it is far cheaper than the full probe.
        if spec.element and _encoder_runs(spec, width, height):
            log.info("Encoder from cache: %s (%s, rate-control=%s)",
                     spec.element,
                     "hardware" if spec.is_hardware else "software",
                     spec.rate_control)
            _ENCODER_CACHE[key] = spec
            return spec
        log.info("Cached encoder %s no longer usable — re-probing",
                 spec.element)
```

Note `_encoder_runs` takes the real capture size — pass through whatever `select_encoder` already receives for width and height, matching how the existing probe path calls it.

- [ ] **Step 4: Record a successful selection**

Where `select_encoder` currently stores its result in `_ENCODER_CACHE` on success, also persist it. Add immediately after the in-memory cache assignment for a non-`None` result:

```python
        store.set_encoder(fingerprint, {
            "element": chosen.element,
            "is_hardware": chosen.is_hardware,
            "rate_control": chosen.rate_control,
            "props": chosen.props,
        })
        store.save()
```

Do not persist a `None` result: "nothing worked" is far more likely to be transient than "this one worked", and caching it would strand a user on software encoding until they found the file.

- [ ] **Step 5: Verify the saving is real**

Run, twice, and compare the timings:

```bash
rm -f ~/.cache/tethrlink/profiles.json
./venv/bin/python -c "
import time, logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
from server.core.server_core import select_encoder, _ENCODER_CACHE
from server.core.encoder import EncoderConfig, RateControl
cfg = EncoderConfig(bitrate_kbps=16410, gop_length=30, rate_control=RateControl.CBR)
t = time.monotonic(); select_encoder(cfg, width=1730, height=1080); print(f'cold: {time.monotonic()-t:.2f}s')
_ENCODER_CACHE.clear()
t = time.monotonic(); select_encoder(cfg, width=1730, height=1080); print(f'from disk: {time.monotonic()-t:.2f}s')
" 2>&1 | grep -viE '^\(|critical'
cat ~/.cache/tethrlink/profiles.json
```

Expected: the second timing is materially lower than the first, the log shows `Encoder from cache:`, and the JSON contains an `encoder` object with a `fingerprint` and the chosen element.

- [ ] **Step 6: Confirm the suite still passes**

Run: `./venv/bin/python -m pytest -q`
Expected: all tests pass (152 at this point)

- [ ] **Step 7: Commit**

```bash
git add server/core/server_core.py
git commit -m "feat: persist the encoder choice across app launches

Probing costs about two seconds because candidates are instantiated and made
to actually encode. That is worth paying once per driver change rather than
once per launch. The stored entry is guarded by a fingerprint of the
GStreamer version and visible encoders, and is re-verified before use, so a
stale or newly broken entry costs one re-probe rather than a broken stream."
```

---

### Task 3: Remember connected devices

**Files:**
- Modify: `server/core/server_core.py` — `_handle_client`.

**Interfaces:**
- Consumes: `ProfileStore` from Task 1.
- Produces: no new public interface.

- [ ] **Step 1: Record the device after a successful handshake**

`_handle_client` already computes `device_id`, `device_name`, `screen_w` and `screen_h` from the hello. After the resolution has been resolved and logged — so the record reflects what was actually used — add:

```python
        try:
            store = ProfileStore()
            store.load()
            known = store.get_device(device_id)
            store.set_device(device_id, {
                "name": device_name,
                "width": screen_w,
                "height": screen_h,
                "last_capture_width": width,
                "last_capture_height": height,
                "connections": (known or {}).get("connections", 0) + 1,
            })
            store.save()
            if known is None:
                self._log(f"First connection from {device_name} — profile saved")
        except Exception as e:
            # Recording a profile is a convenience, never a precondition for
            # streaming.
            log.debug("Could not record device profile: %s", e)
```

- [ ] **Step 2: Verify a profile is written and the counter advances**

Drive two connections using the real-socket handshake driver described in
`.superpowers/sdd/task-6-report-p2.md`, then inspect the store:

```bash
cat ~/.cache/tethrlink/profiles.json
```

Expected: a `devices` object keyed by the hex device id, carrying the reported
screen size, the capture size actually used, and `connections` incremented to 2
after the second connection. The first connection logs `First connection from
… — profile saved`; the second does not.

- [ ] **Step 3: Confirm the suite still passes**

Run: `./venv/bin/python -m pytest -q`
Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add server/core/server_core.py
git commit -m "feat: remember devices by the id the handshake already carries

No protocol change: the 16-byte device id has always been in the hello. This
saves no time, since dimensions arrive in the handshake anyway, but gives
per-device preferences a durable home and is where reported DPI and decoder
limits belong once the handshake carries them."
```

---

### Task 4: Verification

**Files:**
- Modify: `docs/superpowers/plans/2026-08-17-verification-log.md`

- [ ] **Step 1: Confirm the unit suite**

Run: `./venv/bin/python -m pytest -q`
Expected: all pass, roughly 152 tests.

- [ ] **Step 2: Measure the launch saving end to end**

With the store deleted, start the server and record the wall time from the
`Incoming:` line to the `Encoder selected:` or `Encoder from cache:` line.
Restart and repeat.

```bash
rm -f ~/.cache/tethrlink/profiles.json
cd /home/prince/TethrLink && TETHRLINK_CODEC=h264 python3 -m server.app.main
```

**Acceptance:** the second launch reaches streaming measurably sooner, and logs
`Encoder from cache:` rather than re-probing.

- [ ] **Step 3: Confirm a corrupt store degrades gracefully**

```bash
echo 'garbage{' > ~/.cache/tethrlink/profiles.json
cd /home/prince/TethrLink && TETHRLINK_CODEC=h264 python3 -m server.app.main
```

**Acceptance:** the server starts, streams normally, and re-probes. No traceback,
no failure to show the window.

- [ ] **Step 4: Confirm fingerprint invalidation**

Hand-edit the `fingerprint` value in the JSON to something else, then start the
server again.

**Acceptance:** the cached entry is ignored and the encoder is re-probed —
proving a driver or GStreamer upgrade cannot strand a user on a stale choice.

- [ ] **Step 5: Record results and commit**

```bash
git add docs/superpowers/plans/2026-08-17-verification-log.md
git commit -m "docs: record profile persistence verification"
```

---

## Out of Scope

- Pre-warming the virtual display before a client connects. It would cut connect time further, but creating a virtual monitor while nothing is attached rearranges the user's desktop unprompted — a poor trade for a second.
- Per-device preference *editing* in the UI. The store is written now; exposing it is a separate piece of work.
- DPI and decoder-capability fields. They belong in the device record, but the handshake does not yet carry them; that needs the append-only extension block from spec §5.
