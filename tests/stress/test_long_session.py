import csv
import os
import time
import psutil
from tests.lib.mock_client import MockClient
from tests.lib.result import TestResult, passed, failed, warned

HOST            = "127.0.0.1"
DURATION_S      = 1800   # 30 minutes
SAMPLE_EVERY_S  = 30
MAX_RSS_GROWTH  = 50     # MB
MAX_AVG_CPU     = 80.0   # percent
MIN_FPS         = 1.0    # frames per second minimum average


def run(port: int, report_dir: str, server_pid: int) -> TestResult:
    csv_path = os.path.join(report_dir, "cpu_rss.csv")
    proc = psutil.Process(server_pid)

    c = MockClient()
    try:
        c.connect(HOST, port, "LongSessionTest", 1920, 1080, timeout=15.0)
        status, _ = c.read_response()
        if status != "ok":
            return failed("Long Session", f"Got {status} instead of TLOK at start")
    except Exception as e:
        c.close()
        return failed("Long Session", f"Connect failed: {e}")

    samples        = []
    baseline_rss   = proc.memory_info().rss / 1024 / 1024
    total_frames   = 0
    start          = time.monotonic()
    last_sample    = start

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["elapsed_s", "rss_mb", "cpu_pct", "total_frames"])

        try:
            c._sock.settimeout(5.0)
            while time.monotonic() - start < DURATION_S:
                try:
                    frame = c.read_frame()
                    if frame:
                        total_frames += 1
                except socket.timeout:
                    pass  # no frame this second — continue

                now = time.monotonic()
                if now - last_sample >= SAMPLE_EVERY_S:
                    elapsed = now - start
                    rss     = proc.memory_info().rss / 1024 / 1024
                    cpu     = proc.cpu_percent(interval=1.0)
                    samples.append((elapsed, rss, cpu))
                    writer.writerow([f"{elapsed:.0f}", f"{rss:.1f}", f"{cpu:.1f}", total_frames])
                    f.flush()
                    last_sample = now
        except Exception as e:
            c.close()
            return failed("Long Session", f"Stream broke after {total_frames} frames: {e}")

    c.close()

    if not samples:
        return failed("Long Session", "No samples collected")

    elapsed_s   = DURATION_S
    avg_fps     = total_frames / elapsed_s
    peak_rss    = max(s[1] for s in samples)
    rss_growth  = peak_rss - baseline_rss
    avg_cpu     = sum(s[2] for s in samples) / len(samples)

    details = (
        f"Duration: {elapsed_s}s | Frames: {total_frames} | Avg FPS: {avg_fps:.1f}\n"
        f"Baseline RSS: {baseline_rss:.1f} MB | Peak RSS: {peak_rss:.1f} MB | Growth: {rss_growth:.1f} MB\n"
        f"Avg CPU: {avg_cpu:.1f}% | Samples: {len(samples)}"
    )

    if rss_growth > MAX_RSS_GROWTH:
        return failed("Long Session", f"Memory leak: RSS grew {rss_growth:.1f} MB (limit {MAX_RSS_GROWTH} MB)", details)
    if avg_cpu > MAX_AVG_CPU:
        return warned("Long Session", f"High CPU: avg {avg_cpu:.1f}% (limit {MAX_AVG_CPU}%)", details)
    if avg_fps < MIN_FPS:
        return failed("Long Session", f"Low FPS: avg {avg_fps:.2f} (min {MIN_FPS})", details)

    return passed("Long Session", details.replace("\n", " | "))


import socket  # noqa: E402 — needed inside run()
