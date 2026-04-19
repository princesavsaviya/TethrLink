#!/bin/bash
# TetherLink Build Helper

echo "TetherLink Packaging Helper"
echo "=========================="

case "$1" in
  "exe")
    echo "Building Windows .exe (requires PyInstaller)..."
    ./venv/bin/python -m PyInstaller tetherlink.spec
    echo "Done. Check the 'dist/TetherLink' directory."
    ;;
  "deb")
    echo "Building Debian .deb (requires stdeb)..."
    ./venv/bin/python setup.py --command-packages=stdeb.command bdist_deb
    echo "Done. Check the 'deb_dist' directory."
    ;;
  *)
    echo "Usage: ./build.sh [exe|deb]"
    ;;
esac
