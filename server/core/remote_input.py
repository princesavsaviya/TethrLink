"""D-Bus binding for Mutter's RemoteDesktop pointer injection.

Thin by design. The logic that has to be right — coordinate scaling and the
wire-to-evdev button mapping — lives in input_protocol.py and is unit
tested there without a GNOME session. This module's only job is talking to
org.gnome.Mutter.RemoteDesktop, using the same dbus + GLib-mainloop idiom as
MutterVirtualDisplay in server_core.py (dbus.SessionBus, dbus.Interface, a
DBusGMainLoop already installed as the default by server_core at import
time).

Input is an addition bolted onto a video pipeline that already ships.
**Every public method here is exception-safe**: a D-Bus failure is logged
and the event dropped — never retried, since a stale pointer event is
worthless — and never re-raised, since a hiccup on this path must not be
allowed to touch streaming. This project has already shipped one defect
where an auxiliary failure broke the main path; this module's job is to not
repeat that mistake for input.

`create()` returning `None` is not an error condition — it means "no input
for this session," which the caller (a later task) is expected to treat as
a normal, supported outcome and continue video-only.
"""

import logging
import time
from typing import Optional, Set, Tuple

import dbus

from server.core.input_protocol import to_stream_coords

log = logging.getLogger("TethrLink")

RD_BUS    = "org.gnome.Mutter.RemoteDesktop"
RD_PATH   = "/org/gnome/Mutter/RemoteDesktop"
RD_IF     = "org.gnome.Mutter.RemoteDesktop"
RD_SES_IF = "org.gnome.Mutter.RemoteDesktop.Session"
PROPS_IF  = "org.freedesktop.DBus.Properties"

# Default motion-coalescing window: at most one NotifyPointerMotionAbsolute
# dispatch per this many seconds. Matches ServerConfig's default capture
# rate (fps = 30 in server_core.py) since each dispatch is a synchronous
# D-Bus round trip and a touch drag generates events far faster than the
# display refreshes. A caller running the stream at a different fps may
# pass its own interval to __init__.
DEFAULT_FRAME_INTERVAL_S = 1.0 / 30


def dispose_remote_desktop_session(session) -> bool:
    """Best-effort teardown of a RemoteDesktop session that may have been
    created but never started.

    Stop() is this interface's only teardown method, but Mutter rejects
    it — "Session not started" — on a session that was created via
    RemoteDesktop.CreateSession and never had Start() called on it, and
    critically that rejection does not unregister the session from the
    bus: it stays alive there, invisible except by enumerating sessions.
    This is exactly the state a paired MutterVirtualDisplay.setup() (in
    server_core.py) can leave behind: CreateSession (the RemoteDesktop
    side, done earlier by create() below) succeeds, but a later step —
    RecordVirtual, the setup timeout, or the session getting closed out
    from under it — fails before Start() is ever reached.

    The only way observed to actually release such a session is to run
    it through its real lifecycle: Start() it, then Stop() it. Both of
    those, and the initial bare Stop() attempt, are each individually
    best-effort — nothing here is ever allowed to raise, since a
    disposal helper that raises is strictly worse than the leak it
    exists to fix. Every caller (create()'s own failure path, stop(),
    and cleanup_orphaned_sessions()'s RemoteDesktop sweep in
    server_core.py) depends on that guarantee.

    Returns True if a Stop() call is believed to have succeeded (either
    the first bare attempt, or the Start()-then-Stop() fallback), False
    if every attempt failed. Purely informational for the benefit of a
    caller that wants to log accurately — nothing here needs to act on
    it, since every path is already best-effort regardless of outcome.
    """
    try:
        session.Stop()
        return True
    except Exception:
        pass
    try:
        session.Start()
    except Exception:
        pass
    try:
        session.Stop()
        return True
    except Exception:
        return False


