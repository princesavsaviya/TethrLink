"""Write one version number into every file that carries one.

build_deb.sh already treats setup.py as the source of truth for the .deb
and rewrites DEBIAN/control from it. This does the same job for the other
two sources, so a release only ever has to state its version once — on
the tag.

Note on versionCode: releases up to and including 2.0.2 numbered it by hand
and reached 5. version_code() below returns 20002 for 2.0.2, so the first
release to be cut with this tool jumps from 5 to a five-digit number. That
is a one-way jump but a safe one — Android only requires versionCode to
increase, and it does — so do NOT rewrite the value for an already-published
release just to make it match the formula.
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCES = ("setup.py", "snapcraft.yaml", "build.gradle.kts", "control")

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse_tag(tag):
    """'v2.0.2' -> '2.0.2'. None for anything that is not a release tag."""
    m = _TAG.match(tag.strip())
    return ".".join(m.groups()) if m else None


def version_code(version):
    """A monotonic integer for Android. 2.0.2 -> 20002.

    Minor and patch get two digits each, which is room enough and keeps
    the number readable next to the version name.
    """
    major, minor, patch = (int(p) for p in version.split("."))
    return major * 10000 + minor * 100 + patch


def _paths():
    return {
        "setup.py": REPO_ROOT / "setup.py",
        "snapcraft.yaml": REPO_ROOT / "snap/snapcraft.yaml",
        "build.gradle.kts": REPO_ROOT / "android/app/build.gradle.kts",
        "control": REPO_ROOT / "debian_build/DEBIAN/control",
    }


_PATTERNS = {
    "setup.py": re.compile(r'(version=")([0-9][0-9.]*)(")'),
    "snapcraft.yaml": re.compile(r"(^version:\s*')([0-9][0-9.]*)(')", re.M),
    "build.gradle.kts": re.compile(r'(versionName\s*=\s*")([0-9][0-9.]*)(")'),
    "control": re.compile(r"(^Version:\s*)([0-9][0-9.]*)()", re.M),
}


def read_versions():
    """Every source's current version, by source name."""
    found = {}
    for name, path in _paths().items():
        m = _PATTERNS[name].search(path.read_text())
        if m is None:
            raise ValueError(f"no version found in {path}")
        found[name] = m.group(2)
    return found


def set_version(version):
    """Rewrite all four sources. Returns the paths actually written."""
    if parse_tag(f"v{version}") is None:
        raise ValueError(f"not a X.Y.Z version: {version!r}")
    changed = []
    for name, path in _paths().items():
        text = path.read_text()
        new = _PATTERNS[name].sub(rf"\g<1>{version}\g<3>", text)
        if name == "build.gradle.kts":
            new = re.sub(
                r"(versionCode\s*=\s*)(\d+)",
                rf"\g<1>{version_code(version)}",
                new,
            )
        path.write_text(new)
        changed.append(str(path))
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: sync_version.py <X.Y.Z | vX.Y.Z>", file=sys.stderr)
        raise SystemExit(2)
    arg = sys.argv[1]
    v = parse_tag(arg) or parse_tag(f"v{arg}")
    if v is None:
        print(f"not a release version: {arg}", file=sys.stderr)
        raise SystemExit(2)
    for p in set_version(v):
        print(f"updated {p}")
