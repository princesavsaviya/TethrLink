#!/bin/bash
# TethrLink snap builder.
#
# Two things this exists to prevent, both of which have actually happened:
#
# 1. Packing a stale payload. The snap's contents come from debian_build/,
#    whose usr/lib/tethrlink/ subtree is *generated* by build_deb.sh from
#    server/ and is gitignored. `snapcraft pack` on its own happily ships
#    whatever was last generated there — in one case a server/ snapshot four
#    months older than the working tree, silently. So the payload is always
#    regenerated first, here, with no way to skip it.
#
# 2. Building the wrong recipe. There are two, and they are not
#    interchangeable:
#
#      snap/snapcraft.yaml          devmode — the shipping recipe
#      snap/snapcraft-classic.yaml  classic — work in progress
#
#    snapcraft only ever reads snap/snapcraft.yaml, so building the classic
#    variant means temporarily putting it there. Doing that by hand is how
#    you end up publishing one confinement believing you published the
#    other; this script swaps it in, always restores it (even on Ctrl-C or
#    failure), and gives each variant its own artifact name.
#
# Usage:
#   ./build_snap.sh            # devmode (default)
#   ./build_snap.sh devmode
#   ./build_snap.sh classic

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VARIANT="${1:-devmode}"
RECIPE="snap/snapcraft.yaml"
CLASSIC_RECIPE="snap/snapcraft-classic.yaml"
BACKUP="snap/.snapcraft.yaml.shipping"

case "${VARIANT}" in
    devmode|classic) ;;
    *)
        echo "❌ Unknown variant '${VARIANT}' — expected 'devmode' or 'classic'" >&2
        exit 1
        ;;
esac

VERSION=$(sed -n 's/^[[:space:]]*version="\([0-9][0-9.]*\)".*/\1/p' setup.py | head -1)
if [ -z "${VERSION}" ]; then
    echo "❌ Could not determine VERSION from setup.py" >&2
    exit 1
fi

# ── Payload ──────────────────────────────────────────────────────────────
# Unconditional: see note 1 above. build_deb.sh is the only thing that knows
# how to populate debian_build/usr/lib/tethrlink/, and it is cheap to rerun.
echo "🔄 Regenerating the payload from server/ ..."
./build_deb.sh > /dev/null
echo "✅ Payload is current"

# ── Recipe ───────────────────────────────────────────────────────────────
restore_recipe() {
    if [ -f "${BACKUP}" ]; then
        mv -f "${BACKUP}" "${RECIPE}"
        echo "↩️  Restored the shipping recipe at ${RECIPE}"
    fi
}

if [ "${VARIANT}" = "classic" ]; then
    if [ ! -f "${CLASSIC_RECIPE}" ]; then
        echo "❌ ${CLASSIC_RECIPE} not found" >&2
        exit 1
    fi
    # The trap covers every exit path, including a failed snapcraft run and
    # Ctrl-C mid-build: snap/snapcraft.yaml must never be left holding the
    # classic recipe.
    trap restore_recipe EXIT INT TERM
    cp -f "${RECIPE}" "${BACKUP}"
    cp -f "${CLASSIC_RECIPE}" "${RECIPE}"
    echo "🔧 Building the CLASSIC variant (work in progress)"
else
    echo "🔧 Building the DEVMODE variant (shipping)"
fi

# ── Pack ─────────────────────────────────────────────────────────────────
echo "📦 Packing ..."
snapcraft pack --use-lxd

# Both recipes declare the same name and version, so snapcraft writes both
# variants to the same filename. Suffixing *both* — not just classic — is
# deliberate: the first run of this script packed classic over an existing
# devmode artifact and destroyed it, because the rename happened after the
# pack. Neither variant now owns the unsuffixed name.
PACKED="tethrlink_${VERSION}_amd64_${VARIANT}.snap"
mv -f "tethrlink_${VERSION}_amd64.snap" "${PACKED}"

echo "✅ Packed ${PACKED}"
echo
echo "Confinement actually built:"
unsquashfs -cat "${PACKED}" meta/snap.yaml 2>/dev/null | grep -E '^(confinement|grade|version):' || true
echo
if [ "${VARIANT}" = "classic" ]; then
    echo "Install to test:  sudo snap install --dangerous --classic ./${PACKED}"
else
    echo "Install to test:  sudo snap install --dangerous --devmode ./${PACKED}"
fi
