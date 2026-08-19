#!/bin/bash
# TethrLink .deb Builder Script
#
# usr/lib/tethrlink/ inside debian_build/ is entirely generated: this script
# wipes it and repopulates it from the current server/ source tree plus
# pip-installed third-party dependencies on every run, so the package always
# reflects what's actually in the working tree — never a stale snapshot that
# was hand-copied in at some point and then committed. That drift (missing
# modules, an outdated server_core.py) was the whole bug this script exists
# to prevent; see git history around the 2.0.0 packaging fix for the gory
# details.
#
# The rest of debian_build/ (DEBIAN/control, postinst, postrm, usr/bin/
# tethrlink, the .desktop file, icons) is hand-maintained and tracked in git
# as-is; this script only ever touches usr/lib/tethrlink/ and the Version
# field of DEBIAN/control.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

APP_NAME="tethrlink"
BUILD_DIR="debian_build"
PAYLOAD_DIR="${BUILD_DIR}/usr/lib/tethrlink"
CONTROL_FILE="${BUILD_DIR}/DEBIAN/control"

# ── Version ──────────────────────────────────────────────────────────────
# Derived from setup.py — the single source of truth — rather than
# hardcoded here. DEBIAN/control's Version field is kept in sync below for
# the same reason: two independently-maintained copies of the version
# number is exactly how build_deb.sh ended up frozen at 1.0.0 while the
# real release moved on two versions without it.
VERSION=$(sed -n 's/^[[:space:]]*version="\([0-9][0-9.]*\)".*/\1/p' setup.py | head -1)
if [ -z "${VERSION}" ]; then
    echo "❌ Could not determine VERSION from setup.py" >&2
    exit 1
fi
DEB_FILE="${APP_NAME}_${VERSION}_all.deb"

echo "🚀 Building ${DEB_FILE}..."

echo "🔖 Syncing DEBIAN/control Version to ${VERSION}..."
sed -i "s/^Version: .*/Version: ${VERSION}/" "${CONTROL_FILE}"

# ── Payload ──────────────────────────────────────────────────────────────
echo "🧹 Cleaning previous payload..."
rm -rf "${PAYLOAD_DIR}"
mkdir -p "${PAYLOAD_DIR}"

echo "📂 Copying server/ into the package..."
rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    server/ "${PAYLOAD_DIR}/server/"

echo "📦 Bundling third-party Python dependencies..."
pip install --target "${PAYLOAD_DIR}" mss "qrcode[pil]" --upgrade

# Ensure permissions are correct for DEBIAN scripts and the launcher
chmod 755 "${BUILD_DIR}/DEBIAN/postinst"
chmod 755 "${BUILD_DIR}/DEBIAN/postrm"
chmod 755 "${BUILD_DIR}/usr/bin/tethrlink"

# Build the package
dpkg-deb --build "${BUILD_DIR}" "${DEB_FILE}"

echo "✅ Package built successfully: ${DEB_FILE}"
echo "📦 Contents preview:"
dpkg --contents "${DEB_FILE}"
