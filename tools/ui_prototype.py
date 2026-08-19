#!/usr/bin/env python3
"""Standalone prototype of a redesigned TethrLink server window.

Runs on its own — no server, no GStreamer, no tablet. Every state is faked so
the whole flow can be looked at and argued about before any of it touches
`server/ui/window.py`.

    ./venv/bin/python tools/ui_prototype.py
    ./venv/bin/python tools/ui_prototype.py --state recovering

State switching is off-screen so it cannot be mistaken for real UI:
press **1-5** to jump between Idle, Waiting, Streaming, Recovering and
X11 fallback, or pass `--state`.

──────────────────────────────────────────────────────────────────────────────
Design reasoning, so the choices can be challenged rather than guessed at:

1. **Libadwaita, not hand-rolled boxes.** `Adw` is available here and is
   already a declared .deb dependency. Native rows, status pages and toasts
   mean the app looks like part of the desktop. The current window
   reimplements what `AdwPreferencesGroup` and `AdwActionRow` already do.

2. **The window has two jobs, and they are not equally important.** Before a
   device connects, the only thing that matters is *getting connected*. After
   it connects, the user mostly ignores the window — so what matters then is
   answering "is it working?" at a glance. Those are different screens.

3. **Health is surfaced, because we uniquely can.** The pipeline already
   tracks dropped frames, queue overflows and keyframe resyncs. Nothing else
   in a screen-sharing app tells you *why* it looks bad. One honest line —
   Smooth / Recovering — is the most valuable thing this window can say.

4. **Settings show what is in EFFECT, not just what was requested.** An X11
   session always falls back to JPEG, and touch can be switched on but never
   negotiated by the client. That divergence lives in the row subtitle, where
   it stays visible, rather than in a toast that disappears.

5. **Connection details are visible while waiting**, which is exactly when
   someone needs them to work out why a tablet cannot find the server.

Theme and logo are the app's own: `server/ui/style.css` is loaded verbatim so
this cannot drift from the product, with additions below only for the
Adwaita widgets that stylesheet never had to cover.
──────────────────────────────────────────────────────────────────────────────
"""

import json
import os
import pathlib
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Settings are user intent, so they belong in config — not in
# ~/.cache/tethrlink/profiles.json, which is a regenerable cache. This is the
# path the old README always claimed was used; making it true is overdue.
SETTINGS = (pathlib.Path(os.environ.get("XDG_CONFIG_HOME",
                                        pathlib.Path.home() / ".config"))
            / "tethrlink" / "settings.json")


def load_settings():
    """Best-effort: a corrupt or missing file must never stop the app."""
    try:
        d = json.loads(SETTINGS.read_text())
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def save_settings(d):
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(d, indent=2))
        return True
    except OSError:
        return False


STYLE = os.path.join(REPO, "server", "ui", "style.css")
LOGO = os.path.join(REPO, "server", "ui", "assets", "tethrlink.png")

# Palette is the Android app's, mirrored in server/ui/style.css, so the two
# halves of the product look like one product. Android is the source of
# truth: it is the surface the user actually looks at, and its alpha-based
# text and borders scale across surfaces better than fixed greys.
BG = "#0F0F1A"        # bg_primary
CARD = "#1A1A2E"      # bg_card
BORDER = "#FFFFFF1A"  # border_default — GTK is #RRGGBBAA, alpha LAST
TEXT = "#FFFFFF"      # text_primary
DIM = "#FFFFFF99"     # text_secondary — alpha last, not Android's ARGB
ACCENT = "#7C6AF7"    # brand
ACCENT_HOVER = "#6455D4"  # brand_pressed
OK = "#4ADE80"        # success
WARN = "#EAB308"      # warning

