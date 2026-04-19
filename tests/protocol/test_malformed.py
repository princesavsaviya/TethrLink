import os
import socket
import struct
import time
from tests.lib.mock_client import MockClient, MAGIC_HELLO
from tests.lib.result import TestResult, passed, failed

HOST = "127.0.0.1"

# Valid TLHELLO helper
def _valid_hello(name="ValidClient", w=1920, h=1080):
    device_id  = os.urandom(16)
    name_bytes = name.encode()[:64].ljust(64, b"\x00")
    return MAGIC_HELLO + device_id + struct.pack(">II", w, h) + name_bytes


PAYLOADS = [
    ("Empty payload",       b""),
    ("Random 100 bytes",    os.urandom(100)),
    ("Magic + truncated",   MAGIC_HELLO + b"\x00" * 4),
    ("Magic + valid header + 10000 nulls",
     _valid_hello() + b"\x00" * 10000),
    ("Valid TLHELLO width=0 height=0 (expect TLOK)",
     MAGIC_HELLO + os.urandom(16) + struct.pack(">II", 0, 0) + b"ZeroSize".ljust(64, b"\x00")),
]


def _send_and_drain(port: int, payload: bytes) -> None:
    s = socket.create_connection((HOST, port), timeout=5)
    s.settimeout(5)
    if payload:
        s.sendall(payload)
    try:
        s.recv(256)   # drain whatever server sends back
    except OSError:
        pass
    s.close()


def _valid_connect_ok(port: int) -> bool:
    """Server is alive if it responds with either TLOK (free) or TLBUSY (occupied)."""
    c = MockClient()
    try:
        c.connect(HOST, port, "SurvivalCheck", 1920, 1080)
        status, _ = c.read_response()
        c.close()
        return status in ("ok", "busy")
    except Exception:
        c.close()
        return False


def run(port: int, report_dir: str) -> TestResult:
    details = []
    for label, payload in PAYLOADS:
        try:
            _send_and_drain(port, payload)
        except Exception:
            pass   # server closed connection — expected
        time.sleep(0.5)

        # Server must still accept a valid connection after each bad payload
        alive = _valid_connect_ok(port)
        status = "OK" if alive else "DEAD"
        details.append(f"  [{status}] After: {label}")

        if not alive:
            return failed(
                "Malformed Input",
                f"Server stopped accepting connections after: {label}",
                "\n".join(details),
            )

    return passed("Malformed Input", f"Server survived all {len(PAYLOADS)} malformed payloads")
