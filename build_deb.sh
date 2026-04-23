#!/bin/bash
# TethrLink .deb Builder Script

set -e

APP_NAME="tethrlink"
VERSION="1.0.0"
BUILD_DIR="debian_build"
DEB_FILE="${APP_NAME}_${VERSION}_all.deb"

echo "🚀 Building ${DEB_FILE}..."

# Bundle missing Python dependencies
echo "📦 Bundling Python dependencies..."
pip install --target ${BUILD_DIR}/usr/lib/tethrlink mss qrcode[pil] --upgrade

# Ensure permissions are correct for DEBIAN scripts
chmod 755 ${BUILD_DIR}/DEBIAN/postinst
chmod 755 ${BUILD_DIR}/DEBIAN/postrm

# Build the package
dpkg-deb --build ${BUILD_DIR} ${DEB_FILE}

echo "✅ Package built successfully: ${DEB_FILE}"
echo "📦 Contents preview:"
dpkg --contents ${DEB_FILE}

