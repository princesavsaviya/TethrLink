"""
TetherLink — GTK4 Application Entry Point  (V1)
Wires together ServerCore and TetherLinkWindow.
"""

import os
import sys
import logging
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio, GLib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.server_core import ServerCore, ServerConfig, ServerState
from server.ui.window import TetherLinkWindow

log = logging.getLogger("TetherLink.App")


def _load_css():
    css_path = os.path.join(os.path.dirname(__file__), "ui", "style.css")
    provider = Gtk.CssProvider()
    try:
        provider.load_from_path(css_path)
    except Exception as e:
        log.warning("CSS load failed: %s", e)
        return
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display:
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


class TetherLinkApp(Gtk.Application):

    def __init__(self):
        super().__init__(
            application_id="com.tetherlink.server",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._config     = ServerConfig()
        self._state      = ServerState()
        self._core       = None
        self._window     = None
        self._restarting = False

        self._state.on_change(self._on_state_changed)

    def do_activate(self):
        _load_css()

        self._core = ServerCore(
            config=self._config,
            state=self._state,
            on_log=self._on_log,
            on_external_stop=self._on_external_stop,
        )

        self._window = TetherLinkWindow(
            app=self,
            on_start=self._on_start,
            on_stop=self._on_stop,
        )
        self._window.set_server_running(False)
        self._window.present()

    # ── State callback ────────────────────────────────────────────────────────

    def _on_state_changed(self):
        s = self._state
        if self._window is None:
            return

        if s.running:
            self._restarting = False
            self._window.set_server_running(True)
        elif self._restarting:
            self._window.set_server_running(False, restarting=True)
        else:
            self._window.set_server_running(False)

        self._window.update_status(
            connected=s.connected,
            client_ip=s.client_name,
            fps=s.fps,
            resolution=s.resolution,
        )

    def _on_log(self, message: str):
        log.info("[server] %s", message)

    def _on_external_stop(self):
        GLib.idle_add(self._do_stop)

    # ── Start / Stop ──────────────────────────────────────────────────────────

    def _on_start(self):
        if self._core and not self._core.running:
            self._window.set_server_running(True)
            threading.Thread(target=self._core.start, daemon=True).start()

    def _on_stop(self):
        self._restarting = False
        self._do_stop()

    def _do_stop(self):
        if self._core and self._core.running:
            threading.Thread(target=self._core.stop, daemon=True).start()
        if self._window:
            self._window.set_server_running(False)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(name)-28s  %(levelname)s  %(message)s",
    )
    app = TetherLinkApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