class RemoteInput:
    """Binds one Mutter RemoteDesktop session and drives the pointer on the
    paired ScreenCast stream's own coordinate space.

    Usage, mirroring the RemoteDesktop/ScreenCast pairing requirement:
        ri = RemoteInput(width, height)          # same target size handed to
                                                  # MutterVirtualDisplay, since
                                                  # both describe the same
                                                  # virtual monitor
        session_id = ri.create()                # None => no input this session
        if session_id is not None:
            # ... create the ScreenCast session with
            #     remote-desktop-session-id = session_id ...
            ri.bind_stream(stream_path)
            ri.start()                           # after the ScreenCast stream exists
        ...
        ri.move(nx, ny)      # normalised [0,1], coalesced
        ri.button(code, True)
        ri.axis(dx, dy)
        ri.release_all()     # e.g. on an unexpected client disconnect
        ri.stop()            # also flushes pending motion and releases
                              # any still-held buttons

    `width`/`height` are taken at construction rather than in bind_stream(),
    because they are not discoverable from the ScreenCast D-Bus API at all —
    the Stream object's only property is an opaque `mapping-id` (verified by
    introspection). The real pixel size a stream ends up with is whatever its
    PipeWire consumer negotiates (see the comment on MutterVirtualDisplay's
    RecordVirtual call in server_core.py), which in this codebase is always
    the same width/height already decided before any session exists — the
    one forced through the capture pipeline's caps filter. So the caller
    already has this value up front, same as MutterVirtualDisplay(width,
    height, bus), and passing it here keeps to_stream_coords honest about
    the space NotifyPointerMotionAbsolute actually addresses.
    """

    def __init__(self, width: int, height: int,
                 bus: Optional[dbus.SessionBus] = None,
                 frame_interval_s: float = DEFAULT_FRAME_INTERVAL_S):
        self._width = width
        self._height = height
        self._bus = bus or dbus.SessionBus()
        self._frame_interval_s = frame_interval_s
        self._session: Optional[dbus.Interface] = None
        self._stream_path: Optional[str] = None
        self._last_motion_dispatch = 0.0
        # Most recent position dropped by the coalescing gate in move(),
        # retained so it can be flushed later instead of lost outright. See
        # move()'s docstring and _flush_pending_motion().
        self._pending_position: Optional[Tuple[float, float]] = None
        # Button codes this module believes are currently held down on the
        # desktop, so release_all() can recover from a dropped or missing
        # release. See button() and release_all().
        self._held_buttons: Set[int] = set()

    def create(self) -> Optional[str]:
        """Create a RemoteDesktop session and return its SessionId.

        The caller passes this id as `remote-desktop-session-id` in the
        properties dict of ScreenCast's CreateSession, which is what binds
        the two sessions together. Returns None on any failure — video must
        proceed with or without this.
        """
        session_obj = None
        try:
            rd_obj = self._bus.get_object(RD_BUS, RD_PATH)
            rd = dbus.Interface(rd_obj, RD_IF)
            session_path = str(rd.CreateSession())
            session_obj = self._bus.get_object(RD_BUS, session_path)
            props = dbus.Interface(session_obj, PROPS_IF)
            session_id = str(props.Get(RD_SES_IF, "SessionId"))
            self._session = dbus.Interface(session_obj, RD_SES_IF)
            return session_id
        except Exception as e:
            log.warning("RemoteInput.create failed — continuing without input: %s", e)
            self._session = None
            # CreateSession may have already succeeded on the bus even
            # though a later step (reading SessionId) failed — best-effort
            # avoid leaving an orphaned, never-started session behind. See
            # dispose_remote_desktop_session() above for why a bare Stop()
            # is not enough on its own to actually get rid of one.
            if session_obj is not None:
                try:
                    dispose_remote_desktop_session(
                        dbus.Interface(session_obj, RD_SES_IF)
                    )
                except Exception:
                    pass
            return None

    def start(self) -> None:
        """Start the session. Call after the paired ScreenCast session has
        been created, since the two are bound by session id at that point."""
        if self._session is None:
            return
        try:
            self._session.Start()
        except Exception as e:
            log.warning("RemoteInput.start failed: %s", e)

    def bind_stream(self, stream_path: str) -> None:
        """Record the ScreenCast stream path pointer coordinates are scoped
        to. The pixel dimensions for scaling were already given in
        __init__ — see the class docstring for why."""
        self._stream_path = stream_path

    def move(self, nx: float, ny: float) -> None:
        """Move the pointer to a normalised [0,1] position on the bound
        stream.

        Coalesced to at most one dispatch per frame interval: callers may
        invoke this far faster than that (a touch drag easily beats the
        display's refresh rate), and only the most recent position as of
        each interval boundary is actually sent over D-Bus — never the
        first of a burst, since a stale coordinate is worse than a dropped
        one.

        A call that arrives while the gate is closed is not discarded: it
        is kept as the pending position (most recent wins) and flushed
        later by _flush_pending_motion(), so a gesture that ends mid-gate
        (finger lifts, no further move() ever arrives) still reaches its
        true final position instead of stalling wherever the last
        *dispatched* move left the pointer.

        Every argument and every step here — including the scaling call —
        is inside the guard: a malformed, non-numeric argument must not
        escape this method any more than a D-Bus failure does.
        """
        if self._session is None or self._stream_path is None:
            return
        try:
            coords = to_stream_coords(nx, ny, self._width, self._height)
            if coords is None:
                return
            now = time.monotonic()
            if now - self._last_motion_dispatch < self._frame_interval_s:
                self._pending_position = coords
                return
            self._pending_position = None
            x, y = coords
            # Mark the interval consumed before the round trip so a D-Bus
            # failure can't turn into a retry storm on the very next call.
            self._last_motion_dispatch = now
            self._session.NotifyPointerMotionAbsolute(self._stream_path, x, y)
        except Exception as e:
            log.debug("RemoteInput.move dropped: %s", e)

    def _flush_pending_motion(self) -> None:
        """Dispatch a position retained by move()'s coalescing gate, if
        any, immediately and unconditionally.

        Called before any button event, because in absolute-pointer mode a
        gesture is bracketed by button events, so this deterministically
        catches the end of every drag and every tap — and also fixes a
        subtler ordering bug: a tap must click where the finger actually
        is, not where the previous dispatched move left the pointer.

        Also called from stop(), so a session never ends with an
        unflushed position sitting in the gate.
        """
        pending = self._pending_position
        self._pending_position = None
        if pending is None or self._session is None or self._stream_path is None:
            return
        x, y = pending
        self._last_motion_dispatch = time.monotonic()
        try:
            self._session.NotifyPointerMotionAbsolute(self._stream_path, x, y)
        except Exception as e:
            log.debug("RemoteInput.move dropped (flush): %s", e)

    def button(self, code: int, pressed: bool) -> bool:
        """Notify a pointer button edge. Never coalesced — a click is a
        discrete event, not a stream of redundant samples.

        A release for a code this module does not currently believe is
        held is a no-op: there is nothing to release, so forwarding it
        would be pure noise. This is what actually bounds a flood of
        button-release messages — the reader deliberately never
        rate-limits releases (a dropped *meaningful* release would leave
        a button stuck down, the worst failure mode this feature has —
        see server_core.py's dispatch_input_message), so without this
        check every release in such a flood would reach D-Bus
        unthrottled. With it, only genuinely-held releases ever get
        that far, and at most a handful of buttons can be held at once.

        A press, or a release for a code that IS held, is never subject
        to this or any other gate: it flushes any pending coalesced
        motion first (see _flush_pending_motion()), updates the held set,
        and is dispatched — exactly as before.

        release_all() does not call this method at all — it drives the
        held set and the D-Bus session directly — so it is unaffected by
        this no-op rule and still releases everything genuinely held
        regardless of it.

        Returns True if the edge was meaningful and dispatch was
        attempted (regardless of whether the D-Bus call itself
        succeeded — that failure mode is logged and swallowed, same as
        every other method here), False if it was discarded as a no-op
        release. Callers that want to count discarded releases (see
        dispatch_input_message) can do so from this return value.
        """
        if not pressed and code not in self._held_buttons:
            return False
        self._flush_pending_motion()
        if pressed:
            self._held_buttons.add(code)
        else:
            self._held_buttons.discard(code)
        if self._session is None:
            return True
        try:
            self._session.NotifyPointerButton(code, pressed)
        except Exception as e:
            log.debug("RemoteInput.button dropped: %s", e)
        return True

    def release_all(self) -> None:
        """Release every button this module believes is currently held,
        and clear the held set.

        Exception-safe and idempotent, like everything else here: safe to
        call when nothing is held, safe to call more than once, and one
        button's failed release never prevents the others from being
        attempted. Called from stop() so ending a session can never leave
        a button down; also called when a client disconnects
        unexpectedly.
        """
        held = list(self._held_buttons)
        self._held_buttons.clear()
        if self._session is None:
            return
        for code in held:
            try:
                self._session.NotifyPointerButton(code, False)
            except Exception as e:
                log.debug("RemoteInput.release_all dropped release for %s: %s", code, e)

    def axis(self, dx: float, dy: float) -> None:
        """Notify a scroll delta. Never coalesced — each delta is relative,
        so dropping one loses scroll distance rather than just staleness."""
        if self._session is None:
            return
        try:
            self._session.NotifyPointerAxis(dx, dy, 0)
        except Exception as e:
            log.debug("RemoteInput.axis dropped: %s", e)

    def stop(self) -> None:
        """Stop the session. Safe to call twice, and safe to call on a
        session that was created but never started — Mutter rejects a
        bare Stop() in that case ("Session not started") and, left at
        that, never actually disposes the session either.
        dispose_remote_desktop_session() (module-level, above) is what
        handles that: it falls back to Start()-then-Stop() so the
        session is actually released rather than merely forgotten about
        on our end while it lingers on the bus.

        Also flushes any pending coalesced motion and releases every held
        button first, so a session never ends with a stale pointer
        position outstanding or a button stuck down.
        """
        self._flush_pending_motion()
        self.release_all()
        if self._session is None:
            return
        try:
            dispose_remote_desktop_session(self._session)
        except Exception as e:
            log.debug("RemoteInput.stop: %s", e)
        finally:
            self._session = None
            self._stream_path = None
