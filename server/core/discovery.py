"""
TethrLink — UDP Discovery Broadcaster
Announces the server on the local network every 2 seconds.
Android app listens for these broadcasts to find the server automatically.

Packet format (JSON):
  {
    "app":        "TethrLink",
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

from server.core.link import usb_tether_source_addresses

log = logging.getLogger("TethrLink.Discovery")

# ── Constants ─────────────────────────────────────────────────────────────────
BROADCAST_PORT      = 8765
BROADCAST_INTERVAL  = 2.0    # seconds between announcements
SOCKET_TIMEOUT_S    = 1.0    # receive timeout so stop() is responsive

# The *limited* broadcast. Android's UDP sockets receive this far more
# reliably than a directed subnet broadcast (e.g. 10.125.32.255) — a
# directed broadcast is what silently broke discovery whenever the app was
# already open before the server started. Sending 255.255.255.255
# unscoped would leak the announcement onto Wi-Fi too, so it is only ever
# sent from a socket bound to a USB tether interface's own address (see
# usb_tether_source_addresses()) — the bind, not the destination, is what
# keeps this off Wi-Fi.
TETHER_BROADCAST_ADDRESS = "255.255.255.255"

# Loopback broadcast so local diagnostic tooling on this same machine keeps
# discovering the server, unchanged from before this fix.
LOOPBACK_BROADCAST_ADDRESS = "127.255.255.255"

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
            "app":        "TethrLink",
            "name":       self._name,
            "hostname":   self._name,
            "system":     f"Linux {platform.release()} ({platform.machine()})",
            "port":       self._port,
            "resolution": self._resolution,
            "version":    VERSION,
        }
        return json.dumps(payload).encode("utf-8")

    def _send_tether_broadcasts(self, packet: bytes) -> None:
        """Send the limited broadcast out each USB-tethered interface.

        A fresh socket per source address, bound to that address before
        sending: binding is what scopes the packet to this interface (so it
        never reaches Wi-Fi), and it must happen right before use because
        the tether interface is ephemeral — the address resolved a cycle
        ago may already be stale. A bind() on a since-vanished address
        fails with OSError (e.g. `[Errno 99] Cannot assign requested
        address`); that is expected and routine, not a reason to stop
        broadcasting to whichever other interfaces are still live, so it is
        caught and logged per-address rather than allowed to escape and
        kill the loop.
        """
        for source_addr in usb_tether_source_addresses():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((source_addr, 0))
                sock.sendto(packet, (TETHER_BROADCAST_ADDRESS, BROADCAST_PORT))
            except OSError as e:
                log.debug("Tether broadcast from %s failed — skipping "
                          "this cycle: %s", source_addr, e)
            finally:
                sock.close()

    def _send_loopback_broadcast(self, packet: bytes) -> None:
        """Unchanged from before this fix: local diagnostic tooling on this
        same machine keeps discovering the server."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (LOOPBACK_BROADCAST_ADDRESS, BROADCAST_PORT))
        except OSError as e:
            log.debug("Loopback broadcast failed: %s", e)
        finally:
            sock.close()

    def _broadcast_loop(self):
        log.info("Broadcasting presence on UDP port %d every %.0fs",
                 BROADCAST_PORT, BROADCAST_INTERVAL)

        while self._running:
            packet = self._make_packet()
            self._send_tether_broadcasts(packet)
            self._send_loopback_broadcast(packet)
            time.sleep(BROADCAST_INTERVAL)

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