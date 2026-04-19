"""
TetherLink — Main GTK4 Window  (V1)
Single scrollable page, two states:

  Stopped:  logo + welcome + Start Server button
  Running:  status bar + "Open Display Settings" link + Stop Server button
"""

import subprocess

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango


# ── Helper builders ───────────────────────────────────────────────────────────

def _label(text: str, css: str = "", halign=Gtk.Align.START,
           wrap: bool = False) -> Gtk.Label:
    lbl = Gtk.Label(label=text)
    lbl.set_halign(halign)
    if css:
        for cls in css.split():
            lbl.add_css_class(cls)
    if wrap:
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    return lbl


# ── Window ────────────────────────────────────────────────────────────────────

class TetherLinkWindow(Gtk.ApplicationWindow):

    def __init__(self, app, on_start, on_stop):
        super().__init__(application=app)

        self._on_start = on_start
        self._on_stop  = on_stop
        self._updating = False

        self.set_title("TetherLink")
        self.set_default_size(540, 480)
        self.set_resizable(False)

        self._build()

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add_css_class("main-scroll")
        self.set_child(scroll)

        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.set_child(self._root)

        self._build_stopped_state()
        self._build_status_bar()
        self._build_running_state()

        self._show_stopped()

    # ── Stopped state ─────────────────────────────────────────────────────────

    def _build_stopped_state(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.add_css_class("welcome-box")

        logo = _label("TetherLink", "app-logo", Gtk.Align.CENTER)
        sub  = _label("DISPLAY EXTENDER", "app-logo-sub", Gtk.Align.CENTER)

        title = _label("Welcome to TetherLink", "welcome-title", Gtk.Align.CENTER)
        hint  = _label(
            "Connect your Android tablet via USB tethering, then start\n"
            "the server. Your tablet becomes a second display instantly.",
            "welcome-subtitle", Gtk.Align.CENTER, wrap=True,
        )
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_max_width_chars(48)

        self._btn_start = Gtk.Button(label="  Start Server  ")
        self._btn_start.add_css_class("btn-start")
        self._btn_start.set_halign(Gtk.Align.CENTER)
        self._btn_start.connect("clicked", lambda _: self._on_start())

        for w in (logo, sub, title, hint, self._btn_start):
            box.append(w)

        self._stopped_box = box
        self._root.append(box)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_status_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        bar.add_css_class("status-bar")

        self._status_dot  = _label("●", "status-dot idle")
        self._status_text = _label("Waiting for tablet…", "status-text")
        self._status_fps  = _label("", "status-fps")
        self._status_fps.set_hexpand(True)
        self._status_fps.set_halign(Gtk.Align.END)

        bar.append(self._status_dot)
        bar.append(self._status_text)
        bar.append(self._status_fps)

        self._status_bar = bar
        self._root.append(bar)

    # ── Running state ─────────────────────────────────────────────────────────

    def _build_running_state(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_vexpand(True)
        box.set_valign(Gtk.Align.CENTER)

        # ── Display settings card ─────────────────────────────────────────────
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("section-card")
        card.set_margin_top(28)
        card.set_margin_start(28)
        card.set_margin_end(28)

        icon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        icon_row.set_valign(Gtk.Align.CENTER)

        icon = _label("🖥", "", Gtk.Align.START)
        icon.set_css_classes([])

        desc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        desc_box.set_hexpand(True)
        desc_box.append(_label("Display Arrangement", "setting-name"))
        desc_box.append(_label(
            "Position, resolution, and orientation are\n"
            "managed through Ubuntu's Display Settings.",
            "setting-hint", wrap=True,
        ))

        btn_settings = Gtk.Button(label="Open Display Settings ↗")
        btn_settings.add_css_class("btn-apply")
        btn_settings.set_valign(Gtk.Align.CENTER)
        btn_settings.connect("clicked", self._open_display_settings)

        icon_row.append(icon)
        icon_row.append(desc_box)
        icon_row.append(btn_settings)
        card.append(icon_row)
        box.append(card)

        # ── Resolution info ───────────────────────────────────────────────────
        self._res_label = _label("", "setting-hint", Gtk.Align.CENTER)
        self._res_label.set_margin_top(12)
        box.append(self._res_label)

        # ── Stop button ───────────────────────────────────────────────────────
        stop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        stop_box.set_margin_top(32)
        stop_box.set_margin_bottom(36)

        btn_stop = Gtk.Button(label="Stop Server")
        btn_stop.add_css_class("btn-stop")
        btn_stop.set_halign(Gtk.Align.CENTER)
        btn_stop.connect("clicked", lambda _: self._on_stop())
        stop_box.append(btn_stop)
        box.append(stop_box)

        self._running_box = box
        self._root.append(box)

    # ── State switches ────────────────────────────────────────────────────────

    def _show_stopped(self):
        self._stopped_box.set_visible(True)
        self._status_bar.set_visible(False)
        self._running_box.set_visible(False)

    def _show_running(self):
        self._stopped_box.set_visible(False)
        self._status_bar.set_visible(True)
        self._running_box.set_visible(True)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_server_running(self, running: bool, restarting: bool = False):
        if running:
            self._show_running()
        elif restarting:
            self._status_dot.remove_css_class("connected")
            self._status_dot.add_css_class("idle")
            self._status_text.set_label("Restarting…")
            self._status_fps.set_label("")
        else:
            self._show_stopped()

    def update_status(self, connected: bool, client_ip: str = "",
                      fps: int = 0, resolution: str = ""):
        if connected:
            self._status_dot.remove_css_class("idle")
            self._status_dot.add_css_class("connected")
            self._status_text.set_label(
                f"Connected — {client_ip}" if client_ip else "Connected"
            )
            self._status_fps.set_label(f"{fps} FPS" if fps else "")
            if resolution:
                self._res_label.set_label(f"Streaming at {resolution}")
        else:
            self._status_dot.remove_css_class("connected")
            self._status_dot.add_css_class("idle")
            self._status_text.set_label("Waiting for tablet…")
            self._status_fps.set_label("")
            self._res_label.set_label("")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _open_display_settings(self, _btn):
        try:
            subprocess.Popen(["gnome-control-center", "display"],
                             start_new_session=True)
        except FileNotFoundError:
            # Fallback for non-GNOME desktops
            try:
                subprocess.Popen(["xfce4-display-settings"],
                                 start_new_session=True)
            except FileNotFoundError:
                pass
