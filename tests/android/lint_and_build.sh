#!/usr/bin/env bash
# Usage: lint_and_build.sh <report_dir>
set -euo pipefail

REPORT_DIR="${1:?Usage: lint_and_build.sh <report_dir>}"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ANDROID_DIR="$REPO_ROOT/android"
STATUS_FILE="$REPORT_DIR/android_status.txt"

echo "ANDROID_LINT=PENDING"  > "$STATUS_FILE"
echo "ANDROID_BUILD=PENDING" >> "$STATUS_FILE"

cd "$ANDROID_DIR"

# Lint
echo "=== Android Lint ===" | tee "$REPORT_DIR/lint-output.txt"
if ./gradlew lint >> "$REPORT_DIR/lint-output.txt" 2>&1; then
    sed -i 's/ANDROID_LINT=PENDING/ANDROID_LINT=PASS/' "$STATUS_FILE"
    echo "Lint: PASS"
else
    sed -i 's/ANDROID_LINT=PENDING/ANDROID_LINT=WARN/' "$STATUS_FILE"
    echo "Lint: WARN (issues found — see lint-output.txt)"
fi

# Copy lint HTML report if it exists
LINT_HTML=$(find . -name "lint-results*.html" 2>/dev/null | head -1)
[ -n "$LINT_HTML" ] && cp "$LINT_HTML" "$REPORT_DIR/lint-report.html"

# Build
echo "=== Android Build ===" | tee "$REPORT_DIR/build-output.txt"
if ./gradlew assembleRelease >> "$REPORT_DIR/build-output.txt" 2>&1; then
    APK=$(find . -name "*.apk" -path "*/release/*" 2>/dev/null | head -1)
    if [ -n "$APK" ]; then
        cp "$APK" "$REPORT_DIR/app-release.apk"
        echo "Build: PASS — APK copied to $REPORT_DIR/app-release.apk"
    fi
    sed -i 's/ANDROID_BUILD=PENDING/ANDROID_BUILD=PASS/' "$STATUS_FILE"
else
    sed -i 's/ANDROID_BUILD=PENDING/ANDROID_BUILD=FAIL/' "$STATUS_FILE"
    echo "Build: FAIL — check build-output.txt"
    exit 1
fi
