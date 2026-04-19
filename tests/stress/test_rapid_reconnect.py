import time
from tests.lib.mock_client import MockClient
from tests.lib.result import TestResult, passed, failed

HOST    = "127.0.0.1"
CYCLES  = 10
PAUSE_S = 2.0
MAX_CYCLE_S = 10.0


def run(port: int, report_dir: str) -> TestResult:
    details = []

    for i in range(1, CYCLES + 1):
        t0 = time.monotonic()
        c = MockClient()
        try:
            c.connect(HOST, port, f"RapidTest_{i}", 1920, 1080)
            status, _ = c.read_response()
            if status != "ok":
                return failed(
                    "Rapid Reconnect",
                    f"Cycle {i}: got {status} instead of TLOK",
                    "\n".join(details),
                )

            # Read one frame to confirm stream is live
            c._sock.settimeout(10.0)
            frame = c.read_frame()
            if not frame:
                return failed("Rapid Reconnect", f"Cycle {i}: empty frame")

            c.close()   # abrupt close — no graceful shutdown
        except Exception as e:
            c.close()
            return failed("Rapid Reconnect", f"Cycle {i} exception: {e}", "\n".join(details))

        elapsed = time.monotonic() - t0
        if elapsed > MAX_CYCLE_S:
            return failed(
                "Rapid Reconnect",
                f"Cycle {i} took {elapsed:.1f}s (limit {MAX_CYCLE_S}s) — lock may be stuck",
                "\n".join(details),
            )

        details.append(f"Cycle {i}: OK ({elapsed:.2f}s)")
        time.sleep(PAUSE_S)

    return passed("Rapid Reconnect", f"All {CYCLES} cycles OK")