# Adwaita widgets did not exist in the original stylesheet, so they need
# colours of their own. Everything here is drawn from the palette above.
EXTRA_CSS = f"""
headerbar {{
    background-color: {CARD};
    border-bottom: 1px solid {BORDER};
    color: {TEXT};
}}
.app-title {{ font-weight: 700; }}

/* Adwaita rows: match .section-card rather than Adwaita's own light grey. */
preferencesgroup > box > label {{
    color: {DIM};
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
row.activatable:hover {{ background-color: {BORDER}; }}
listview, list, row {{
    background-color: {CARD};
    color: {TEXT};
}}
list {{
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
row > box > label.subtitle {{ color: {DIM}; font-size: 12px; }}

.hero-title  {{ font-size: 22px; font-weight: 700; color: {TEXT}; }}
.hero-sub    {{ color: {DIM}; font-size: 13px; }}
.hero-ok     {{ color: {OK}; }}
.hero-warn   {{ color: {WARN}; }}

.stat-card {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
.stat-value  {{ font-size: 20px; font-weight: 700; color: {TEXT}; }}
.stat-label  {{ font-size: 11px; color: {DIM}; letter-spacing: 0.3px; }}

.pill-primary {{
    background-image: none;
    background-color: {ACCENT};
    color: #ffffff;
    border: none;
    border-radius: 999px;
    padding: 10px 30px;
    font-weight: 600;
}}
.pill-primary:hover {{ background-color: {ACCENT_HOVER}; }}
.pill-quiet {{
    background-image: none;
    background-color: transparent;
    color: {DIM};
    border: 1px solid {BORDER};
    border-radius: 999px;
    padding: 8px 24px;
}}
.pill-quiet:hover {{ color: {WARN}; border-color: {WARN}; }}

banner > revealer > widget {{
    background-color: {CARD};
    border: 1px solid {WARN};
    border-radius: 12px;
    color: {TEXT};
}}
"""

STATES = ["idle", "waiting", "streaming", "recovering", "x11"]

FAKE = {
    "waiting": {"port": 51137, "link": "10.125.32.247"},
    "streaming": {
        "device": "SM-X920", "res": "1730×1080", "fps": 30, "codec": "H.264",
        "bitrate": 8000, "dropped": 12, "overflows": 3,
        "input": True, "health": "smooth",
    },
    "recovering": {
        "device": "SM-X920", "res": "1730×1080", "fps": 18, "codec": "H.264",
        "bitrate": 8000, "dropped": 204, "overflows": 37,
        "input": True, "health": "recovering",
    },
    "x11": {
        "device": "SM-X920", "res": "1920×1080", "fps": 20, "codec": "JPEG",
        "bitrate": 40000, "dropped": 0, "overflows": 0,
        "input": False, "health": "smooth",
    },
}


def _load_css():
    if os.path.exists(STYLE):
        p = Gtk.CssProvider()
        p.load_from_path(STYLE)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    extra = Gtk.CssProvider()
    extra.load_from_data(EXTRA_CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), extra,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)


