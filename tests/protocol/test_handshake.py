import socket
import time
from tests.lib.mock_client import MockClient
from tests.lib.result import TestResult, passed, failed

HOST = "127.0.0.1"


def run(port: int, report_dir: str) -> TestResult:
    details = []

    # Connect and get TLOK
    c = MockClient()
    try:
        c.connect(HOST, port, "HandshakeTest", 1920, 1080)
        status, info = c.read_response()
        if status != "ok":
            return failed("Handshake", f"Expected TLOK, got: {status}")
        details.append(f"TLOK received: w={info[0]} h={info[1]} codec={info[2]}")

        # Frame arrives within 10s
        c._sock.settimeout(10.0)
        frame = c.read_frame()
        if not frame:
            return failed("Handshake", "Empty frame received")
        if frame[:2] != b"\xff\xd8":
            return failed("Handshake", f"Frame is not JPEG (magic={frame[:2].hex()})")
        details.append(f"First frame received: {len(frame)} bytes, valid JPEG")

        c.close()
    except Exception as e:
        c.close()
        return failed("Handshake", f"Exception: {e}")

    # Reconnect immediately (lock must be released)
    time.sleep(0.5)
    c2 = MockClient()
    try:
        c2.connect(HOST, port, "HandshakeTest2", 1920, 1080)
        status2, _ = c2.read_response()
        if status2 != "ok":
            return failed("Handshake", f"Reconnect got {status2} instead of TLOK — lock not released")
        details.append("Reconnect after close: TLOK received (lock released correctly)")
        c2.close()
    except Exception as e:
        c2.close()
        return failed("Handshake", f"Reconnect exception: {e}")

    return passed("Handshake", "\n".join(details))
