"""docs/releases.json is a public contract: the releases page renders it and
the release workflow appends to it. Both break silently on a malformed entry,
so the shape is asserted here rather than discovered in production."""
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "docs/releases.json"
INDEX = REPO_ROOT / "docs/index.html"

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The two unversioned names the site's download buttons depend on.
LATEST_ASSET_NAMES = {"deb": "tethrlink_all.deb", "apk": "tethrlink.apk"}


def load():
    return json.loads(MANIFEST.read_text())


def test_manifest_parses_and_declares_its_schema():
    data = load()
    assert data["schema"] == 1
    assert isinstance(data["releases"], list) and data["releases"]


def test_every_release_has_the_required_shape():
    for r in load()["releases"]:
        assert SEMVER.match(r["version"]), r
        assert r["tag"] == f"v{r['version']}", r
        assert ISO_DATE.match(r["date"]), r
        assert isinstance(r["summary"], str) and r["summary"].strip(), r
        assert isinstance(r["changes"], list) and r["changes"], r
        assert all(isinstance(c, str) and c.strip() for c in r["changes"]), r
        android = r["android"]
        assert isinstance(android["minSdk"], int), r
        assert isinstance(android["targetSdk"], int), r
        assert android["minSdk"] <= android["targetSdk"], r
        assert isinstance(android["minLabel"], str) and android["minLabel"], r
        assert set(r["assets"]) == {"deb", "apk"}, r


def test_releases_are_newest_first_and_unique():
    versions = [r["version"] for r in load()["releases"]]
    assert len(versions) == len(set(versions)), "duplicate release entries"
    keyed = [tuple(int(p) for p in v.split(".")) for v in versions]
    assert keyed == sorted(keyed, reverse=True), "not newest-first"


def test_the_site_still_links_the_unversioned_latest_assets():
    """The download buttons on index.html point at fixed, unversioned names
    under releases/latest/download/. If either name changes, every download
    button on the live site breaks."""
    html = INDEX.read_text()
    assert "releases/latest/download/tethrlink_all.deb" in html
    assert "releases/latest/download/tethrlink.apk" in html


def test_the_newest_release_uses_those_same_fixed_names():
    """Only the newest entry is reachable through releases/latest/download/,
    so it is the one that has to match index.html. Historical entries address
    their own tag and may legitimately carry the names that release actually
    shipped — v1.0.0, for instance, published tethrlink_1.0.0_amd64.deb and
    tethrlink_1.0.0_android.apk, long before the names were fixed."""
    newest = load()["releases"][0]
    for kind, expected in LATEST_ASSET_NAMES.items():
        assert newest["assets"][kind] == expected, newest


def test_asset_names_are_either_absent_or_real_filenames():
    """A row may say "this release published no such asset" with null, but it
    may never carry an empty or non-filename string — the page would render a
    link straight to a 404."""
    for r in load()["releases"]:
        for kind, value in r["assets"].items():
            if value is None:
                continue
            assert isinstance(value, str) and value.strip(), r
            assert "/" not in value, r
            suffix = ".deb" if kind == "deb" else ".apk"
            assert value.endswith(suffix), r
