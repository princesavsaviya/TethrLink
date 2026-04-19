import os
from datetime import datetime
from typing import List
from tests.lib.result import TestResult

ICON = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "SKIP": "⏭️"}
_UNKNOWN_ICON = "❓"

MANUAL_CHECKLIST = """
## Manual Tests Required Before App Store Submission

- [ ] Real USB tethering test (Samsung tablet + phone)
- [ ] Play Store screenshots, privacy policy page, and store listing form
- [ ] Debian/Ubuntu .deb package install test on clean Ubuntu 22.04 VM
- [ ] Ubuntu 22.04 VM: confirm server launches (GNOME 42 / GStreamer 1.20)
- [ ] Ubuntu 24.04 VM: confirm server launches (GNOME 46 / GStreamer 1.24)
- [ ] Visual frame quality check (human eyes — no artefacts, correct colour)
- [ ] Real-device orientation change test (currently causes stream disconnect)

> Note: Pure X11 sessions are explicitly unsupported.
> TetherLink requires a Wayland session (Mutter ScreenCast API).
"""


def _overall(results: List[TestResult]) -> str:
    if not results:
        return "FAIL"
    return "PASS" if all(r.status in ("PASS", "WARN", "SKIP") for r in results) else "FAIL"


def generate(results: List[TestResult], report_dir: str) -> str:
    os.makedirs(report_dir, exist_ok=True)
    overall = _overall(results)
    icon = ICON[overall]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# TetherLink V1 Pre-Release Test Report",
        f"**Date:** {timestamp}",
        f"**Overall:** {icon} {overall}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Test | Result | Notes |",
        "|---|---|---|",
    ]

    for r in results:
        safe_name = r.name.replace("|", "\\|")
        safe_notes = r.notes.replace("|", "\\|")
        lines.append(f"| {safe_name} | {ICON.get(r.status, _UNKNOWN_ICON)} {r.status} | {safe_notes} |")

    lines += ["", "---", "", "## Details", ""]

    for r in results:
        if r.details:
            safe_details = r.details.strip().replace("```", "\\`\\`\\`")
            lines += [f"### {r.name}", "", "```", safe_details, "```", ""]

    lines.append(MANUAL_CHECKLIST)

    report = "\n".join(lines)
    path = os.path.join(report_dir, "report.md")
    with open(path, "w") as f:
        f.write(report)
    return path


def print_summary(results: List[TestResult]) -> None:
    print("\n" + "=" * 60)
    print("TETHERLINK TEST SUITE RESULTS")
    print("=" * 60)
    for r in results:
        print(f"  {ICON.get(r.status, _UNKNOWN_ICON)} {r.status:4s}  {r.name}")
        if r.notes:
            print(f"         {r.notes}")
    overall = _overall(results)
    print("=" * 60)
    print(f"  Overall: {ICON[overall]} {overall}")
    print("=" * 60 + "\n")
