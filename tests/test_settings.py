import json
import os

from server.core.settings import (
    DEFAULT_CODEC,
    DEFAULT_TOUCH_ENABLED,
    load_settings,
    resolve_codec,
    resolve_touch_enabled,
    save_settings,
    settings_path,
)


# ── location resolution ──────────────────────────────────────────────────────

def test_prefers_xdg_config_home_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert str(settings_path()).startswith(str(tmp_path / "xdg"))


def test_falls_back_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = settings_path()
    assert str(p).startswith(str(tmp_path / ".config"))


def test_path_is_namespaced_and_named():
    p = settings_path()
    assert "tethrlink" in str(p)
    assert p.name == "settings.json"


# ── round trip ────────────────────────────────────────────────────────────────

def test_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    assert save_settings({"codec": "jpeg", "touch_enabled": True}, path) is True

    reloaded = load_settings(path)
    assert resolve_codec(reloaded) == "jpeg"
    assert resolve_touch_enabled(reloaded) is True


def test_round_trips_default_values(tmp_path):
    path = tmp_path / "settings.json"
    save_settings({"codec": "h264", "touch_enabled": False}, path)

    reloaded = load_settings(path)
    assert resolve_codec(reloaded) == "h264"
    assert resolve_touch_enabled(reloaded) is False


def test_save_writes_valid_json(tmp_path):
    path = tmp_path / "settings.json"
    save_settings({"codec": "h264", "touch_enabled": False}, path)
    parsed = json.loads(path.read_text())
    assert parsed == {"codec": "h264", "touch_enabled": False}


# ── robustness: a settings file must never break the app ─────────────────────

def test_corrupt_file_falls_back_to_defaults(tmp_path):
    bad = tmp_path / "settings.json"
    bad.write_text("{ this is not valid json")

    reloaded = load_settings(bad)
    assert resolve_codec(reloaded) == DEFAULT_CODEC
    assert resolve_touch_enabled(reloaded) is DEFAULT_TOUCH_ENABLED


def test_missing_file_falls_back_to_defaults(tmp_path):
    reloaded = load_settings(tmp_path / "absent.json")
    assert resolve_codec(reloaded) == DEFAULT_CODEC
    assert resolve_touch_enabled(reloaded) is DEFAULT_TOUCH_ENABLED


def test_wrong_shape_file_falls_back_to_defaults(tmp_path):
    """Valid JSON of the wrong top-level type must not crash the accessors."""
    odd = tmp_path / "settings.json"
    odd.write_text('["a list, not an object"]')

    reloaded = load_settings(odd)
    assert resolve_codec(reloaded) == DEFAULT_CODEC
    assert resolve_touch_enabled(reloaded) is DEFAULT_TOUCH_ENABLED


def test_invalid_codec_value_falls_back_to_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"codec": "vp9", "touch_enabled": True}))

    reloaded = load_settings(path)
    assert resolve_codec(reloaded) == DEFAULT_CODEC
    # A bad codec value shouldn't drag touch_enabled down with it.
    assert resolve_touch_enabled(reloaded) is True


def test_non_bool_touch_enabled_falls_back_to_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"codec": "jpeg", "touch_enabled": "false"}))

    reloaded = load_settings(path)
    assert resolve_codec(reloaded) == "jpeg"
    # A truthy string must not be treated as touch enabled.
    assert resolve_touch_enabled(reloaded) is DEFAULT_TOUCH_ENABLED


def test_save_reports_failure_rather_than_raising(tmp_path):
    unwritable = tmp_path / "nodir" / "deeper" / "settings.json"
    os.makedirs(tmp_path / "nodir", exist_ok=True)
    os.chmod(tmp_path / "nodir", 0o400)
    try:
        assert save_settings({"codec": "h264", "touch_enabled": False}, unwritable) is False
    finally:
        os.chmod(tmp_path / "nodir", 0o700)


def test_temp_file_is_unique_per_process(tmp_path):
    path = tmp_path / "settings.json"
    save_settings({"codec": "h264", "touch_enabled": False}, path)

    fixed_tmp = tmp_path / "settings.tmp"
    assert not fixed_tmp.exists()

    import glob
    pid_tmp_files = list(glob.glob(str(tmp_path / "settings.*.tmp")))
    assert len(pid_tmp_files) == 0
