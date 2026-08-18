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
