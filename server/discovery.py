"""
TetherLink — UDP Discovery Broadcaster
Announces the server on the local network every 2 seconds.
Android app listens for these broadcasts to find the server automatically.

Packet format (JSON):
  {
    "app":        "TetherLink",
    "name":       "Prince's OMEN",
    "port":       51137,
    "resolution": "1920x1080",
    "version":    "0.9.5"
  }
"""

import json
import logging
import platform
import socket
import threading
import time

log = logging.getLogger("TetherLink.Discovery")

# ── Constants ─────────────────────────────────────────────────────────────────
BROADCAST_PORT     = 8765
BROADCAST_INTERVAL = 2.0    # seconds between announcements
BROADCAST_ADDRESS  = "255.255.255.255"
SOCKET_TIMEOUT_S   = 1.0    # receive timeout so stop() is responsive

VERSION = "0.9.5"
# ─────────────────────────────────────────────────────────────────────────────


class DiscoveryBroadcaster:
    """
    Broadcasts server presence via UDP on all network interfaces.
    Runs in a background daemon thread.
    """

    def __init__(self, port: int, width: int, height: int):
        self._port       = port
        self._resolution = f"{width}x{height}"
        self._running    = False
        self._thread     = None
        self._name       = socket.gethostname()

    def _make_packet(self) -> bytes:
        payload = {
            "app":        "TetherLink",
            "name":       self._name,
            "hostname":   self._name,
            "system":     f"Linux {platform.release()} ({platform.machine()})",
            "port":       self._port,
            "resolution": self._resolution,
            "version":    VERSION,
        }
        return json.dumps(payload).encode("utf-8")

    def _broadcast_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(SOCKET_TIMEOUT_S)

        log.info("Broadcasting presence on UDP port %d every %.0fs",
                 BROADCAST_PORT, BROADCAST_INTERVAL)

        while self._running:
            packet = self._make_packet()
            try:
                sock.sendto(packet, (BROADCAST_ADDRESS, BROADCAST_PORT))
            except Exception as e:
                log.debug("Broadcast error: %s", e)
            time.sleep(BROADCAST_INTERVAL)

        sock.close()

    def start(self):
        self._running = True
        self._thread  = threading.Thread(
            target=self._broadcast_loop, daemon=True
        )
        self._thread.start()
        log.info("Discovery broadcaster started — device: %s, port: %d",
                 self._name, self._port)

    def stop(self):
        self._running = False