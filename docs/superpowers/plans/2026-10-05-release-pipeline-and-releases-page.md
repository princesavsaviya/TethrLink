# Release Pipeline and Public Releases Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pushing a `vX.Y.Z` tag builds and publishes a public GitHub Release carrying the `.deb` and the signed `.apk`, and updates a public page that lists every release, the Android versions it supports, and what it changed.

**Architecture:** One manifest, `docs/releases.json`, is the contract between the two halves. The release workflow appends an entry to it on every tag and commits it back; the page at `docs/releases.html` renders it client-side with no build step. Version numbers stop being hand-maintained in four files: `tools/sync_version.py` derives all of them from the tag, and a test enforces that they agree.

**Tech Stack:** GitHub Actions, `dpkg-deb` via the existing `build_deb.sh`, Gradle + `actions/setup-java` (Temurin 21), Python 3 + pytest for the version/manifest tooling, plain HTML/CSS/JS for the page (matching `docs/index.html`, which uses no framework).

## Scope note

The pipeline and the page are separable, but both are defined by the `docs/releases.json` schema. Splitting them into two plans would duplicate that schema and let the two copies drift — exactly the failure mode Task 1 exists to kill. They stay in one plan, ordered so Phase A (Tasks 1–4) is independently shippable: the page works against a hand-seeded manifest before any CI exists.

## Global Constraints

- **Release asset names are load-bearing.** `docs/index.html` links to `releases/latest/download/tethrlink_all.deb` and `releases/latest/download/tethrlink.apk`. Assets MUST be uploaded under exactly those two names, unversioned. Renaming either breaks every download button on the live site.
- **Android support floor:** `minSdk = 21` (Android 5.0 Lollipop), `targetSdk = 35`, `compileSdk = 35`. Any entry in the manifest must state the floor the release actually shipped with, not the current one.
- **The signing key never enters the repo.** `*.jks` is gitignored and `tethrlink.jks` has never been committed (verified). `android/app/build.gradle.kts` already reads `TETHRLINK_KEYSTORE_PATH`, `TETHRLINK_KEYSTORE_PASSWORD`, `TETHRLINK_KEY_PASSWORD`; key alias is `tethrlink`. CI must use those same env vars and nothing else.
- **Version sources that must agree:** `setup.py` (`version="X.Y.Z"`), `snap/snapcraft.yaml` (`version: 'X.Y.Z'`), `android/app/build.gradle.kts` (`versionName`), `debian_build/DEBIAN/control` (`Version:`). They are currently drifted — setup.py and snapcraft say 2.0.1, Android and control say 2.0.0.
- **`build_deb.sh` derives the version from `setup.py` and rewrites `DEBIAN/control` itself.** Do not duplicate that logic; sync `setup.py` and let the script do the rest.
- **The deb lands as `tethrlink_${VERSION}_all.deb` in the repo root.** The workflow renames it on upload, not in the script.
- **`docs/` on `main` is the GitHub Pages root.** `docs/CNAME` (`tethrlink.princesavsaviya.com`) exists only on `main`, so the site publishes from `main` — a `docs/releases.json` entry sitting on `develop` is not live. Every release therefore has to reach `main` before the page reflects it. Confirm the Pages source branch in Settings → Pages before Task 8.
- **The Gradle wrapper is pinned to `gradle-9.0-milestone-1`.** Do not upgrade it as part of this work; if CI cannot fetch that distribution, raise it rather than silently bumping.
- Tag format is `vX.Y.Z`. The repo also carries one non-version tag, `best-quality-h264`, which the seeder must skip.

---

## File Structure

| File | Responsibility |
|---|---|
| `tools/sync_version.py` | Create. Single entry point that writes one version into all four sources. Pure functions + a thin CLI. |
| `tests/test_sync_version.py` | Create. Proves each rewrite, and proves the four sources agree in the working tree. |
| `docs/releases.json` | Create. The manifest: one entry per public release. |
| `tests/test_releases_manifest.py` | Create. Schema validation + the asset-name contract with `index.html`. |
| `docs/releases.html` | Create. Renders the manifest. No build step, no framework. |
| `docs/index.html` | Modify. Add a link to the releases page in the nav and near the download cards. |
| `.github/workflows/release.yml` | Create. Tag-triggered build and publish. |
| `docs/RELEASING.md` | Create. The human runbook: required secrets, how to cut a release, how to roll one back. |

