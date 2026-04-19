import socket
import time
from tests.lib.mock_client import MockClient, MAGIC_BUSY
from tests.lib.result import TestResult, passed, failed

HOST = "127.0.0.1"


def run(port: int, report_dir: str) -> TestResult:
    details = []
    time.sleep(0.5)   # ensure any previous test's lock is released

    # Client A: connect and hold
    a = MockClient()
    try:
        a.connect(HOST, port, "ClientA", 1920, 1080)
        status_a, _ = a.read_response()
        if status_a != "ok":
            a.close()
            return failed("TLBUSY", f"Client A got {status_a} instead of TLOK")
        details.append("Client A: TLOK received")
    except Exception as e:
        a.close()
        return failed("TLBUSY", f"Client A exception: {e}")

    # Client B: should get TLBUSY
    b = MockClient()
    try:
        b.connect(HOST, port, "ClientB", 1920, 1080, timeout=5.0)
        status_b, _ = b.read_response()
        if status_b != "busy":
            a.close()
            b.close()
            return failed("TLBUSY", f"Client B got {status_b} instead of TLBUSY")
        details.append("Client B: TLBUSY received correctly")
        b.close()
    except Exception as e:
        a.close()
        b.close()
        return failed("TLBUSY", f"Client B exception: {e}")

    # Disconnect A — lock must be released
    a.close()
    time.sleep(1.5)

    # Client C: should now get TLOK
    c = MockClient()
    try:
        c.connect(HOST, port, "ClientC", 1920, 1080)
        status_c, _ = c.read_response()
        if status_c != "ok":
            c.close()
            return failed("TLBUSY", f"Client C got {status_c} after A disconnected — lock not released")
        details.append("Client C: TLOK after A disconnected (lock released correctly)")
        c.close()
    except Exception as e:
        c.close()
        return failed("TLBUSY", f"Client C exception: {e}")

    return passed("TLBUSY", " | ".join(details))
