"""
TethrLink — Main GTK4 Window (V2 — Libadwaita)

Three states, not two: Idle (nothing running), Waiting (server up, no
tablet yet) and Streaming (a tablet is connected). Waiting and Streaming
used to be one "running" screen; splitting them matters because the two
have almost nothing in common — before a device connects, the only thing
that matters is *getting connected*, and once it has, the user mostly
ignores the window and just wants an at-a-glance answer to "is it still
working?". Settings (codec + touch) appear on all three states, since
touch applies from the moment a client connects — a user who could only
reach the toggle *after* starting would have to connect, flip it, then
reconnect just to use it the first time.

Built on Adw (libadwaita) rather than hand-rolled Gtk.Box chains: `Adw` is
already a declared .deb/.snap dependency, and native rows/status
pages/toasts/banners mean the app looks like part of the desktop instead
of reimplementing what AdwPreferencesGroup, AdwActionRow and AdwBanner
already do.

This is a production port of the approved standalone prototype,
tools/ui_prototype.py — see that file's module docstring for the full
design rationale. The differences from the prototype are implementation,
not design: the prototype throws away and rebuilds its entire page on
every state change, which is fine for a demo driven by keypresses but
would rebuild (and lose the interaction/focus state of) every settings
row on every per-second FPS tick here. Production instead builds each
page's widgets once and updates them in place — the same pattern the V1
window already used for its two states, just extended to three.

server/ui/style.css is still the stylesheet (loaded once, in
server/app/main.py's _load_css()) — its palette already matches this
design (unified with the Android app's), so only the additional
selectors this redesign's Adwaita widgets need (rows, banners, hero/stat
text, pill buttons) were added to it; nothing existing was recolored.
"""

import logging
import os
import subprocess

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from server.core.link import usb_tether_source_addresses

log = logging.getLogger("TethrLink")

# Adwaita widgets work without Adw.Application, but only after an explicit
# Adw.init() call — TethrLinkApp (server/app/main.py) is a plain
# Gtk.Application, not Adw.Application, so this module does its own
# libadwaita bring-up rather than assuming the app class already did.
# Safe to call more than once (e.g. if this module is imported twice).
Adw.init()


def _label(text: str = "", css: str = "", halign=Gtk.Align.START,
           justify=None) -> Gtk.Label:
    lbl = Gtk.Label(label=text)
    lbl.set_halign(halign)
    if justify is not None:
        lbl.set_justify(justify)
    for cls in css.split():
        lbl.add_css_class(cls)
    return lbl