---

## Phase A — the manifest and the page

### Task 1: One version, four files

**Files:**
- Create: `tools/sync_version.py`
- Test: `tests/test_sync_version.py`

**Interfaces:**
- Produces: `read_versions() -> dict[str, str]` mapping source name → version string, over the four files; `set_version(version: str) -> list[str]` rewriting all four and returning the paths changed; `parse_tag(tag: str) -> str | None` turning `v2.0.2` into `2.0.2` and returning `None` for a non-version tag; `version_code(version: str) -> int` turning `2.0.2` into a monotonic integer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sync_version.py
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_sync_version.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'tools.sync_version'`.

- [ ] **Step 3: Write the implementation**

```python
# tools/sync_version.py
"""Write one version number into every file that carries one.

build_deb.sh already treats setup.py as the source of truth for the .deb
and rewrites DEBIAN/control from it. This does the same job for the other
two sources, so a release only ever has to state its version once — on
the tag.
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_sync_version.py -v`
Expected: 5 pass, `test_the_real_repo_is_internally_consistent` FAILS with the current drift (`2.0.1` vs `2.0.0`).

- [ ] **Step 5: Resolve the drift, then re-run**

Run: `python3 tools/sync_version.py 2.0.1 && python3 -m pytest tests/test_sync_version.py -v`
Expected: all 6 pass. Review `git diff` — exactly four files, only version lines changed, `versionCode` now `20001`.

- [ ] **Step 6: Commit**

```bash
git add tools/sync_version.py tests/test_sync_version.py setup.py snap/snapcraft.yaml android/app/build.gradle.kts debian_build/DEBIAN/control
git commit -m "build: derive every version number from one place"
```

---

### Task 2: The manifest and its contract

**Files:**
- Create: `docs/releases.json`
- Test: `tests/test_releases_manifest.py`

**Interfaces:**
- Produces: the `docs/releases.json` schema consumed by Task 3 (the page) and appended to by Task 7 (the workflow). Top level is `{"schema": 1, "releases": [...]}`, newest first. Each release: `version` (string `X.Y.Z`), `tag` (string `vX.Y.Z`), `date` (ISO `YYYY-MM-DD`), `android` (`{"minSdk": int, "minLabel": string, "targetSdk": int}`), `summary` (one sentence), `changes` (array of strings), `assets` (`{"deb": string|null, "apk": string|null}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_releases_manifest.py
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


def test_asset_names_match_what_the_site_actually_links_to():
    """The download buttons on index.html point at fixed, unversioned names
    under releases/latest/download/. An entry claiming a different filename
    would render a dead link on the releases page."""
    html = INDEX.read_text()
    assert "releases/latest/download/tethrlink_all.deb" in html
    assert "releases/latest/download/tethrlink.apk" in html
    for r in load()["releases"]:
        for value in r["assets"].values():
            if value is not None:
                assert value in ("tethrlink_all.deb", "tethrlink.apk"), r
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest tests/test_releases_manifest.py -v`
Expected: every test errors — `FileNotFoundError: docs/releases.json`.

- [ ] **Step 3: Seed the manifest**

Seed only the releases that have public assets and that you can describe honestly. Everything before `v1.0.0` was a pre-release iteration on the same day and is not worth listing. Write `docs/releases.json`:

```json
{
  "schema": 1,
  "releases": [
    {
      "version": "2.0.1",
      "tag": "v2.0.1",
      "date": "2026-08-25",
      "android": { "minSdk": 21, "minLabel": "Android 5.0", "targetSdk": 35 },
      "summary": "Packaging fixes so the snap ships the H.264 plugins it needs and launches from the right directory.",
      "changes": [
        "Snap bundles the two GStreamer plugins H.264 actually requires.",
        "The launcher runs from the library directory, so the working directory can no longer shadow the install.",
        "Release the client lock once rather than twice when the virtual display fails."
      ],
      "assets": { "deb": "tethrlink_all.deb", "apk": "tethrlink.apk" }
    },
    {
      "version": "2.0.0",
      "tag": "v2.0.0",
      "date": "2026-08-19",
      "android": { "minSdk": 21, "minLabel": "Android 5.0", "targetSdk": 35 },
      "summary": "Touch input: the tablet can drive the pointer, plus hardware H.264 and a rebuilt site.",
      "changes": [
        "Tap, drag, right-click and scroll on the tablet move the pointer on the PC.",
        "Touch is off by default and negotiated per connection; old clients are unaffected.",
        "The server serves only peers on a USB-attached interface, detected at runtime.",
        "Hardware H.264 encoding with runtime encoder probing."
      ],
      "assets": { "deb": "tethrlink_all.deb", "apk": "tethrlink.apk" }
    },
    {
      "version": "1.1.0",
      "tag": "v1.1.0",
      "date": "2026-08-18",
      "android": { "minSdk": 21, "minLabel": "Android 5.0", "targetSdk": 34 },
      "summary": "A working H.264 video pipeline, with JPEG kept as the fallback.",
      "changes": [
        "H.264 becomes the default codec; JPEG is now the opt-in fallback.",
        "Capture geometry is derived from the connected device instead of being rescaled.",
        "Encoded frames route through a lossless FIFO so overflow cannot emit corrupt frames."
      ],
      "assets": { "deb": "tethrlink_all.deb", "apk": "tethrlink.apk" }
    },
    {
      "version": "1.0.0",
      "tag": "v1.0.0",
      "date": "2026-04-19",
      "android": { "minSdk": 21, "minLabel": "Android 5.0", "targetSdk": 34 },
      "summary": "First public release — a real second display over USB, streamed as JPEG.",
      "changes": [
        "GNOME ScreenCast capture of a dedicated virtual monitor.",
        "Automatic server discovery over the USB tether.",
        "Debian package and Android client."
      ],
      "assets": { "deb": "tethrlink_all.deb", "apk": "tethrlink.apk" }
    }
  ]
}
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_releases_manifest.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add docs/releases.json tests/test_releases_manifest.py
git commit -m "docs: add the release manifest the releases page renders"
```

---

### Task 3: The releases page

**Files:**
- Create: `docs/releases.html`

**Interfaces:**
- Consumes: `docs/releases.json` from Task 2 (fetched relative, so it works on Pages and from `file://` only via a local server).
- Produces: a page at `/releases.html` that Task 4 links to.

- [ ] **Step 1: Write the page**

Match `docs/index.html`'s existing look — it defines its palette as CSS custom properties on `:root` (`--line` and friends) and uses no framework. Reuse the same tokens by copying the `:root` block from `index.html` rather than inventing a second palette.

```html
<!-- docs/releases.html -->
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>TethrLink — Releases</title>
<meta name="description" content="Every TethrLink release: what changed, which Android versions it supports, and where to download it."/>
<style>
  /* Copy the :root custom properties verbatim from index.html so the two
     pages cannot drift into two different palettes. */
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  .wrap { max-width: 860px; margin: 0 auto; padding: 48px 20px 96px; }
  h1 { font-size: clamp(28px, 5vw, 42px); margin: 0 0 8px; }
  .lede { opacity: .7; margin: 0 0 40px; }
  .rel { border:1px solid var(--line); border-radius:14px; padding:20px 22px; margin-bottom:18px; }
  .rel-head { display:flex; flex-wrap:wrap; gap:10px; align-items:baseline; justify-content:space-between; }
  .ver { font-size:22px; font-weight:700; }
  .date { opacity:.55; font-size:13px; }
  .latest { font-size:11px; letter-spacing:.06em; text-transform:uppercase;
            border:1px solid currentColor; border-radius:999px; padding:2px 8px; }
  .summary { margin:10px 0 14px; }
  .compat { font-size:13px; opacity:.75; margin:0 0 12px; }
  ul.changes { margin:0 0 16px; padding-left:20px; }
  ul.changes li { margin-bottom:6px; }
  .dl { display:flex; flex-wrap:wrap; gap:10px; }
  .dl a { font-size:13px; text-decoration:none; border:1px solid var(--line);
          border-radius:10px; padding:8px 14px; }
  .err { padding:20px; border:1px solid var(--line); border-radius:12px; }
</style>

<div class="wrap">
  <p><a href="./">← TethrLink</a></p>
  <h1>Releases</h1>
  <p class="lede">Every public release, what it changed, and the Android versions it supports.</p>
  <div id="list"><p class="lede">Loading…</p></div>
</div>

<script>
const REPO = "https://github.com/princesavsaviya/TethrLink";

function assetUrl(tag, name) {
  // Per-release assets live under the tag; only `latest` has the stable
  // unversioned path, so historical rows must address their own tag.
  return `${REPO}/releases/download/${tag}/${name}`;
}

function render(data) {
  const list = document.getElementById("list");
  list.innerHTML = "";
  data.releases.forEach((r, i) => {
    const el = document.createElement("article");
    el.className = "rel";

    const head = document.createElement("div");
    head.className = "rel-head";
    const left = document.createElement("div");
    const ver = document.createElement("span");
    ver.className = "ver";
    ver.textContent = r.version;
    left.appendChild(ver);
    if (i === 0) {
      const tagEl = document.createElement("span");
      tagEl.className = "latest";
      tagEl.textContent = "Latest";
      tagEl.style.marginLeft = "10px";
      left.appendChild(tagEl);
    }
    const date = document.createElement("span");
    date.className = "date";
    date.textContent = r.date;
    head.append(left, date);
    el.appendChild(head);

    const sum = document.createElement("p");
    sum.className = "summary";
    sum.textContent = r.summary;
    el.appendChild(sum);

    const compat = document.createElement("p");
    compat.className = "compat";
    compat.textContent =
      `Android ${r.android.minLabel.replace("Android ", "")} and newer ` +
      `(API ${r.android.minSdk}–${r.android.targetSdk}) · Linux with GNOME on Wayland`;
    el.appendChild(compat);

    const ul = document.createElement("ul");
    ul.className = "changes";
    r.changes.forEach(c => {
      const li = document.createElement("li");
      li.textContent = c;
      ul.appendChild(li);
    });
    el.appendChild(ul);

    const dl = document.createElement("div");
    dl.className = "dl";
    if (r.assets.deb) {
      const a = document.createElement("a");
      a.href = assetUrl(r.tag, r.assets.deb);
      a.textContent = ".deb package";
      dl.appendChild(a);
    }
    if (r.assets.apk) {
      const a = document.createElement("a");
      a.href = assetUrl(r.tag, r.assets.apk);
      a.textContent = "Android .apk";
      dl.appendChild(a);
    }
    const notes = document.createElement("a");
    notes.href = `${REPO}/releases/tag/${r.tag}`;
    notes.textContent = "Release notes";
    dl.appendChild(notes);
    el.appendChild(dl);

    list.appendChild(el);
  });
}

fetch("releases.json", { cache: "no-cache" })
  .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(render)
  .catch(e => {
    document.getElementById("list").innerHTML =
      `<div class="err">Couldn't load the release list (${e.message}). ` +
      `All releases are on <a href="${REPO}/releases">GitHub</a>.</div>`;
  });
</script>
```

- [ ] **Step 2: Serve and check it renders**

Run: `python3 -m http.server 8099 --directory docs`
Open `http://localhost:8099/releases.html`. Expected: four release cards, newest first, `2.0.1` badged "Latest", each with compatibility line, bullets, and three links. `fetch` of a `file://` path is blocked by CORS, so the local server is required — this is not a bug in the page.

- [ ] **Step 3: Check the failure path**

Run: `mv docs/releases.json /tmp/ && ` reload the page.
Expected: the error card, naming the HTTP status and linking to GitHub releases. Then `mv /tmp/releases.json docs/`.

- [ ] **Step 4: Commit**

```bash
git add docs/releases.html
git commit -m "docs: add a public releases page"
```

---

### Task 4: Link the page from the site

**Files:**
- Modify: `docs/index.html` (the nav, and the download section near the `.dl-card` block around line 1000)

- [ ] **Step 1: Add the links**

Add a list item to the existing `ul.nav-links` at `docs/index.html:493`:

```html
<li><a href="releases.html">Releases</a></li>
```

and below the three download cards (around `docs/index.html:1000`) add:

```html
<p style="text-align:center;margin-top:18px;font-size:14px;opacity:.7">
  Looking for an older version? <a href="releases.html">See all releases</a>.
</p>
```

- [ ] **Step 2: Verify both links resolve**

Run: `python3 -m http.server 8099 --directory docs` and click through from `/` to `/releases.html` and back.
Expected: both directions work.

- [ ] **Step 3: Re-run the manifest contract test**

Run: `python3 -m pytest tests/test_releases_manifest.py -v`
Expected: 5 passed — in particular `test_asset_names_match_what_the_site_actually_links_to`, which reads this file.

- [ ] **Step 4: Commit**

```bash
git add docs/index.html
git commit -m "docs: link the releases page from the site"
```

---

## Phase B — the pipeline

### Task 5: Build the .deb in CI

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Produces: a `deb` job uploading an artifact named `deb` containing `tethrlink_all.deb`.

- [ ] **Step 1: Write the workflow skeleton with the deb job**

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags: ['v*.*.*']
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to dry-run (no release is published)'
        required: true

permissions:
  contents: write

jobs:
  version:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.v.outputs.version }}
    steps:
      - uses: actions/checkout@v4
      - id: v
        run: |
          set -euo pipefail
          RAW="${{ github.event.inputs.version || github.ref_name }}"
          V=$(python3 -c "import sys;sys.path.insert(0,'.');from tools.sync_version import parse_tag;t=parse_tag('$RAW') or parse_tag('v$RAW');print(t or '')")
          if [ -z "$V" ]; then echo "::error::'$RAW' is not a vX.Y.Z release tag"; exit 1; fi
          echo "version=$V" >> "$GITHUB_OUTPUT"

  deb:
    needs: version
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - name: Pin every version source to the tag
        run: python3 tools/sync_version.py "${{ needs.version.outputs.version }}"
      - name: Build the package
        run: ./build_deb.sh
      - name: Rename to the stable asset name
        # index.html links to releases/latest/download/tethrlink_all.deb;
        # the built file is versioned, so it is renamed here rather than in
        # build_deb.sh, which other workflows rely on keeping versioned.
        run: mv "tethrlink_${{ needs.version.outputs.version }}_all.deb" tethrlink_all.deb
      - uses: actions/upload-artifact@v4
        with:
          name: deb
          path: tethrlink_all.deb
          if-no-files-found: error
```

- [ ] **Step 2: Dry-run it**

Push the branch, then from the Actions tab run the workflow via **Run workflow** with version `2.0.1`.
Expected: the `deb` job succeeds and the run page offers a `deb` artifact. Download it and run `dpkg --contents tethrlink_all.deb`; expect `usr/lib/tethrlink/server/` populated and `usr/bin/tethrlink` present.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: build the .deb on a release tag"
```

---

### Task 6: Build the signed APK in CI

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: repository secrets `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_PASSWORD`.
- Produces: an `apk` job uploading an artifact named `apk` containing `tethrlink.apk`.

- [ ] **Step 1: Add the secrets (manual, once)**

```bash
base64 -w0 tethrlink.jks > /tmp/ks.b64
```

In GitHub → Settings → Secrets and variables → Actions, add `ANDROID_KEYSTORE_BASE64` (contents of `/tmp/ks.b64`), `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_PASSWORD`. Then `shred -u /tmp/ks.b64`. The key alias is already hardcoded as `tethrlink` in `build.gradle.kts`; do not add a secret for it.

- [ ] **Step 2: Add the job**

```yaml
  apk:
    needs: version
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: '21'
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - name: Pin every version source to the tag
        run: python3 tools/sync_version.py "${{ needs.version.outputs.version }}"
      - name: Restore the signing keystore
        env:
          KS: ${{ secrets.ANDROID_KEYSTORE_BASE64 }}
        run: |
          set -euo pipefail
          if [ -z "$KS" ]; then echo "::error::ANDROID_KEYSTORE_BASE64 is not set"; exit 1; fi
          echo "$KS" | base64 -d > "$RUNNER_TEMP/release.jks"
      - name: Build the release APK
        working-directory: android
        env:
          TETHRLINK_KEYSTORE_PATH: ${{ runner.temp }}/release.jks
          TETHRLINK_KEYSTORE_PASSWORD: ${{ secrets.ANDROID_KEYSTORE_PASSWORD }}
          TETHRLINK_KEY_PASSWORD: ${{ secrets.ANDROID_KEY_PASSWORD }}
        run: ./gradlew :app:assembleRelease --no-daemon
      - name: Verify it is actually signed
        # An unsigned or debug-signed APK installs for nobody as an upgrade.
        run: |
          set -euo pipefail
          APK=android/app/build/outputs/apk/release/app-release.apk
          "$ANDROID_HOME"/build-tools/35.0.0/apksigner verify --print-certs "$APK"
          mv "$APK" tethrlink.apk
      - name: Wipe the keystore
        if: always()
        run: rm -f "$RUNNER_TEMP/release.jks"
      - uses: actions/upload-artifact@v4
        with:
          name: apk
          path: tethrlink.apk
          if-no-files-found: error
```

- [ ] **Step 3: Dry-run and verify the signature**

Run the workflow manually with version `2.0.1`. Expected: `apk` artifact produced, and the "Verify it is actually signed" step prints a certificate whose SHA-256 matches your release key. Confirm locally against your own keystore:

```bash
keytool -list -v -keystore tethrlink.jks -alias tethrlink | grep SHA256
```

Expected: the two SHA-256 fingerprints are identical. If they differ, the wrong key is in the secret — stop and fix before publishing anything.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: build and sign the release APK"
```

---

### Task 7: Publish the release and update the manifest

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: the `deb` and `apk` artifacts from Tasks 5 and 6, and the `docs/releases.json` schema from Task 2.

- [ ] **Step 1: Add the publish job**

```yaml
  publish:
    needs: [version, deb, apk]
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: develop
          token: ${{ secrets.GITHUB_TOKEN }}
      - uses: actions/download-artifact@v4
        with: { name: deb, path: dist }
      - uses: actions/download-artifact@v4
        with: { name: apk, path: dist }

      - name: Fail if the manifest has no entry for this tag
        # The release notes are written by a human, in the manifest, before
        # the tag is pushed. Generating them from commit subjects instead
        # would publish "fix: ..." lines to users who have never read this
        # repository.
        run: |
          set -euo pipefail
          python3 - <<'PY'
          import json, os, sys
          v = os.environ["V"]
          data = json.load(open("docs/releases.json"))
          if not any(r["version"] == v for r in data["releases"]):
              sys.exit(f"docs/releases.json has no entry for {v} — add it, commit, then re-tag")
          PY
        env:
          V: ${{ needs.version.outputs.version }}

      - name: Render the release notes from the manifest
        env:
          V: ${{ needs.version.outputs.version }}
        run: |
          python3 - > NOTES.md <<'PY'
          import json, os
          v = os.environ["V"]
          r = next(x for x in json.load(open("docs/releases.json"))["releases"]
                   if x["version"] == v)
          print(r["summary"]); print()
          for c in r["changes"]:
              print(f"- {c}")
          print()
          a = r["android"]
          print(f"**Android:** {a['minLabel']} and newer (API {a['minSdk']}–{a['targetSdk']}).")
          print("**Linux:** GNOME on Wayland.")
          print()
          print("All releases: https://tethrlink.princesavsaviya.com/releases.html")
          PY

      - name: Publish
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh release create "${{ github.ref_name }}" \
            dist/tethrlink_all.deb dist/tethrlink.apk \
            --title "TethrLink ${{ needs.version.outputs.version }}" \
            --notes-file NOTES.md

      - name: Commit the synced version files back to develop
        run: |
          set -euo pipefail
          python3 tools/sync_version.py "${{ needs.version.outputs.version }}"
          if git diff --quiet; then echo "already in sync"; exit 0; fi
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git commit -am "release: ${{ needs.version.outputs.version }}"
          git push origin develop
```

- [ ] **Step 2: Rehearse on a throwaway tag**

```bash
git tag v0.0.1-rehearsal && git push origin v0.0.1-rehearsal
```

Expected: the `version` job FAILS with "not a vX.Y.Z release tag", proving non-release tags cannot publish. Then delete it:

```bash
git push --delete origin v0.0.1-rehearsal && git tag -d v0.0.1-rehearsal
```

- [ ] **Step 3: Verify the manifest gate**

Temporarily rename the `2.0.1` entry's version to `2.0.99` in `docs/releases.json`, commit on a branch, and run the workflow manually.
Expected: the publish job is skipped (manual runs aren't tags), so instead confirm the gate by reverting and relying on the real tag in Task 8.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: publish the GitHub Release from the manifest"
```

---

### Task 8: The runbook, and the first real release

**Files:**
- Create: `docs/RELEASING.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Cutting a release

1. Add the new entry at the **top** of `docs/releases.json` — version, tag,
   date, Android floor, summary, changes, assets. The workflow refuses to
   publish a tag the manifest does not describe, so this comes first.
2. `python3 -m pytest tests/test_releases_manifest.py tests/test_sync_version.py`
3. Commit and push to `develop`, then merge to `main`.
4. `git tag vX.Y.Z && git push origin vX.Y.Z`
5. Watch the run. It builds the `.deb` and the signed `.apk`, publishes a
   GitHub Release with both under their stable names, and pushes a commit
   syncing the four version sources.

## Required secrets

| Secret | What it is |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | `base64 -w0 tethrlink.jks` |
| `ANDROID_KEYSTORE_PASSWORD` | Keystore password |
| `ANDROID_KEY_PASSWORD` | Password for the `tethrlink` key |

The key alias `tethrlink` is hardcoded in `android/app/build.gradle.kts`.
`tethrlink.jks` is gitignored and must never be committed.

## Rolling back

Deleting a GitHub Release does not un-publish it from anyone who already
downloaded it. To supersede a bad release, publish the next patch version —
`releases/latest/download/` follows the newest release automatically.

## Why the asset names are fixed

`docs/index.html` links to `releases/latest/download/tethrlink_all.deb` and
`releases/latest/download/tethrlink.apk`. Those names are a public contract;
`tests/test_releases_manifest.py` asserts it.
```

- [ ] **Step 2: Cut the first real release end to end**

Add a `2.0.2` entry to `docs/releases.json` describing the discovery fix from `b0a3cda`, then:

```bash
git tag v2.0.2 && git push origin v2.0.2
```

Expected: a published release with both assets, notes rendered from the manifest, and `https://tethrlink.princesavsaviya.com/releases.html` showing `2.0.2` badged "Latest".

- [ ] **Step 3: Verify the live download paths**

```bash
curl -sIL https://github.com/princesavsaviya/TethrLink/releases/latest/download/tethrlink_all.deb | grep -E '^HTTP|location' | tail -2
curl -sIL https://github.com/princesavsaviya/TethrLink/releases/latest/download/tethrlink.apk | grep -E '^HTTP' | tail -1
```

Expected: both end in `HTTP/2 200`. These are the URLs the live site's download buttons use.

- [ ] **Step 4: Commit**

```bash
git add docs/RELEASING.md
git commit -m "docs: add the release runbook"
```

---

## Risks

- **The Gradle wrapper is a milestone build** (`gradle-9.0-milestone-1`). If CI cannot fetch it, raise it rather than bumping the wrapper as a side effect of this work — a Gradle upgrade is its own change with its own testing.
- **`build_deb.sh` runs `pip install --target`**, so the `.deb` contents depend on what PyPI serves at build time. A release built twice may not be byte-identical. Out of scope here; worth pinning later.
- **`apksigner`'s path assumes build-tools 35.0.0** is present on the runner image. If the image changes, resolve it with `$(ls -d $ANDROID_HOME/build-tools/* | tail -1)` instead of failing silently.
- **The publish job pushes to `develop`.** If `develop` has moved since the tag, the push is rejected and the release is already live — the version-sync commit then has to be made by hand.
- **The page lags the release unless `main` is updated.** Pages serves `main`, the workflow commits to `develop`, and releases are tagged from `main`. Decide one of: tag only from `main` and have the workflow push there, or add a step that fast-forwards `docs/releases.json` onto `main`. Until that is settled, a published release will not appear on the live page without a manual merge.
