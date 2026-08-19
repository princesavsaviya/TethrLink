"""Unit tests for RemoteInput's own state machines (motion coalescing,
held-button tracking) — the parts of remote_input.py that do not require a
live GNOME/Mutter session to exercise.

No real D-Bus session is ever touched: RemoteInput() is constructed with a
throwaway `bus` object (any truthy value bypasses the real
`dbus.SessionBus()` call in __init__), and a fake session object stands in
for the dbus.Interface a real create() would have produced. The D-Bus calls
themselves (create/start/bind_stream's real behaviour against Mutter) are
intentionally NOT covered here — that requires a live session and is
documented as verified manually in the task report.
"""

import time

import pytest

from server.core.remote_input import RemoteInput


class _FakeBus:
    """Stands in for dbus.SessionBus() purely so RemoteInput.__init__ never
    tries to open a real session bus connection. Never otherwise used."""


class _FakeSession:
    """Records NotifyPointer* calls in place of a live
    org.gnome.Mutter.RemoteDesktop.Session D-Bus interface."""

    def __init__(self):
        self.motions = []  # list of (stream_path, x, y)
        self.buttons = []  # list of (code, pressed)

    def NotifyPointerMotionAbsolute(self, stream_path, x, y):
        self.motions.append((stream_path, x, y))

    def NotifyPointerButton(self, code, pressed):
        self.buttons.append((code, pressed))


class _RaisingButtonSession(_FakeSession):
    """Like _FakeSession, but NotifyPointerButton raises for a chosen set
    of (code, pressed) edges, to exercise exception-safety."""

    def __init__(self, fail_on=()):
        super().__init__()
        self._fail_on = set(fail_on)

    def NotifyPointerButton(self, code, pressed):
        if (code, pressed) in self._fail_on:
            raise RuntimeError("simulated D-Bus failure")
        super().NotifyPointerButton(code, pressed)


def _bound(width=1000, height=500, frame_interval_s=1.0, session=None):
    """A RemoteInput with a fake session already bound directly (bypassing
    create()/bind_stream(), which need a real bus)."""
    ri = RemoteInput(width, height, bus=_FakeBus(), frame_interval_s=frame_interval_s)
    session = session if session is not None else _FakeSession()
    ri._session = session
    ri._stream_path = "/fake/stream"
    return ri, session


def _close_gate(ri):
    """Force move()'s coalescing gate shut, deterministically, regardless
    of how long the test process has been up (time.monotonic()'s epoch is
    unspecified)."""
    ri._last_motion_dispatch = time.monotonic()


def _open_gate(ri):
    """Force move()'s coalescing gate open, deterministically."""
    ri._last_motion_dispatch = time.monotonic() - 10.0


# ── Finding 1: malformed arguments must not escape move() ──────────────────

@pytest.mark.parametrize("nx, ny", [
    (None, 0.5),
    ("nope", 0.5),
    (0.5, (1, 2)),
    (object(), object()),
    (float("nan"), 0.5),
    (0.5, float("inf")),
])
def test_move_does_not_raise_on_malformed_arguments(nx, ny):
    ri, session = _bound()
    _open_gate(ri)
    ri.move(nx, ny)  # must not raise
    assert session.motions == []
    assert ri._pending_position is None


def test_move_dispatches_immediately_when_gate_is_open():
    ri, session = _bound(frame_interval_s=1.0)
    _open_gate(ri)
    ri.move(0.25, 0.5)
    assert session.motions == [("/fake/stream", 250.0, 250.0)]
    assert ri._pending_position is None


# ── Finding 2: coalescing retains and flushes the final position ───────────

def test_gated_move_is_retained_not_dropped():
    ri, session = _bound(frame_interval_s=1000.0)
    _close_gate(ri)
    ri.move(0.5, 0.5)
    assert session.motions == []  # not dispatched while gate is closed
    assert ri._pending_position == (500.0, 250.0)


def test_gated_move_is_flushed_by_a_subsequent_button_event():
    ri, session = _bound(frame_interval_s=1000.0)
    _close_gate(ri)
    ri.move(0.5, 0.5)
    ri.button(0, True)
    assert session.motions == [("/fake/stream", 500.0, 250.0)]
    assert session.buttons == [(0, True)]
    # motion must be flushed BEFORE the button dispatch, per the ordering
    # requirement (a tap must click where the finger actually is)
    assert ri._pending_position is None