class TethrLinkWindow(Adw.ApplicationWindow):

    # Dropdown row order — index must match _CODEC_DISPLAY and the
    # strings handed to Gtk.StringList.new() in _build_settings_group().
    _CODEC_VALUES = ("h264", "jpeg")
    _CODEC_DISPLAY = ("H.264", "JPEG")

    def __init__(self, app, on_start, on_stop, on_codec_change, initial_codec="h264",
                 on_touch_change=None, initial_touch_enabled=False):
        super().__init__(application=app, title="TethrLink")
        self.set_default_size(440, 640)

        self._on_start = on_start
        self._on_stop = on_stop
        self._on_codec_change = on_codec_change
        self._initial_codec = initial_codec if initial_codec in self._CODEC_VALUES else "h264"
        # on_touch_change may legitimately be None (e.g. a caller that only
        # wants the window shell); guarded at the call site in
        # _on_touch_toggled below rather than defaulting to a no-op here.
        self._on_touch_change = on_touch_change
        self._initial_touch_enabled = bool(initial_touch_enabled)

        # Guards against the programmatic set_selected()/set_active() calls
        # in _refresh_settings() re-entering the user-edit handlers below —
        # there are up to three row instances (one per page) kept in sync,
        # so without this a single real user edit would fan out into
        # spurious extra on_codec_change/on_touch_change calls.
        self._updating_codec = False
        self._updating_touch = False

        # What the user has asked for (persists across state changes; only
        # ever changed by the user via the rows below).
        self._want_codec = self._initial_codec
        self._want_touch = self._initial_touch_enabled

        # Current session snapshot, refreshed only by update_status()/
        # set_server_running() — the settings rows read what is actually in
        # EFFECT from here, never from their own widget state, and the
        # streaming page's health/stat display reads real data only:
        # nothing here is a guess or a leftover from a previous session.
        self._running = False
        self._connected = False
        self._client_ip = ""     # actually the device name (e.g. "SM-X920")
                                  # — see update_status()'s docstring.
        self._fps = 0
        self._resolution = ""
        self._codec_name = ""    # effective codec this session ("H.264"/"JPEG"), "" if none
        self._input_active = False
        self._dropped = 0
        self._overflows = 0
        self._port = 0

        # One entry per page that embeds a settings group (idle, waiting,
        # streaming all show one — see module docstring) — kept as three
        # separate row instances rather than one row re-parented between
        # pages, since production keeps all three pages alive
        # simultaneously (only one set_visible(True) at a time) rather than
        # rebuilding the tree on every state change.
        self._codec_rows = []
        self._touch_rows = []

        # Dark forced: the product's stylesheet is a dark theme, and
        # letting the session pick light would render this window
        # unreadable against it.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon_path = os.path.join(os.path.dirname(__file__), "assets", "tethrlink.png")
        if os.path.exists(icon_path):
            mark = Gtk.Image.new_from_file(icon_path)
            mark.set_pixel_size(20)
            title_box.append(mark)
        title_box.append(_label("TethrLink", "app-title"))
        header.set_title_widget(title_box)
        toolbar.add_top_bar(header)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Maximised, the window is as wide as the display, and settings rows
        # stretched across it read as a spreadsheet rather than a panel. Clamp
        # keeps the content column at a readable width and centres it, which is
        # what every GNOME app does with preference rows.
        clamp = Adw.Clamp()
        clamp.set_maximum_size(560)
        clamp.set_tightening_threshold(460)
        clamp.set_child(self._content)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(clamp)
        toolbar.set_content(scroller)
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        self._build()

    # ── build (once) ─────────────────────────────────────────────────────

    def _build(self):
        self._idle_box = self._build_idle()
        self._waiting_box = self._build_waiting()
        self._streaming_box = self._build_streaming()
        for box in (self._idle_box, self._waiting_box, self._streaming_box):
            self._content.append(box)
        self._apply_state()

    def _page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(20)
        box.set_margin_bottom(22)
        box.set_margin_start(18)
        box.set_margin_end(18)
        return box

    def _toast(self, text):
        self._toasts.add_toast(Adw.Toast(title=text, timeout=3))

    # ── idle ─────────────────────────────────────────────────────────────

    def _build_idle(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_margin_start(28)
        box.set_margin_end(28)
        box.set_margin_top(20)
        box.set_margin_bottom(22)

        icon_path = os.path.join(os.path.dirname(__file__), "assets", "tethrlink.png")
        if os.path.exists(icon_path):
            logo = Gtk.Image.new_from_file(icon_path)
            logo.set_pixel_size(112)
            box.append(logo)

        box.append(_label("Ready to connect", "hero-title", Gtk.Align.CENTER))
        box.append(_label(
            "Connect your tablet by USB and turn on USB tethering,\n"
            "then start the server.",
            "hero-sub", Gtk.Align.CENTER, justify=Gtk.Justification.CENTER))

        btn = Gtk.Button(label="Start Server")
        btn.add_css_class("pill-primary")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_top(10)
        btn.connect("clicked", lambda _b: self._on_start())
        box.append(btn)

        settings = self._build_settings_group()
        settings.set_margin_top(18)
        box.append(settings)
        return box

    # ── waiting ──────────────────────────────────────────────────────────

    def _build_waiting(self):
        outer = self._page()

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head.set_halign(Gtk.Align.CENTER)
        spin = Gtk.Spinner()
        spin.set_size_request(28, 28)
        spin.start()
        head.append(spin)
        head.append(_label("Waiting for your tablet", "hero-title", Gtk.Align.CENTER))
        head.append(_label(
            "Open TethrLink on the tablet — it should find\n"
            "this computer automatically.",
            "hero-sub", Gtk.Align.CENTER, justify=Gtk.Justification.CENTER))
        outer.append(head)

        # Exactly what someone needs when a tablet will not connect: the
        # port the server is listening on, and whether a USB tether link
        # even exists right now. usb_tether_source_addresses() re-walks
        # sysfs fresh on every call (see server/core/link.py) rather than
        # caching, so this is never stale.
        conn = Adw.PreferencesGroup(title="CONNECTION")
        self._port_row = self._info("Listening on port", "—")
        conn.add(self._port_row)
        self._link_row = self._info("USB link", "Not detected")
        conn.add(self._link_row)
        outer.append(conn)

        outer.append(self._build_settings_group())
        outer.append(self._stop_button())
        return outer

    # ── streaming ────────────────────────────────────────────────────────

    def _build_streaming(self):
        outer = self._page()

        # Health is surfaced because we uniquely can: the pipeline already
        # tracks dropped frames and queue overflows (StreamMetrics), so one
        # honest line — Smooth / Recovering — is the most valuable thing
        # this window can say once a device is connected. It's computed
        # from real per-second counts (see ServerState.dropped's docstring
        # in server_core.py), never guessed: see _refresh_streaming().
        self._banner = Adw.Banner(title="Frames are being dropped")
        self._banner.set_button_label("Details")
        self._banner.connect("button-clicked", self._on_banner_details)
        outer.append(self._banner)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hero.set_halign(Gtk.Align.CENTER)
        self._hero_title = _label("", "hero-title", Gtk.Align.CENTER)
        self._hero_sub = _label("", "hero-sub", Gtk.Align.CENTER)
        hero.append(self._hero_title)
        hero.append(self._hero_sub)
        outer.append(hero)

        # FPS / resolution / touch, not FPS / bitrate / touch as in the
        # prototype: the pipeline only ever reports the *target* bitrate
        # fed to the encoder at connection time (ServerConfig.bitrate),
        # never a measured live throughput, and update_status() doesn't
        # carry one either. Showing the static target as if it tracked the
        # live stream would be exactly the kind of fabricated reading this
        # window is designed never to show — so resolution (real,
        # per-connection data already in update_status()) takes its place.
        stats = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)
        self._stat_fps = self._stat("0", "FRAMES / SEC")
        self._stat_res = self._stat("—", "RESOLUTION")
        self._stat_touch = self._stat("Off", "TOUCH INPUT")
        stats.append(self._stat_fps)
        stats.append(self._stat_res)
        stats.append(self._stat_touch)
        outer.append(stats)

        outer.append(self._build_settings_group())

        arrange = Adw.PreferencesGroup()
        row = Adw.ActionRow(title="Display arrangement",
                            subtitle="Position and orientation are managed by GNOME")
        open_btn = Gtk.Button(label="Open")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", self._open_display_settings)
        row.add_suffix(open_btn)
        arrange.add(row)
        outer.append(arrange)

        outer.append(self._stop_button())
        return outer

    def _on_banner_details(self, _banner):
        self._toast(
            f"{self._dropped} dropped · {self._overflows} queue overflows "
            "this second — usually the link or the tablet's decoder "
            "falling behind")

    # ── settings (shared by all three pages) ────────────────────────────

    def _build_settings_group(self):
        """One instance of the codec/touch settings, for whichever page
        this is embedded in. Subtitles report what is in EFFECT, not just
        what was requested — the two genuinely differ: an X11 session
        always falls back to JPEG regardless of the codec picked here, and
        touch can be switched on here but never actually negotiated (the
        tablet didn't advertise support, or RemoteDesktop pairing failed
        and the session fell back to video-only). A control that only
        showed its own position would be quietly lying about what's
        actually happening. Both rows are locked while a client is
        connected: the pipeline (and, for touch, the RemoteDesktop
        pairing) is built once per connection, so a change made mid-stream
        would silently do nothing — better a visibly locked control with a
        reason attached than one that looks live but isn't.
        """
        group = Adw.PreferencesGroup(title="SETTINGS")

        codec_row = Adw.ComboRow(title="Video codec",
                                 model=Gtk.StringList.new(list(self._CODEC_DISPLAY)))
        codec_row.connect("notify::selected", self._on_codec_selected)
        group.add(codec_row)
        self._codec_rows.append(codec_row)

        touch_row = Adw.SwitchRow(title="Touch input")
        touch_row.connect("notify::active", self._on_touch_toggled)
        group.add(touch_row)
        self._touch_rows.append(touch_row)

        return group

    def _refresh_settings(self):
        idx = self._CODEC_VALUES.index(self._want_codec)
        codec_in_effect = self._connected and self._codec_name

        self._updating_codec = True
        try:
            for row in self._codec_rows:
                row.set_selected(idx)
                row.set_sensitive(not self._connected)
                if codec_in_effect:
                    requested_display = self._CODEC_DISPLAY[idx]
                    if self._codec_name != requested_display:
                        row.set_subtitle(f"Using {self._codec_name} this session")
                    else:
                        row.set_subtitle(f"Active · {self._codec_name}")
                else:
                    row.set_subtitle("Applies to the next connection")
        finally:
            self._updating_codec = False

        self._updating_touch = True
        try:
            for row in self._touch_rows:
                row.set_active(self._want_touch)
                row.set_sensitive(not self._connected)
                if not self._connected:
                    row.set_subtitle("Lets the tablet control this computer")
                elif self._want_touch and not self._input_active:
                    row.set_subtitle("Not active — the tablet did not request it")
                elif self._input_active:
                    row.set_subtitle("Active this session")
                else:
                    row.set_subtitle("Off")
        finally:
            self._updating_touch = False

    def _on_codec_selected(self, row, _pspec):
        if self._updating_codec:
            return
        idx = row.get_selected()
        if not (0 <= idx < len(self._CODEC_VALUES)):
            return
        self._want_codec = self._CODEC_VALUES[idx]
        self._on_codec_change(self._want_codec)
        self._toast(f"{self._CODEC_DISPLAY[idx]} applies on the next connection")
        self._refresh_settings()

    def _on_touch_toggled(self, row, _pspec):
        if self._updating_touch:
            return
        self._want_touch = row.get_active()
        if self._on_touch_change:
            self._on_touch_change(self._want_touch)
        self._toast("Touch enabled" if self._want_touch else "Touch disabled")
        self._refresh_settings()

    # ── shared building blocks ──────────────────────────────────────────

    def _stop_button(self):
        b = Gtk.Button(label="Stop Server")
        b.add_css_class("pill-quiet")
        b.set_halign(Gtk.Align.CENTER)
        b.set_margin_top(4)
        b.connect("clicked", lambda _b: self._on_stop())
        return b

    def _stat(self, value, caption):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("stat-card")
        box.set_size_request(-1, 74)
        v = _label(value, "stat-value")
        v.set_margin_top(12)
        c = _label(caption, "stat-label")
        c.set_margin_bottom(12)
        box.append(v)
        box.append(c)
        # Value label is the child callers update; stash it on the box so
        # refresh code doesn't need get_first_child() gymnastics.
        box.value_label = v
        return box

    def _info(self, title, value):
        row = Adw.ActionRow(title=title)
        v = _label(value, "hero-sub")
        v.set_valign(Gtk.Align.CENTER)
        row.add_suffix(v)
        row.value_label = v
        return row

    def _open_display_settings(self, _btn):
        try:
            subprocess.Popen(["gnome-control-center", "display"],
                             start_new_session=True)
        except FileNotFoundError:
            # Fallback for non-GNOME desktops.
            try:
                subprocess.Popen(["xfce4-display-settings"],
                                 start_new_session=True)
            except FileNotFoundError:
                pass

    # ── state machine ────────────────────────────────────────────────────

    def _apply_state(self):
        idle = not self._running
        waiting = self._running and not self._connected
        streaming = self._running and self._connected

        self._idle_box.set_visible(idle)
        self._waiting_box.set_visible(waiting)
        self._streaming_box.set_visible(streaming)

        self._refresh_settings()
        if waiting:
            self._refresh_waiting()
        if streaming:
            self._refresh_streaming()

    def _refresh_waiting(self):
        self._port_row.value_label.set_label(str(self._port) if self._port else "—")
        addrs = usb_tether_source_addresses()
        self._link_row.value_label.set_label(addrs[0] if addrs else "Not detected")

    def _refresh_streaming(self):
        healthy = (self._dropped == 0 and self._overflows == 0)
        self._banner.set_revealed(not healthy)

        self._hero_title.set_label("Streaming smoothly" if healthy else "Recovering")
        self._hero_title.remove_css_class("hero-ok")
        self._hero_title.remove_css_class("hero-warn")
        self._hero_title.add_css_class("hero-ok" if healthy else "hero-warn")
        sub_bits = [b for b in (self._client_ip, self._codec_name) if b]
        self._hero_sub.set_label("  ·  ".join(sub_bits))

        self._stat_fps.value_label.set_label(str(self._fps))
        self._stat_res.value_label.set_label(self._resolution or "—")
        self._stat_touch.value_label.set_label("On" if self._input_active else "Off")

    # ── public API ────────────────────────────────────────────────────────

    def set_server_running(self, running: bool, restarting: bool = False):
        self._running = bool(running)
        if not running:
            # A stop always clears the last-known connection snapshot so a
            # later render never shows data left over from the previous run.
            self._connected = False
        self._apply_state()
        if restarting:
            # No current caller passes restarting=True (see main.py), but
            # the parameter is kept working rather than silently doing
            # nothing: a toast is the least intrusive way to surface a
            # restart without inventing a fourth page for a transition
            # nothing currently triggers.
            self._toast("Restarting…")

    def update_status(self, connected: bool, client_ip: str = "",
                      fps: int = 0, resolution: str = "", codec_name: str = "",
                      input_active: bool = False, *,
                      dropped: int = 0, overflows: int = 0, port: int = 0):
        # `client_ip` is named for the parameter's original meaning, but
        # ServerState.client_name — what callers actually pass here — is
        # the connected device's name (e.g. "SM-X920"), not an IP address.
        # Kept as-is rather than renamed: this is a public method other
        # code (server/app/main.py) calls by this signature.
        self._connected = bool(connected)
        self._client_ip = client_ip
        self._fps = fps
        self._resolution = resolution
        self._codec_name = codec_name
        self._input_active = input_active
        self._dropped = dropped
        self._overflows = overflows
        if port:
            self._port = port
        if not connected:
            # A just-ended session's readings must not linger and be
            # mistaken for live data on whatever's shown next.
            self._fps = 0
            self._resolution = ""
            self._codec_name = ""
            self._input_active = False
            self._dropped = 0
            self._overflows = 0
        self._apply_state()