class PrototypeWindow(Adw.ApplicationWindow):
    def __init__(self, app, state="idle"):
        super().__init__(application=app, title="TethrLink")
        self.set_default_size(440, 640)
        self._state = state
        saved = load_settings()
        self._want_codec = saved.get("codec", "H.264")
        self._want_touch = bool(saved.get("touch_enabled", False))

        self._toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if os.path.exists(LOGO):
            mark = Gtk.Image.new_from_file(LOGO)
            mark.set_pixel_size(20)
            title.append(mark)
        name = Gtk.Label(label="TethrLink")
        name.add_css_class("app-title")
        title.append(name)
        header.set_title_widget(title)
        toolbar.add_top_bar(header)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar.set_content(self._content)
        self._toasts.set_child(toolbar)
        self.set_content(self._toasts)

        # Prototype-only, and deliberately invisible so it cannot be mistaken
        # for a real control: number keys jump between states.
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self._render()

    def _on_key(self, _c, keyval, _code, _mod):
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_5:
            self._state = STATES[keyval - Gdk.KEY_1]
            self._render()
            return True
        return False

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

    def _go(self, state):
        self._state = state
        self._render()

    # ── idle ─────────────────────────────────────────────────────────────────

    def _build_idle(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_valign(Gtk.Align.CENTER)
        box.set_vexpand(True)
        box.set_margin_start(28)
        box.set_margin_end(28)

        if os.path.exists(LOGO):
            logo = Gtk.Image.new_from_file(LOGO)
            logo.set_pixel_size(112)
            box.append(logo)

        t = Gtk.Label(label="Ready to connect")
        t.add_css_class("hero-title")
        s = Gtk.Label(
            label="Connect your tablet by USB and turn on USB tethering,\n"
                  "then start the server.",
            justify=Gtk.Justification.CENTER)
        s.add_css_class("hero-sub")
        box.append(t)
        box.append(s)

        btn = Gtk.Button(label="Start Server")
        btn.add_css_class("pill-primary")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_top(10)
        btn.connect("clicked", lambda _b: self._go("waiting"))
        box.append(btn)

        # Settings belong here too. Touch applies from the moment a client
        # connects, so a user who can only reach it *after* starting would
        # have to connect, toggle, then reconnect to actually use it.
        settings = self._settings_group(None, None)
        settings.set_margin_top(18)
        box.append(settings)
        return box

    # ── waiting ──────────────────────────────────────────────────────────────

    def _build_waiting(self):
        d = FAKE["waiting"]
        outer = self._page()

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        head.set_halign(Gtk.Align.CENTER)
        spin = Gtk.Spinner()
        spin.set_size_request(28, 28)
        spin.start()
        head.append(spin)
        t = Gtk.Label(label="Waiting for your tablet")
        t.add_css_class("hero-title")
        s = Gtk.Label(label="Open TethrLink on the tablet — it should find\n"
                            "this computer automatically.",
                      justify=Gtk.Justification.CENTER)
        s.add_css_class("hero-sub")
        head.append(t)
        head.append(s)
        outer.append(head)

        # Exactly what someone needs when a tablet will not connect.
        conn = Adw.PreferencesGroup(title="CONNECTION")
        conn.add(self._info("Listening on port", str(d["port"])))
        conn.add(self._info("USB link", d["link"]))
        outer.append(conn)

        outer.append(self._settings_group(None, None))
        outer.append(self._stop_button())
        return outer

    # ── streaming ────────────────────────────────────────────────────────────

    def _build_streaming(self, d):
        outer = self._page()
        healthy = d["health"] == "smooth"

        if not healthy:
            banner = Adw.Banner(title="Frames are being dropped", revealed=True)
            banner.set_button_label("Details")
            banner.connect("button-clicked", lambda _b: self._toast(
                f"{d['dropped']} dropped · {d['overflows']} queue overflows — "
                "usually the link or the tablet's decoder falling behind"))
            outer.append(banner)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hero.set_halign(Gtk.Align.CENTER)
        t = Gtk.Label(label="Streaming smoothly" if healthy else "Recovering")
        t.add_css_class("hero-title")
        t.add_css_class("hero-ok" if healthy else "hero-warn")
        s = Gtk.Label(label=f"{d['device']}  ·  {d['res']}  ·  {d['codec']}")
        s.add_css_class("hero-sub")
        hero.append(t)
        hero.append(s)
        outer.append(hero)

        stats = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                        spacing=10, homogeneous=True)
        stats.append(self._stat(str(d["fps"]), "FRAMES / SEC"))
        stats.append(self._stat(f"{d['bitrate'] / 1000:.1f}", "MBIT / SEC"))
        stats.append(self._stat("On" if d["input"] else "Off", "TOUCH INPUT"))
        outer.append(stats)

        outer.append(self._settings_group(d["codec"], d["input"]))

        arrange = Adw.PreferencesGroup()
        row = Adw.ActionRow(title="Display arrangement",
                            subtitle="Position and orientation are managed by GNOME")
        b = Gtk.Button(label="Open")
        b.set_valign(Gtk.Align.CENTER)
        b.connect("clicked", lambda _x: self._toast("Would open GNOME Display Settings"))
        row.add_suffix(b)
        arrange.add(row)
        outer.append(arrange)

        outer.append(self._stop_button())
        return outer

    # ── shared ───────────────────────────────────────────────────────────────

    def _page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(20)
        box.set_margin_bottom(22)
        box.set_margin_start(18)
        box.set_margin_end(18)
        return box

    def _stop_button(self):
        b = Gtk.Button(label="Stop Server")
        b.add_css_class("pill-quiet")
        b.set_halign(Gtk.Align.CENTER)
        b.set_margin_top(4)
        b.connect("clicked", lambda _x: self._go("idle"))
        return b

    def _settings_group(self, effective_codec, input_active):
        """Settings, showing what is in EFFECT alongside what was requested.

        The two genuinely differ: an X11 session always falls back to JPEG, and
        touch can be switched on here but never negotiated by the client. A
        control that only showed its own position would be quietly lying.
        """
        group = Adw.PreferencesGroup(title="SETTINGS")

        codec = Adw.ComboRow(title="Video codec",
                             model=Gtk.StringList.new(["H.264", "JPEG"]))
        codec.set_selected(0 if self._want_codec == "H.264" else 1)
        if effective_codec and effective_codec != self._want_codec:
            codec.set_subtitle(f"Using {effective_codec} this session")
        elif effective_codec:
            codec.set_subtitle(f"Active · {effective_codec}")
        else:
            codec.set_subtitle("Applies to the next connection")
        codec.connect("notify::selected", self._on_codec)
        group.add(codec)

        touch = Adw.SwitchRow(title="Touch input", active=self._want_touch)
        if input_active is None:
            touch.set_subtitle("Lets the tablet control this computer")
        elif self._want_touch and not input_active:
            touch.set_subtitle("Not active — the tablet did not request it")
        elif input_active:
            touch.set_subtitle("Active this session")
        else:
            touch.set_subtitle("Off")
        touch.connect("notify::active", self._on_touch)
        group.add(touch)
        return group

    def _persist(self):
        save_settings({"codec": self._want_codec,
                       "touch_enabled": self._want_touch})

    def _on_codec(self, row, _p):
        self._want_codec = ["H.264", "JPEG"][row.get_selected()]
        self._persist()
        self._toast(f"{self._want_codec} applies on the next connection")

    def _on_touch(self, row, _p):
        self._want_touch = row.get_active()
        self._persist()
        self._toast("Touch enabled" if self._want_touch else "Touch disabled")

    def _stat(self, value, caption):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.add_css_class("stat-card")
        box.set_size_request(-1, 74)
        v = Gtk.Label(label=value)
        v.add_css_class("stat-value")
        v.set_margin_top(12)
        c = Gtk.Label(label=caption)
        c.add_css_class("stat-label")
        c.set_margin_bottom(12)
        box.append(v)
        box.append(c)
        return box

    def _info(self, title, value):
        row = Adw.ActionRow(title=title)
        v = Gtk.Label(label=value)
        v.add_css_class("hero-sub")
        v.set_valign(Gtk.Align.CENTER)
        row.add_suffix(v)
        return row


class PrototypeApp(Adw.Application):
    def __init__(self, state="idle"):
        super().__init__(application_id="com.tethrlink.uiprototype",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self._state = state

    def do_activate(self):
        # Force dark: the product's stylesheet is a dark theme, and letting the
        # session pick light would render it unreadable.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        _load_css()
        PrototypeWindow(self, self._state).present()


if __name__ == "__main__":
    start = "idle"
    if "--state" in sys.argv:
        i = sys.argv.index("--state")
        if i + 1 < len(sys.argv) and sys.argv[i + 1] in STATES:
            start = sys.argv[i + 1]
    sys.exit(PrototypeApp(start).run([]))
