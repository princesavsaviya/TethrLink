"""The four places a version number lives must never drift apart again.

They already did once: setup.py and snapcraft.yaml reached 2.0.1 while
build.gradle.kts and DEBIAN/control stayed on 2.0.0, so the shipped APK
announced a version the server had moved past.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.sync_version import (
    parse_tag, version_code, read_versions, set_version, SOURCES,
)

def test_parse_tag_accepts_a_version_tag():
    assert parse_tag("v2.0.2") == "2.0.2"

def test_parse_tag_rejects_a_non_version_tag():
    # The repo carries `best-quality-h264`; it must never be treated
    # as a release.
    assert parse_tag("best-quality-h264") is None

def test_version_code_is_monotonic():
    assert version_code("2.0.1") < version_code("2.0.2")
    assert version_code("2.0.9") < version_code("2.1.0")
    assert version_code("2.9.9") < version_code("3.0.0")

def test_every_source_is_covered():
    assert set(SOURCES) == {"setup.py", "snapcraft.yaml", "build.gradle.kts", "control"}

def test_set_version_rewrites_all_four(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "snap").mkdir()
    (repo / "android/app").mkdir(parents=True)
    (repo / "debian_build/DEBIAN").mkdir(parents=True)
    (repo / "setup.py").write_text('setup(\n    name="TethrLink",\n    version="2.0.1",\n)\n')
    (repo / "snap/snapcraft.yaml").write_text("name: tethrlink\nversion: '2.0.1'\n")
    (repo / "android/app/build.gradle.kts").write_text(
        '        versionCode   = 4\n        versionName   = "2.0.0"\n'
    )
    (repo / "debian_build/DEBIAN/control").write_text("Package: tethrlink\nVersion: 2.0.0\n")

    monkeypatch.setattr("tools.sync_version.REPO_ROOT", repo)
    changed = set_version("2.1.0")

    assert len(changed) == 4
    assert '    version="2.1.0",' in (repo / "setup.py").read_text()
    assert "version: '2.1.0'" in (repo / "snap/snapcraft.yaml").read_text()
    gradle = (repo / "android/app/build.gradle.kts").read_text()
    assert 'versionName   = "2.1.0"' in gradle
    assert f"versionCode   = {version_code('2.1.0')}" in gradle
    assert "Version: 2.1.0" in (repo / "debian_build/DEBIAN/control").read_text()

def test_the_real_repo_is_internally_consistent():
    """Guards the working tree itself, not a fixture."""
    versions = read_versions()
    assert len(set(versions.values())) == 1, (
        f"version drift across sources: {versions}"
    )
