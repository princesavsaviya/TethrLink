"""MockClient — simulates a TetherLink Android client for automated tests."""
import json
import os
import socket
import struct
from typing import Optional, Tuple

MAGIC_HELLO = b"TLHELO"
MAGIC_OK    = b"TLOK__"
MAGIC_BUSY  = b"TLBUSY"
DISCOVERY_PORT = 8765
_MAX_FRAME_BYTES = 50 * 1024 * 1024  # 50 MB sanity cap


class MockClient:
    """Simulates a TetherLink Android client over TCP."""

    def __init__(self):
        self._sock: Optional[socket.socket] = None

    def connect(
        self,
        host: str,
        port: int,
        device_name: str = "MockDevice",
        width: int = 1920,
        height: int = 1080,
        timeout: float = 10.0,
    ) -> None:
        """Connect and send TLHELLO handshake."""
        self.close()  # release any previous socket before reconnecting
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)

        device_id = os.urandom(16)
        name_bytes = device_name[:64].encode("utf-8").ljust(64, b"\x00")
        packet = (
            MAGIC_HELLO
            + device_id
            + struct.pack(">II", width, height)
            + name_bytes
        )
        self._sock.sendall(packet)

    def read_response(self) -> Tuple[str, Optional[Tuple[int, int, int]]]:
        """
        Read server response after handshake.
        Returns ("ok", (w, h, codec)) | ("busy", None) | ("error", None)
        """
        header = self._recvall(6)
        if header == MAGIC_BUSY:
            return "busy", None
        if header == MAGIC_OK:
            rest = self._recvall(9)   # ">IIB" = 4+4+1
            w, h, codec = struct.unpack(">IIB", rest)
            return "ok", (w, h, codec)
        return "error", None

    def read_frame(self) -> bytes:
        """Read one size-prefixed JPEG frame."""
        size_bytes = self._recvall(4)
        size = struct.unpack(">I", size_bytes)[0]
        if size > _MAX_FRAME_BYTES:
            raise ValueError(f"Frame size {size} exceeds {_MAX_FRAME_BYTES} byte limit")
        return self._recvall(size)

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _recvall(self, n: int) -> bytes:
        if self._sock is None:
            raise ConnectionError("Not connected — call connect() first")
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError(f"Connection closed after {len(buf)}/{n} bytes")
            buf += chunk
        return buf


def listen_udp(port: int = DISCOVERY_PORT, timeout: float = 10.0) -> Optional[dict]:
    """Listen for one UDP broadcast packet. Returns parsed JSON dict or None on timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)
    try:
        try:
            sock.bind(("", port))
        except OSError as e:
            raise RuntimeError(f"Cannot bind UDP port {port}: {e}") from e
        data, _ = sock.recvfrom(4096)
        return json.loads(data.decode("utf-8"))
    except (socket.timeout, json.JSONDecodeError):
        return None
    finally:
        sock.close()
