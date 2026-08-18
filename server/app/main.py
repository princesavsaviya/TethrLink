"""
TethrLink — GTK4 Application Entry Point  (V1)
Wires together ServerCore and TethrLinkWindow.
"""

import os
import sys
import logging
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Gio, GLib
from server.core.server_core import (
    ServerCore, ServerConfig, ServerState, detect_codec, CODEC_H264,
)
from server.ui.window import TethrLinkWindow


log = logging.getLogger("TethrLink.App")


def _load_css():
    # Look for style.css in server/ui/
    # If main is in server/app, then server/ui is ../ui/style.css
    base_dir = os.path.dirname(os.path.dirname(__file__))
    css_path = os.path.join(base_dir, "ui", "style.css")
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


class TethrLinkApp(Gtk.Application):

    def __init__(self):
        super().__init__(
            application_id="com.tethrlink.server",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._config     = ServerConfig()
        # Codec override for testing/comparison. ServerConfig now defaults to
        # H.264; set TETHRLINK_CODEC=jpeg to fall back to the old path, or
        # TETHRLINK_CODEC=h264 to be explicit about it.
        _codec_env = os.environ.get("TETHRLINK_CODEC")
        if _codec_env:
            self._config.codec = detect_codec(_codec_env.strip().lower())
            log.info("Codec overridden via TETHRLINK_CODEC=%s", _codec_env)

        # Resolution override for testing, e.g. TETHRLINK_RES=1920x1080.
        # Capture normally matches the connected device exactly, which is the
        # sharpest arrangement but also the most expensive to decode: a
        # 2960x1848 stream at 30 fps asks the tablet for roughly 164 Mpx/s.
        # Forcing a smaller size trades some sharpness for decode headroom,
        # which is worth measuring rather than guessing at.
        _res_env = os.environ.get("TETHRLINK_RES")
        if _res_env:
            try:
                w_str, h_str = _res_env.strip().lower().split("x", 1)
                self._config.width = int(w_str)
                self._config.height = int(h_str)
                log.info("Resolution overridden via TETHRLINK_RES=%dx%d",
                         self._config.width, self._config.height)
            except (ValueError, AttributeError):
                log.warning(
                    "Ignoring malformed TETHRLINK_RES=%r — expected WIDTHxHEIGHT, "
                    "e.g. 1920x1080", _res_env)

        self._state      = ServerState()
        self._core       = None
        self._window     = None
        self._restarting = False


        self._state.on_change(self._on_state_changed)

    def do_activate(self):
        _load_css()

        # Set application icon
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "tethrlink.png")
        if os.path.exists(icon_path):
            try:
                # For GTK4, we can't use set_default_icon_from_file easily for all windows,
                # but we can set it on the window itself or use a Resource.
                # We'll handle it in the window.py builder for now, or use this as a hint.
                pass
            except Exception as e:
                log.warning("App icon load failed: %s", e)

        self._core = ServerCore(
            config=self._config,
            state=self._state,
            on_log=self._on_log,
            on_external_stop=self._on_external_stop,
        )

        self._window = TethrLinkWindow(
            app=self,
            on_start=self._on_start,
            on_stop=self._on_stop,
            on_codec_change=self._on_codec_change,
            initial_codec="h264" if self._config.codec == CODEC_H264 else "jpeg",
        )
        self._window.set_server_running(False)
        self._window.present()

    def _on_app_shutdown(self, *_):
        self._on_quit_app()

    def _on_quit_app(self):
        if self._core:
            self._core.stop()
            self._core = None
        self.quit()


    # ── State callback ────────────────────────────────────────────────────────

    def _on_state_changed(self):
        s = self._state
        if self._window is None:
            return

        if s.running:
            self._restarting = False
            self._window.set_server_running(True)
        elif self._restarting:
            self._start_server()
        else:
            self._window.set_server_running(False)

        self._window.update_status(
            connected=s.connected,
            client_ip=s.client_name,
            fps=s.fps,
            resolution=s.resolution,
            codec_name=s.codec_name,
        )

    def _on_log(self, message: str):
        log.info("[server] %s", message)

    def _on_external_stop(self):
        GLib.idle_add(self._do_stop)

    def _on_codec_change(self, codec_str: str):
        self._config.codec = detect_codec(codec_str)
        log.info("Codec set to %s via UI — applies to the next connection",
                 codec_str)

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
    app = TethrLinkApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