def test_most_recent_gated_position_wins():
    ri, session = _bound(frame_interval_s=1000.0)
    _close_gate(ri)
    ri.move(0.1, 0.1)
    ri.move(0.9, 0.9)  # replaces the first pending position, not queued
    ri.button(0, True)
    assert session.motions == [("/fake/stream", 900.0, 450.0)]


def test_stop_flushes_pending_position():
    ri, session = _bound(frame_interval_s=1000.0)
    _close_gate(ri)
    ri.move(0.4, 0.4)
    ri.stop()
    assert session.motions == [("/fake/stream", 400.0, 200.0)]


def test_flush_is_a_noop_when_nothing_is_pending():
    ri, session = _bound(frame_interval_s=1000.0)
    ri.button(0, True)  # no prior move() at all
    assert session.motions == []
    assert session.buttons == [(0, True)]


# ── Finding 3: held-button tracking / release_all ───────────────────────────

def test_release_all_releases_exactly_the_held_buttons():
    ri, session = _bound()
    ri.button(0, True)
    ri.button(1, True)
    ri.button(0, False)  # released normally; should not be released again
    session.buttons.clear()
    ri.release_all()
    assert session.buttons == [(1, False)]
    assert ri._held_buttons == set()


def test_release_all_is_safe_when_nothing_is_held():
    ri, session = _bound()
    ri.release_all()
    assert session.buttons == []
    ri.release_all()  # idempotent
    assert session.buttons == []


def test_release_all_continues_after_one_release_fails():
    session = _RaisingButtonSession(fail_on={(0, False)})
    ri, _ = _bound(session=session)
    ri.button(0, True)
    ri.button(1, True)
    ri.release_all()  # must not raise despite code 0's release failing
    assert (1, False) in session.buttons
    assert ri._held_buttons == set()  # cleared regardless of dispatch outcome


def test_stop_releases_held_buttons():
    ri, session = _bound()
    ri.button(0, True)
    ri.stop()
    assert (0, False) in session.buttons
    assert ri._held_buttons == set()


def test_stop_is_safe_to_call_twice():
    ri, session = _bound()
    ri.button(0, True)
    ri.stop()
    session.buttons.clear()
    ri.stop()  # session is now None; must not raise, must not re-release
    assert session.buttons == []


# ── Finding 2 (review): a release for an unheld button is a no-op ──────────
#
# Button releases are exempt from the reader's flood limiter (a dropped
# *meaningful* release leaves a button stuck down — the worst failure mode
# this feature has). That exemption alone would let a flood of releases
# reach D-Bus completely unthrottled. button() is what actually bounds
# that: a release for a code it does not believe is held is discarded as a
# no-op, reported via its return value, before it ever reaches the D-Bus
# session or touches the held-button state.

def test_release_of_never_held_button_is_a_noop():
    ri, session = _bound()
    result = ri.button(0, False)  # no prior press
    assert result is False
    assert session.buttons == []
    assert ri._held_buttons == set()


def test_press_then_release_still_dispatches_both():
    ri, session = _bound()
    pressed = ri.button(0, True)
    released = ri.button(0, False)
    assert (pressed, released) == (True, True)
    assert session.buttons == [(0, True), (0, False)]
    assert ri._held_buttons == set()


def test_press_returns_true_even_with_no_session_bound():
    """button() reports whether the edge was meaningful, independent of
    whether a D-Bus session exists to receive it — dispatch_input_message
    (server_core.py) relies on this to distinguish a real drop from a
    press/genuine-release that merely has nowhere to go."""
    ri = RemoteInput(100, 100, bus=_FakeBus())  # no _session bound at all
    assert ri.button(5, True) is True
    assert ri.button(5, False) is True  # genuine release: was held


def test_release_all_after_several_presses_releases_exactly_those_buttons():
    ri, session = _bound()
    for code in (0, 1, 2):
        ri.button(code, True)
    session.buttons.clear()
    ri.release_all()
    assert sorted(session.buttons) == [(0, False), (1, False), (2, False)]
    assert ri._held_buttons == set()


def test_flood_of_unmatched_releases_dispatches_nothing():
    ri, session = _bound()
    for _ in range(2000):
        result = ri.button(7, False)  # never pressed
        assert result is False
    assert session.buttons == []
    assert ri._held_buttons == set()


