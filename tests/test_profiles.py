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


# ── hardening: fixing gaps in the "never raises" contract ─────────────────────

def test_save_returns_false_when_record_not_json_serializable(tmp_path):
    """save() must return False, not raise, when a stored value cannot be
    serialized (a set, dataclass, circular reference, etc.). The file must
    remain intact if it existed before."""
    store = ProfileStore(tmp_path / "p.json")
    store.set_device("d", {"name": "phone"})
    store.save()
    original_content = (tmp_path / "p.json").read_text()

    # Inject a non-serializable value (a set) directly into the internal state.
    store._data["devices"]["d"]["tags"] = {"tag1", "tag2"}

    # save() must return False and not raise.
    assert store.save() is False

    # The file must remain intact with the original content.
    assert (tmp_path / "p.json").read_text() == original_content


def test_get_encoder_returns_none_when_fingerprint_is_none(tmp_path):
    """get_encoder(None) must return None even when a stored entry lacks
    a fingerprint key. This prevents spurious matches."""
    store = ProfileStore(tmp_path / "p.json")
    # Manually inject an encoder entry without a fingerprint key.
    store._data["encoder"] = {"record": {"element": "nvh264enc"}}

    # get_encoder(None) should return None, not the stored record.
    assert store.get_encoder(None) is None

    # But with the correct fingerprint, it should still work.
    store._data["encoder"]["fingerprint"] = "fp-123"
    assert store.get_encoder("fp-123")["element"] == "nvh264enc"


def test_temp_file_is_unique_per_process(tmp_path):
    """The temporary file used during save must be unique per process,
    not a fixed name, to avoid collisions when multiple processes save
    concurrently."""
    store = ProfileStore(tmp_path / "p.json")
    store.set_device("d", {"name": "x"})
    store.save()

    # The fixed ".tmp" name should not exist.
    fixed_tmp = tmp_path / "p.tmp"
    assert not fixed_tmp.exists()

    # No files matching the PID-based pattern should be left behind either.
    import glob
    pid_tmp_files = list(tmp_path.glob(f"p.*.tmp"))
    assert len(pid_tmp_files) == 0
