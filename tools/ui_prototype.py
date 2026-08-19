#!/usr/bin/env python3
"""Standalone prototype of a redesigned TethrLink server window.

Runs on its own — no server, no GStreamer, no tablet. Every state is faked so
the whole flow can be looked at and argued about before any of it touches
`server/ui/window.py`.

    ./venv/bin/python tools/ui_prototype.py

Use the "Simulate" control in the header bar to step through states.

──────────────────────────────────────────────────────────────────────────────
Design reasoning, so the choices can be challenged rather than guessed at:

1. **Libadwaita, not hand-rolled boxes.** `Adw` is available here and is
   already a declared .deb dependency. Using it means native GNOME rows,
   status pages and toasts — the app stops looking like a bespoke dialog and
   starts looking like part of the desktop. The current window reimplements
   what `AdwPreferencesGroup` and `AdwActionRow` already do.

2. **The window has two jobs, and they are not equally important.** Before a
   device connects, the only thing that matters is *getting connected*. After
   it connects, the user mostly ignores the window — so what matters then is
   answering "is it working?" at a glance. Those are different screens, not
   one screen with everything on it.

3. **Health is surfaced, because we uniquely can.** The pipeline already
   tracks dropped frames, queue overflows and keyframe resyncs. Nothing else
   in a screen-sharing app tells you *why* it looks bad. Turning those into
   one honest line — Smooth / Recovering / Struggling — is the most valuable
   thing this window can say, and it costs nothing to compute.

4. **Settings state is shown, not just requested.** The codec you asked for
   and the codec actually in use can differ (X11 always falls back to JPEG),
   and touch can be requested but not negotiated by the client. That
   divergence lives in the row subtitle, where it is always visible, rather
   than in a toast that disappears.

5. **Connection details are visible while waiting**, because that is exactly
   when someone needs them to debug a tablet that will not find the server.
──────────────────────────────────────────────────────────────────────────────
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

# ── Fake state, standing in for ServerState ──────────────────────────────────

STATES = ["idle", "waiting", "streaming_smooth", "streaming_recovering", "x11_fallback"]

FAKE = {
    "idle": {},
    "waiting": {"port": 51137, "link": "10.125.32.247"},
    "streaming_smooth": {
        "device": "SM-X920", "res": "1730×1080", "fps": 30, "codec": "H.264",
        "encoder": "nvh264enc (hardware)", "bitrate": 8000,
        "dropped": 12, "overflows": 3, "input": True, "health": "smooth",
    },
    "streaming_recovering": {
        "device": "SM-X920", "res": "1730×1080", "fps": 18, "codec": "H.264",
        "encoder": "nvh264enc (hardware)", "bitrate": 8000,
        "dropped": 204, "overflows": 37, "input": True, "health": "recovering",
    },
    "x11_fallback": {
        "device": "SM-X920", "res": "1920×1080", "fps": 20, "codec": "JPEG",
        "encoder": "jpegenc (software)", "bitrate": 40000,
        "dropped": 0, "overflows": 0, "input": False, "health": "smooth",
    },
}


class PrototypeWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="TethrLink")
        self.set_default_size(460, 620)
        self._state = "idle"
        # What the user asked for, as distinct from what is in effect.
        self._want_codec = "H.264"
        self._want_touch = False

        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="TethrLink",
                                                subtitle="Display Extender"))

        # Prototype-only: lets a reviewer see every state without a device.
        sim = Gtk.DropDown.new_from_strings(
            ["Idle", "Waiting", "Streaming", "Recovering", "X11 fallback"])
        sim.set_tooltip_text("Prototype only — simulate a state")
        sim.connect("notify::selected", self._on_simulate)
        header.pack_end(sim)

        toolbar.add_top_bar(header)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar.set_content(self._content)
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        self._render()

    # ── state plumbing ───────────────────────────────────────────────────────

    def _on_simulate(self, dropdown, _param):
        self._state = STATES[dropdown.get_selected()]
        self._render()

    def _render(self):
        child = self._content.get_first_child()
        while child:
            self._content.remove(child)
            child = self._content.get_first_child()
        if self._state == "idle":
            self._content.append(self._build_idle())
        elif self._state == "waiting":
            self._content.append(self._build_waiting())
        else:
            self._content.append(self._build_streaming(FAKE[self._state]))

    def _toast(self, text):
        self._toasts.add_toast(Adw.Toast(title=text, timeout=3))

    # ── idle: the only job is to get started ─────────────────────────────────

    def _build_idle(self):
        page = Adw.StatusPage(
            icon_name="video-display-symbolic",
            title="Ready to connect",
            description="Connect your tablet by USB and turn on USB tethering, "
                        "then start the server.",
        )
        btn = Gtk.Button(label="Start Server")
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _b: (setattr(self, "_state", "waiting"),
                                           self._render()))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.append(page)
        box.append(btn)
        return box

    # ── waiting: show what a stuck user needs to debug it ────────────────────

    def _build_waiting(self):
        d = FAKE["waiting"]
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        outer.set_margin_top(24); outer.set_margin_bottom(24)
        outer.set_margin_start(18); outer.set_margin_end(18)

        page = Adw.StatusPage(
            icon_name="preferences-system-network-symbolic",
            title="Waiting for your tablet",
            description="Open TethrLink on the tablet. It should find this "
                        "computer automatically.",
        )
        page.set_vexpand(False)
        outer.append(page)

        # Visible precisely when someone needs it to work out why a tablet
        # cannot see the server.
        group = Adw.PreferencesGroup(title="Connection")
        group.add(self._row("Listening on port", str(d["port"]), "network-server-symbolic"))
        group.add(self._row("USB link", d["link"], "drive-harddisk-usb-symbolic"))
        outer.append(group)

        outer.append(self._settings_group(effective_codec=None, input_active=None))

        stop = Gtk.Button(label="Stop Server")
        stop.add_css_class("destructive-action")
        stop.add_css_class("pill")
        stop.set_halign(Gtk.Align.CENTER)
        stop.connect("clicked", lambda _b: (setattr(self, "_state", "idle"),
                                            self._render()))
        outer.append(stop)
        return outer

    # ── streaming: answer "is it working?" first ─────────────────────────────

    def _build_streaming(self, d):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        outer.set_margin_top(18); outer.set_margin_bottom(24)
        outer.set_margin_start(18); outer.set_margin_end(18)

        healthy = d["health"] == "smooth"

        if not healthy:
            banner = Adw.Banner(
                title="Recovering — frames are being dropped",
                revealed=True,
            )
            banner.set_button_label("Why?")
            banner.connect("button-clicked", lambda _b: self._toast(
                f"{d['dropped']} dropped, {d['overflows']} queue overflows. "
                "Usually the link or the decoder falling behind."))
            outer.append(banner)

        # The headline. Big, and it answers the only question that matters.
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hero.set_halign(Gtk.Align.CENTER)
        title = Gtk.Label(label="Streaming smoothly" if healthy else "Recovering")
        title.add_css_class("title-1")
        sub = Gtk.Label(label=f"{d['device']}  ·  {d['res']}  ·  {d['codec']}")
        sub.add_css_class("dim-label")
        hero.append(title)
        hero.append(sub)
        outer.append(hero)

        # Live numbers, in the order a user actually cares about them.
        stats = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                        spacing=12, homogeneous=True)
        stats.append(self._stat(str(d["fps"]), "frames / sec"))
        stats.append(self._stat(f"{d['bitrate'] / 1000:.1f}", "Mbit / sec"))
        stats.append(self._stat("On" if d["input"] else "Off", "touch input"))
        outer.append(stats)

        outer.append(self._settings_group(
            effective_codec=d["codec"], input_active=d["input"]))

        arrange = Adw.PreferencesGroup()
        row = Adw.ActionRow(
            title="Display arrangement",
            subtitle="Position and orientation are managed by GNOME",
        )
        btn = Gtk.Button(label="Open")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", lambda _b: self._toast(
            "Would open GNOME Display Settings"))
        row.add_suffix(btn)
        arrange.add(row)
        outer.append(arrange)

        stop = Gtk.Button(label="Stop Server")
        stop.add_css_class("destructive-action")
        stop.add_css_class("pill")
        stop.set_halign(Gtk.Align.CENTER)
        stop.connect("clicked", lambda _b: (setattr(self, "_state", "idle"),
                                            self._render()))
        outer.append(stop)
        return outer

    # ── shared pieces ────────────────────────────────────────────────────────

    def _settings_group(self, effective_codec, input_active):
        """Settings, showing what is in EFFECT alongside what was requested.

        The two genuinely differ: an X11 session always falls back to JPEG, and
        touch can be switched on here but never negotiated by the client. A
        control that only showed its own position would be quietly lying.
        """
        group = Adw.PreferencesGroup(title="Settings")

        codec = Adw.ComboRow(
            title="Video codec",
            model=Gtk.StringList.new(["H.264", "JPEG"]),
        )
        codec.set_selected(0 if self._want_codec == "H.264" else 1)
        if effective_codec and effective_codec != self._want_codec:
            codec.set_subtitle(f"⚠ Using {effective_codec} this session")
        elif effective_codec:
            codec.set_subtitle(f"Active: {effective_codec}")
        else:
            codec.set_subtitle("Applies to the next connection")
        codec.connect("notify::selected", self._on_codec)
        group.add(codec)

        touch = Adw.SwitchRow(
            title="Touch input",
            active=self._want_touch,
        )
        if input_active is None:
            touch.set_subtitle("Lets the tablet control this computer")
        elif self._want_touch and not input_active:
            touch.set_subtitle("⚠ Not active — the tablet did not request it")
        elif input_active:
            touch.set_subtitle("Active this session")
        else:
            touch.set_subtitle("Off")
        touch.connect("notify::active", self._on_touch)
        group.add(touch)
        return group

    def _on_codec(self, row, _p):
        self._want_codec = ["H.264", "JPEG"][row.get_selected()]
        self._toast(f"{self._want_codec} applies on the next connection")

    def _on_touch(self, row, _p):
        self._want_touch = row.get_active()
        self._toast("Touch enabled" if self._want_touch else "Touch disabled")

    def _stat(self, value, caption):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("card")
        box.set_size_request(-1, 76)
        v = Gtk.Label(label=value)
        v.add_css_class("title-2")
        v.set_margin_top(10)
        c = Gtk.Label(label=caption)
        c.add_css_class("caption")
        c.add_css_class("dim-label")
        c.set_margin_bottom(10)
        box.append(v)
        box.append(c)
        return box

    def _row(self, title, value, icon):
        row = Adw.ActionRow(title=title, subtitle=value)
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
        return row


class PrototypeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.tethrlink.uiprototype")

    def do_activate(self):
        PrototypeWindow(self).present()


if __name__ == "__main__":
    import sys
    sys.exit(PrototypeApp().run(sys.argv))
