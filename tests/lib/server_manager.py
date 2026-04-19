import os
import signal
import socket
import subprocess
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SERVER_SCRIPT = os.path.join(REPO_ROOT, "server", "tetherlink_server.py")
VENV_PYTHON = os.path.join(REPO_ROOT, "venv", "bin", "python3")
PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def start(port: int, report_dir: str, wayland_display: str = None) -> subprocess.Popen:
    """Start server in headless mode on given port. Returns process handle."""
    log_path = os.path.join(report_dir, f"server_{port}.log")
    env = os.environ.copy()
    if wayland_display:
        env["WAYLAND_DISPLAY"] = wayland_display

    cmd = [PYTHON, SERVER_SCRIPT, "--headless", "--port", str(port)]
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        env=env,
    )

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _port_open(port):
            return proc
        if proc.poll() is not None:
            log_file.close()
            raise RuntimeError(
                f"Server exited early (code {proc.returncode}). "
                f"Check {log_path}"
            )
        time.sleep(0.3)

    stop(proc)
    log_file.close()
    raise RuntimeError(
        f"Server did not open port {port} within 15s. Check {log_path}"
    )


def stop(proc: subprocess.Popen) -> None:
    """Gracefully stop server; SIGKILL after 3s if needed."""
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