def test_flood_of_unmatched_releases_does_not_disturb_a_genuinely_held_button():
    """A flood of no-op releases for OTHER codes must not affect a button
    that genuinely is held — release_all() must still recover it."""
    ri, session = _bound()
    ri.button(3, True)
    for _ in range(500):
        ri.button(9, False)  # unrelated, never held, always a no-op
    session.buttons.clear()
    ri.release_all()
    assert session.buttons == [(3, False)]
    assert ri._held_buttons == set()


# ── Finding 1 (review): disposing a RemoteDesktop session created but ──────
# never started (pairing-failure path can otherwise orphan it)

class _NeverStartedThenDisposableSession:
    """Simulates Mutter's real RemoteDesktop.Session behaviour for a
    session that was created (via CreateSession) but never Start()ed:
    Stop() raises "Session not started" until Start() has actually run,
    after which Stop() succeeds. This is the exact scenario
    dispose_remote_desktop_session() exists to handle — see its docstring
    in remote_input.py."""

    def __init__(self):
        self.started = False
        self.start_calls = 0
        self.stop_calls = 0

    def Start(self):
        self.start_calls += 1
        self.started = True

    def Stop(self):
        self.stop_calls += 1
        if not self.started:
            raise RuntimeError("Session not started")


class _AlwaysFailingSession:
    """Every call fails, no matter what — exercises dispose_remote_desktop_
    session()'s guarantee that it never raises, even when nothing works."""

    def Start(self):
        raise RuntimeError("boom")

    def Stop(self):
        raise RuntimeError("boom")


def test_dispose_remote_desktop_session_succeeds_immediately_when_started():
    from server.core.remote_input import dispose_remote_desktop_session

    session = _NeverStartedThenDisposableSession()
    session.started = True  # simulate an already-started session
    assert dispose_remote_desktop_session(session) is True
    assert session.stop_calls == 1
    assert session.start_calls == 0  # Start() fallback never needed


def test_dispose_remote_desktop_session_falls_back_to_start_then_stop():
    from server.core.remote_input import dispose_remote_desktop_session

    session = _NeverStartedThenDisposableSession()
    assert dispose_remote_desktop_session(session) is True
    # First Stop() raised (not started); Start() then a second Stop()
    # is what actually disposed it.
    assert session.start_calls == 1
    assert session.stop_calls == 2


def test_dispose_remote_desktop_session_never_raises_and_reports_failure():
    from server.core.remote_input import dispose_remote_desktop_session

    session = _AlwaysFailingSession()
    assert dispose_remote_desktop_session(session) is False  # never raises


def test_create_failure_disposes_a_partially_created_session(monkeypatch):
    """create()'s own failure path — SessionId read fails after
    CreateSession already succeeded — must dispose the now-orphaned
    session via dispose_remote_desktop_session(), not a bare Stop().

    dbus.Interface() is monkeypatched at the module level (restored by
    the `monkeypatch` fixture automatically) rather than routed through
    the real dbus.Interface + a get_dbus_method-implementing fake: it is
    only a thin wrapper (object, dbus_interface) -> proxy, so replacing
    it outright with a fake that returns the right stub per interface
    name is simpler and exercises exactly the same create() code path.
    """
    import server.core.remote_input as rim

    class _RDRootIface:
        def CreateSession(self):
            return "/org/gnome/Mutter/RemoteDesktop/Session/1"

    class _BrokenPropsIface:
        def Get(self, iface, name):
            raise RuntimeError("SessionId read failed")

    session = _NeverStartedThenDisposableSession()

    class _FakeCreateBus:
        def get_object(self, bus_name, path):
            return object()  # opaque; only the patched Interface() below matters

    def _fake_interface(obj, iface):
        if iface == rim.RD_IF:
            return _RDRootIface()
        if iface == rim.PROPS_IF:
            return _BrokenPropsIface()
        if iface == rim.RD_SES_IF:
            return session
        raise AssertionError(f"unexpected interface: {iface}")

    monkeypatch.setattr(rim.dbus, "Interface", _fake_interface)

    ri = RemoteInput(100, 100, bus=_FakeCreateBus())
    result = ri.create()

    assert result is None
    # dispose_remote_desktop_session's Start()-then-Stop() fallback ran,
    # proving this isn't just a bare (ineffective) Stop() call.
    assert session.start_calls == 1
    assert session.stop_calls == 2
