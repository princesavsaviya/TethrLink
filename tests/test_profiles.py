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
