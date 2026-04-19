#!/usr/bin/env python3
"""
Headless TetherLink server entry point for automated tests.
Uses ServerCore — no GUI, responds to SIGTERM for clean shutdown.
"""
import argparse
import logging
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

parser = argparse.ArgumentParser(description="TetherLink headless server")
parser.add_argument("--port", type=int, default=51137)
parser.add_argument("--headless", action="store_true")  # accepted for compat
args = parser.parse_args()

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from server.server_core import ServerCore, ServerConfig, ServerState

config = ServerConfig(port=args.port)
state = ServerState()
core = ServerCore(config, state, on_log=lambda msg: print(msg, flush=True))

loop = GLib.MainLoop()


def _shutdown(*_):
    core.stop()
    loop.quit()


signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

core.start()
loop.run()
