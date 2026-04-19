import ast
import importlib.util
import os
import re
import sys
from tests.lib.result import TestResult, passed, failed, warned

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Map top-level import names → PyPI package names (where they differ)
IMPORT_TO_PYPI = {
    "gi":       "PyGObject",
    "dbus":     "dbus-python",
    "gst":      "gstreamer (system)",
    "psutil":   "psutil",
    "pystray":  "pystray",
    "PIL":      "Pillow",
}

# stdlib modules to exclude from the check
STDLIB = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()


def _imports_in_file(path: str) -> set:
    names = set()
    try:
        with open(path) as f:
            tree = ast.parse(f.read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.add(node.module.split(".")[0])
    except SyntaxError:
        pass
    return names


def run(report_dir: str) -> TestResult:
    req_path = os.path.join(REPO, "server", "requirements.txt")
    if not os.path.exists(req_path):
        return failed("Requirements Check", "server/requirements.txt not found")

    with open(req_path) as f:
        req_lines = [l.strip().lower() for l in f if l.strip() and not l.startswith("#")]
    req_packages = {re.split(r"[>=<!]", l)[0].strip() for l in req_lines}

    # Collect all imports from server/*.py
    server_dir = os.path.join(REPO, "server")
    all_imports = set()
    for fname in os.listdir(server_dir):
        if fname.endswith(".py"):
            all_imports |= _imports_in_file(os.path.join(server_dir, fname))

    missing = []
    for imp in sorted(all_imports):
        if imp in STDLIB:
            continue
        if imp.startswith("_"):
            continue
        pypi_name = IMPORT_TO_PYPI.get(imp, imp).lower()
        if pypi_name not in req_packages and imp.lower() not in req_packages:
            missing.append(f"  import '{imp}' → PyPI '{pypi_name}' not in requirements.txt")

    if missing:
        return warned(
            "Requirements Check",
            f"{len(missing)} imports may be missing from requirements.txt",
            "\n".join(missing),
        )
    return passed("Requirements Check", "All non-stdlib imports accounted for")
