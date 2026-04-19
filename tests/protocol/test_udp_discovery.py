import socket
import time
from tests.lib.mock_client import listen_udp
from tests.lib.result import TestResult, passed, failed, warned

REQUIRED_FIELDS = {"app", "port"}        # minimum required
EXPECTED_FIELDS = {"name", "hostname", "resolution", "version"}   # nice-to-have


def run(report_dir: str) -> TestResult:
    packet = listen_udp(timeout=12.0)

    if packet is None:
        return failed(
            "UDP Discovery",
            "No UDP broadcast received within 12s — check server is running",
        )

    missing_required = REQUIRED_FIELDS - packet.keys()
    if missing_required:
        return failed(
            "UDP Discovery",
            f"Missing required fields: {missing_required}",
            f"Received: {packet}",
        )

    if packet.get("app") != "TetherLink":
        return failed(
            "UDP Discovery",
            f"app field is '{packet.get('app')}', expected 'TetherLink'",
        )

    # Verify the announced port is actually open
    port = packet.get("port")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            pass
    except OSError:
        return failed(
            "UDP Discovery",
            f"Announced port {port} is not reachable",
        )

    missing_nice = EXPECTED_FIELDS - packet.keys()
    if missing_nice:
        return warned(
            "UDP Discovery",
            f"Missing optional fields: {missing_nice}",
            f"Received: {packet}",
        )

    return passed("UDP Discovery", f"Broadcast OK — port={port}, app=TetherLink")
