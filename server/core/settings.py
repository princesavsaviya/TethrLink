"""Durable store for user-chosen settings: codec and touch-input preference.

Where server/core/profiles.py caches the *rediscoverable* (which encoder
works on this machine, a device's last-seen dimensions), this module
persists *user intent* — a choice the user made and reasonably expects
remembered across restarts. That distinction is why this lives under
~/.config/tethrlink/ (XDG config) rather than ~/.cache/tethrlink/ (XDG
cache) alongside profiles.json: losing profiles.json costs one slow
re-probe, but losing settings.json would silently revert a deliberate
choice (e.g. touch input) back to its default every launch.

Same best-effort contract as profiles.py: a missing or corrupt file must
never stop the app from starting. Every read falls back to defaults —
per field, not as an all-or-nothing file-level decision — and a failed
write degrades to "the choice isn't remembered next launch", not a crash
of the running app.

No `gi` import: like profiles.py, this is plain file I/O and should be
testable without GTK/GStreamer.
"""

import json
import logging
import os
import pathlib
from typing import Any, Dict, Optional

log = logging.getLogger("TethrLink")

_FILENAME = "settings.json"
_APP_DIR = "tethrlink"

# Mirrors ServerConfig's own defaults in server_core.py — H.264 on, touch
# off (see ServerConfig.codec / ServerConfig.touch_enabled for why those
# are the defaults). Keeping this fallback in sync with them is
# deliberate: a settings file that is missing, corrupt, or has an invalid
# value for a field must produce exactly the same configuration as a
# fresh install would, not some other "reasonable-looking" value.
DEFAULT_CODEC = "h264"
DEFAULT_TOUCH_ENABLED = False
_VALID_CODECS = ("h264", "jpeg")


def settings_path() -> pathlib.Path:
    """Where the store lives: XDG_CONFIG_HOME, or its documented default
    of ~/.config, under an app-namespaced subdirectory — one level up in
    the XDG hierarchy from profiles.py's cache directory, because this is
    configuration, not cache (see module docstring).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = pathlib.Path(xdg) if xdg else pathlib.Path(os.path.expanduser("~")) / ".config"
    return base / _APP_DIR / _FILENAME


def load_settings(path: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Best-effort read of the raw settings dict. Never raises: a missing
    file, a corrupt file, or a file whose top level isn't a JSON object
    all fall back to {} rather than stopping the app from starting.

    Deliberately returns the raw dict rather than a resolved
    (codec, touch_enabled) pair — resolve_codec()/resolve_touch_enabled()
    below validate the two fields this app currently uses, but the store
    itself stays a generic key/value bag so the server can add settings
    later without this function's signature changing.
    """
    p = pathlib.Path(path) if path is not None else settings_path()
    try:
        raw = json.loads(p.read_text())
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        log.debug("Ignoring unreadable settings file %s: %s", p, e)
        return {}
    if not isinstance(raw, dict):
        log.debug("Ignoring settings file with unexpected shape: %s", p)
        return {}
    return raw


def save_settings(data: Dict[str, Any], path: Optional[pathlib.Path] = None) -> bool:
    """Write `data` as the settings store. Returns False on failure
    instead of raising — a save failure (read-only home, disk full, a
    confined path that isn't writable) must degrade to "not remembered
    next launch", never crash the caller.

    Writes via a temporary file (named with the PID, as profiles.py does)
    so a save interrupted mid-write can never leave a half-written file
    for the next launch to choke on.
    """
    p = pathlib.Path(path) if path is not None else settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_stem(f"{p.stem}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(p)
        return True
    except (OSError, TypeError, ValueError) as e:
        log.debug("Could not write settings file %s: %s", p, e)
        return False


def resolve_codec(data: Dict[str, Any]) -> str:
    """The stored codec choice, or DEFAULT_CODEC if absent/invalid."""
    codec = data.get("codec")
    return codec if codec in _VALID_CODECS else DEFAULT_CODEC


def resolve_touch_enabled(data: Dict[str, Any]) -> bool:
    """The stored touch-input choice, or DEFAULT_TOUCH_ENABLED if
    absent/invalid. Strict `bool` check (not merely truthy) so a
    hand-edited "touch_enabled": "false" (a truthy string) can't silently
    turn touch on.
    """
    touch = data.get("touch_enabled")
    return touch if isinstance(touch, bool) else DEFAULT_TOUCH_ENABLED
