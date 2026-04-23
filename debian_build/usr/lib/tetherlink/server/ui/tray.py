"""
TetherLink — System Tray Icon
Shows connection status, FPS, connected client IP.
Runs alongside the main server in a separate thread.
"""

import os
import threading
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, GLib, AyatanaAppIndicator3

# ── Tray state (updated by server) ───────────────────────────────────────────

class TrayState:
    def __init__(self):
        self.connected    = False
        self.client_ip    = None
        self.fps          = 0
        self.resolution   = None
        self._callbacks   = []

    def update(self, connected=None, client_ip=None, fps=None, resolution=None):
        if connected  is not None: self.connected  = connected
        if client_ip  is not None: self.client_ip  = client_ip
        if fps        is not None: self.fps        = fps
        if resolution is not None: self.resolution = resolution
        for cb in self._callbacks:
            GLib.idle_add(cb)

    def on_change(self, callback):
        self._callbacks.append(callback)


# ── Tray icon ─────────────────────────────────────────────────────────────────

class TetherLinkTray:

    ICON_ACTIVE   = "network-transmit"
    ICON_IDLE     = "network-offline"
    ICON_CONNECT  = "network-transmit-receive"

    def __init__(self, state: TrayState, on_quit):
        self._state    = state
        self._on_quit  = on_quit
        self._menu     = None
        self._items    = {}

        self._icon_path = os.path.join(
            os.path.dirname(__file__), "assets", "tetherlink.png"
        )
        icon_name = self._icon_path if os.path.exists(self._icon_path) else self.ICON_IDLE

        self._indicator = AyatanaAppIndicator3.Indicator.new(
            "tetherlink",
            icon_name,
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self._indicator.set_title("TetherLink")

        self._build_menu()
        state.on_change(self._refresh)

    def _build_menu(self):
        menu = Gtk.Menu()

        # Title (non-clickable)
        title = Gtk.MenuItem(label="TetherLink")
        title.set_sensitive(False)
        menu.append(title)
        menu.append(Gtk.SeparatorMenuItem())

        # Status line
        status = Gtk.MenuItem(label="⏳ Waiting for tablet...")
        status.set_sensitive(False)
        menu.append(status)
        self._items["status"] = status

        # FPS line
        fps = Gtk.MenuItem(label="")
        fps.set_sensitive(False)
        menu.append(fps)
        self._items["fps"] = fps

        # Resolution line
        res = Gtk.MenuItem(label="")
        res.set_sensitive(False)
        menu.append(res)
        self._items["res"] = res

        menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label="Quit TetherLink")
        quit_item.connect("activate", lambda _: self._on_quit())
        menu.append(quit_item)

        menu.show_all()
        self._indicator.set_menu(menu)
        self._menu = menu

    def _refresh(self):
        s = self._state
        if s.connected and s.client_ip:
            icon = self._icon_path if os.path.exists(self._icon_path) else self.ICON_CONNECT
            self._indicator.set_icon_full(icon, "Connected")
            self._items["status"].set_label(f"🟢 Connected — {s.client_ip}")
            self._items["fps"].set_label(f"   {s.fps} FPS")
            self._items["res"].set_label(
                f"   {s.resolution}" if s.resolution else ""
            )
        else:
            icon = self._icon_path if os.path.exists(self._icon_path) else self.ICON_IDLE
            self._indicator.set_icon_full(icon, "Waiting")
            self._items["status"].set_label("⏳ Waiting for tablet...")
            self._items["fps"].set_label("")
            self._items["res"].set_label(
                f"   Virtual display: {s.resolution}" if s.resolution else ""
            )
        self._menu.show_all()

    def run(self):
        """Block — runs the Gtk main loop. Call from a dedicated thread."""
        Gtk.main()

    def quit(self):
        GLib.idle_add(Gtk.main_quit)


def start_tray(state: TrayState, on_quit) -> TetherLinkTray:
    """Start the tray icon in a background thread. Returns the tray object."""
    tray = TetherLinkTray(state, on_quit)
    thread = threading.Thread(target=tray.run, daemon=True)
    thread.start()
    return tray
