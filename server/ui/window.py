"""
TethrLink — Main GTK4 Window  (V1)
Single scrollable page, two states:

  Stopped:  logo + welcome + Start Server button
  Running:  status bar + "Open Display Settings" link + Stop Server button

A single Settings card (Video Codec + Touch Input) sits between the two
and stays visible in both states. Both settings are locked while a client
is connected: the pipeline (and, for touch, the RemoteDesktop pairing) is
built once per connection, so a change mid-stream would do nothing.
"""

import os
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

class TethrLinkWindow(Gtk.ApplicationWindow):

    # Dropdown row order — index must match the strings passed to
    # Gtk.DropDown.new_from_strings() in _build_codec_row().
    _CODEC_VALUES = ("h264", "jpeg")

    def __init__(self, app, on_start, on_stop, on_codec_change, initial_codec="h264",
                 on_touch_change=None, initial_touch_enabled=False):
        super().__init__(application=app)

        self._on_start = on_start
        self._on_stop  = on_stop
        self._on_codec_change = on_codec_change
        self._initial_codec = initial_codec if initial_codec in self._CODEC_VALUES else "h264"
        self._updating = False
        # on_touch_change may legitimately be None (e.g. a caller that only
        # wants the window shell); guarded at the call site in
        # _on_touch_toggled below rather than defaulting to a no-op here.
        self._on_touch_change = on_touch_change
        self._initial_touch_enabled = bool(initial_touch_enabled)
        self._updating_touch = False

        self.set_title("TethrLink")
        self.set_default_size(540, 480)
        self.set_resizable(False)

        # Set window icon
        icon_path = os.path.join(
            os.path.dirname(__file__), "assets", "tethrlink.png"
        )
        if os.path.exists(icon_path):
            # GTK4 uses GIcon or Paintable for window icons
            from gi.repository import Gdk, Gio
            file = Gio.File.new_for_path(icon_path)
            paintable = Gtk.FileLauncher.new(file) # Wait, GTK4 way is different
            # Actually, Gtk4 uses set_icon_name or CSS. 
            # Simplified: set the image in the UI.
            pass

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
        self._build_settings_card()
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

        icon_path = os.path.join(
            os.path.dirname(__file__), "assets", "tethrlink.png"
        )
        if os.path.exists(icon_path):
            logo_img = Gtk.Image.new_from_file(icon_path)
            logo_img.set_pixel_size(128)
            logo_img.add_css_class("app-logo-img")
        else:
            logo_img = _label("TethrLink", "app-logo", Gtk.Align.CENTER)

        sub = _label("DISPLAY EXTENDER", "app-logo-sub", Gtk.Align.CENTER)

        title = _label("Welcome to TethrLink", "welcome-title", Gtk.Align.CENTER)
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

        for w in (logo_img, sub, title, hint, self._btn_start):
            box.append(w)

        self._stopped_box = box
        self._root.append(box)

    # ── Settings card (codec + touch) ────────────────────────────────────────
    # Both settings are "pick before you connect" choices, locked while a
    # client is connected — the pipeline (and, for touch, the RemoteDesktop
    # pairing) is built once per connection, so a change mid-stream would do
    # nothing. They share one card and one heading instead of each getting
    # its own card/icon/hint block, since that's the same kind of thing
    # repeated twice. Display Arrangement below stays a separate card: it's
    # guidance, not a setting.

    def _build_settings_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("section-card")
        card.set_margin_top(20)
        card.set_margin_start(28)
        card.set_margin_end(28)

        card.append(_label("Settings", "settings-title"))
        card.append(self._build_codec_row())

        divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        divider.add_css_class("settings-divider")
        card.append(divider)

        card.append(self._build_touch_row())

        self._settings_card = card
        self._root.append(card)

    def _build_codec_row(self):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.set_valign(Gtk.Align.CENTER)

        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_box.set_hexpand(True)
        name_box.append(_label("Video Codec", "setting-name"))
        name_box.append(_label(
            "H.264 is sharper and lighter on bandwidth. Applies next connection.",
            "setting-hint", wrap=True,
        ))

        # Row order must match _CODEC_VALUES above.
        self._codec_dropdown = Gtk.DropDown.new_from_strings(["H.264", "JPEG"])
        self._codec_dropdown.add_css_class("codec-dropdown")
        self._codec_dropdown.set_valign(Gtk.Align.CENTER)

        self._updating = True
        self._codec_dropdown.set_selected(self._CODEC_VALUES.index(self._initial_codec))
        self._updating = False
        self._codec_dropdown.connect("notify::selected", self._on_codec_selected)

        top.append(name_box)
        top.append(self._codec_dropdown)
        row.append(top)

        # What is actually in use for the current session — set only while
        # connected. On the X11 capture path the stream is always JPEG
        # regardless of what's configured, so this can legitimately disagree
        # with the dropdown above.
        self._active_codec_label = _label("", "setting-hint")
        row.append(self._active_codec_label)

        return row

    def _build_touch_row(self):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.set_valign(Gtk.Align.CENTER)

        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_box.set_hexpand(True)
        name_box.append(_label("Touch Input", "setting-name"))
        name_box.append(_label(
            "Lets the tablet drive this PC's pointer, off by default. "
            "Applies next connection.",
            "setting-hint", wrap=True,
        ))

        self._touch_switch = Gtk.Switch()
        self._touch_switch.add_css_class("touch-switch")
        self._touch_switch.set_valign(Gtk.Align.CENTER)

        self._updating_touch = True
        self._touch_switch.set_active(self._initial_touch_enabled)
        self._updating_touch = False
        self._touch_switch.connect("notify::active", self._on_touch_toggled)

        top.append(name_box)
        top.append(self._touch_switch)
        row.append(top)

        # What is actually active for the current session — set only while
        # connected, the same way _active_codec_label reports the resolved
        # codec rather than the requested one: negotiation can want input
        # and still not get it (the client didn't advertise support, or
        # RemoteDesktop pairing failed and the session fell back to
        # video-only), so this reflects that outcome, not the toggle.
        self._active_touch_label = _label("", "setting-hint")
        row.append(self._active_touch_label)

        return row

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

        warn = _label(
            "⚠  Avoid changing orientation — this will disconnect the stream.",
            "setting-hint", Gtk.Align.CENTER,
        )
        warn.set_margin_top(6)
        card.append(warn)
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
                      fps: int = 0, resolution: str = "", codec_name: str = "",
                      input_active: bool = False):
        # The pipeline is built once per connection, so a codec change made
        # mid-stream would silently do nothing. Lock the control while
        # connected rather than accept an edit that has no effect. Touch
        # input is the same story: RemoteDesktop pairing happens once, at
        # connection time, so the switch is locked for the same reason.
        self._codec_dropdown.set_sensitive(not connected)
        self._touch_switch.set_sensitive(not connected)
        if connected:
            self._status_dot.remove_css_class("idle")
            self._status_dot.add_css_class("connected")
            self._status_text.set_label(
                f"Connected — {client_ip}" if client_ip else "Connected"
            )
            self._status_fps.set_label(f"{fps} FPS" if fps else "")
            if resolution:
                self._res_label.set_label(f"Streaming at {resolution}")
            self._active_codec_label.set_label(
                f"Active this session: {codec_name}" if codec_name else ""
            )
            self._active_touch_label.set_label(
                "Touch input active this session" if input_active
                else "Touch input inactive this session"
            )
        else:
            self._status_dot.remove_css_class("connected")
            self._status_dot.add_css_class("idle")
            self._status_text.set_label("Waiting for tablet…")
            self._status_fps.set_label("")
            self._res_label.set_label("")
            self._active_codec_label.set_label("")
            self._active_touch_label.set_label("")

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_codec_selected(self, dropdown, _pspec):
        if self._updating:
            return
        idx = dropdown.get_selected()
        if 0 <= idx < len(self._CODEC_VALUES):
            self._on_codec_change(self._CODEC_VALUES[idx])

    def _on_touch_toggled(self, switch, _pspec):
        if self._updating_touch:
            return
        if self._on_touch_change:
            self._on_touch_change(switch.get_active())

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
