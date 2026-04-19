from dataclasses import dataclass


@dataclass
class TestResult:
    name: str
    status: str        # "PASS" | "FAIL" | "WARN" | "SKIP"
    notes: str = ""
    details: str = ""  # multi-line extra info shown in Details section


def passed(name: str, notes: str = "") -> TestResult:
    return TestResult(name=name, status="PASS", notes=notes)


def failed(name: str, notes: str, details: str = "") -> TestResult:
    return TestResult(name=name, status="FAIL", notes=notes, details=details)


def warned(name: str, notes: str, details: str = "") -> TestResult:
    return TestResult(name=name, status="WARN", notes=notes, details=details)


def skipped(name: str, notes: str = "") -> TestResult:
    return TestResult(name=name, status="SKIP", notes=notes)
