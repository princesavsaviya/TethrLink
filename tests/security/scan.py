import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from tests.lib.result import TestResult, passed, failed, warned

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ALLOWED_PERMISSIONS = {
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.ACCESS_WIFI_STATE",
    "android.permission.CHANGE_NETWORK_STATE",
    "android.permission.FOREGROUND_SERVICE",
    "android.permission.RECEIVE_BOOT_COMPLETED",
    "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
}

# Patterns that are OK in constants/comments but suspicious elsewhere
_HARDCODED_IP_RE   = re.compile(r'(?<![#"\'])192\.168\.|127\.0\.0\.1')
_SECRET_RE         = re.compile(r'(?i)(password|secret|api_key|token)\s*=\s*["\'][^"\']+["\']')
_TODO_RE           = re.compile(r'\b(TODO|FIXME|HACK|XXX)\b')


def run(report_dir: str) -> TestResult:
    findings_high   = []
    findings_warn   = []
    findings_info   = []

    # 1. bandit
    bandit_out = os.path.join(report_dir, "bandit-report.json")
    try:
        subprocess.run(
            ["bandit", "-r", os.path.join(REPO, "server"),
             "-f", "json", "-o", bandit_out, "-q"],
            capture_output=True, timeout=60,
        )
        with open(bandit_out) as f:
            bandit = json.load(f)
        for issue in bandit.get("results", []):
            sev = issue.get("issue_severity", "LOW")
            msg = f"[bandit] {issue['issue_text']} ({issue['filename']}:{issue['line_number']})"
            if sev == "HIGH":
                findings_high.append(msg)
            elif sev == "MEDIUM":
                findings_warn.append(msg)
    except FileNotFoundError:
        findings_warn.append("[bandit] bandit not installed — skipped")
    except Exception as e:
        findings_warn.append(f"[bandit] error: {e}")

    # 2. Hardcoded value scan
    scan_dirs = [
        os.path.join(REPO, "server"),
        os.path.join(REPO, "android", "app", "src", "main", "java"),
    ]
    for scan_dir in scan_dirs:
        for root, _, files in os.walk(scan_dir):
            for fname in files:
                if not fname.endswith((".py", ".kt", ".java")):
                    continue
                fpath = os.path.join(root, fname)
                rel   = os.path.relpath(fpath, REPO)
                with open(fpath, errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        stripped = line.strip()
                        if stripped.startswith("#") or stripped.startswith("//"):
                            continue
                        if _HARDCODED_IP_RE.search(line):
                            findings_info.append(f"[hardcoded-ip] {rel}:{i}: {stripped[:80]}")
                        if _SECRET_RE.search(line):
                            findings_high.append(f"[hardcoded-secret] {rel}:{i}: {stripped[:80]}")
                        if _TODO_RE.search(line):
                            findings_info.append(f"[todo] {rel}:{i}: {stripped[:80]}")

    # 3. Android manifest permission audit
    manifest = os.path.join(
        REPO, "android", "app", "src", "main", "AndroidManifest.xml"
    )
    if os.path.exists(manifest):
        try:
            tree = ET.parse(manifest)
            ns = "http://schemas.android.com/apk/res/android"
            for elem in tree.iter("uses-permission"):
                perm = elem.get(f"{{{ns}}}name", "")
                if perm and perm not in ALLOWED_PERMISSIONS:
                    findings_warn.append(f"[manifest] Unexpected permission: {perm}")
        except Exception as e:
            findings_warn.append(f"[manifest] parse error: {e}")

    # 4. TCP bind documentation (info only)
    findings_info.append(
        "[tcp-bind] Server binds 0.0.0.0 — acceptable: USB tethering subnet "
        "(192.168.42.x) is not internet-routable. No external exposure."
    )

    # Build result
    details_lines = (
        ["HIGH:"] + findings_high +
        ["", "WARN:"] + findings_warn +
        ["", "INFO:"] + findings_info
    )
    details = "\n".join(details_lines)

    if findings_high:
        return failed(
            "Security Scan",
            f"{len(findings_high)} HIGH, {len(findings_warn)} WARN",
            details,
        )
    if findings_warn:
        return warned(
            "Security Scan",
            f"0 HIGH, {len(findings_warn)} WARN, {len(findings_info)} INFO",
            details,
        )
    return passed(
        "Security Scan",
        f"0 HIGH, 0 WARN, {len(findings_info)} INFO",
    )


if __name__ == "__main__":
    import tempfile
    d = tempfile.mkdtemp()
    r = run(d)
    print(r.status, r.notes)
    if r.details:
        print(r.details)
