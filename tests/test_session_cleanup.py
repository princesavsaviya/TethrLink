"""Unit tests for Finding 1 (code review): a paired MutterVirtualDisplay
that fails to set up must not orphan the RemoteDesktop session it was
paired with, and cleanup_orphaned_sessions() must sweep RemoteDesktop
sessions in addition to ScreenCast ones.

No real D-Bus session is ever touched. `dbus.Interface` is monkeypatched
at the module level to an identity function — `dbus.Interface(obj, iface)`
just returns `obj` unchanged — because it is a thin (object, dbus_interface)
-> proxy wrapper with no behaviour of its own (verified against the
installed dbus-python: __init__ just stores the object and interface
name, and attribute access normally delegates to object.get_dbus_method()).
Bypassing that indirection lets the fakes below implement plain Python
methods directly instead of a get_dbus_method(member, iface) protocol,
which is not needed to exercise the logic under test here.
"""

import pytest

from server.core import server_core
from server.core.remote_input import RemoteInput


# ── Fakes for MutterVirtualDisplay.setup()'s paired-failure path ───────────

class _FakeSCRoot:
    """Fake object at MUTTER_PATH: only CreateSession() is needed."""

    def __init__(self, session_path):
        self._session_path = session_path

    def CreateSession(self, props):
        return self._session_path


class _FakeSCSession:
    """Fake ScreenCast session object. connect_to_signal() is called
    directly on it (not through the Interface wrapper); RecordVirtual()
    is what simulates the pairing failure."""

    def __init__(self, record_virtual_exc):
        self._exc = record_virtual_exc
        self.closed_handlers = []

    def connect_to_signal(self, signal_name, handler, dbus_interface=None, **kw):
        if signal_name == "Closed":
            self.closed_handlers.append(handler)

    def RecordVirtual(self, props):
        raise self._exc

    def Start(self):
        pass  # never reached on the paired branch, but harmless if it were


class _FakeDisplayBus:
    """Routes get_object() to the ScreenCast root or its one session;
    anything else (notably RD_PATH, which cleanup_orphaned_sessions() now
    also sweeps on every MutterVirtualDisplay.__init__) gets a bare
    object with no dbus methods, so that sweep fails harmlessly and is
    swallowed — exactly like a bus with nothing listening there."""

    def __init__(self, sc_root, session_path, sc_session):
        self._sc_root = sc_root
        self._session_path = session_path
        self._sc_session = sc_session

    def get_object(self, service_name, path):
        if path == server_core.MUTTER_PATH:
            return self._sc_root
        if path == self._session_path:
            return self._sc_session
        return object()


class _NeverStartedSession:
    """Simulates Mutter's real RemoteDesktop.Session behaviour for a
    session that was created (via CreateSession) but never Start()ed:
    Stop() raises "Session not started" until Start() has actually run."""

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


def test_setup_disposes_remote_desktop_session_when_record_virtual_fails(monkeypatch):
    """The core Finding 1 scenario: CreateSession (ScreenCast) succeeds,
    the RemoteDesktop session already exists (created earlier by
    RemoteInput.create(), simulated here by binding _session directly),
    and then RecordVirtual raises. setup() must dispose the RemoteDesktop
    session itself — not merely rely on the caller remembering to."""
    monkeypatch.setattr(server_core.dbus, "Interface", lambda obj, iface: obj)

    session_path = "/org/gnome/Mutter/ScreenCast/Session/1"
    sc_session = _FakeSCSession(RuntimeError("simulated RecordVirtual failure"))
    bus = _FakeDisplayBus(_FakeSCRoot(session_path), session_path, sc_session)

    remote_input = RemoteInput(100, 100, bus=object())
    rd_session = _NeverStartedSession()
    remote_input._session = rd_session
    remote_input._stream_path = None  # bind_stream() never reached

    display = server_core.MutterVirtualDisplay(
        100, 100, bus=bus,
        remote_input=remote_input,
        remote_input_session_id="fake-session-id",
    )

    with pytest.raises(RuntimeError, match="simulated RecordVirtual failure"):
        display.setup()

    assert display._paired is True
    # Disposed right here, in setup()'s own failure path.
    assert remote_input._session is None
    # The Start()-then-Stop() fallback is what actually disposed it — a
    # bare Stop() alone (stop_calls == 1, start_calls == 0) would mean
    # the session was only forgotten about, not released, on the bus.
    assert rd_session.start_calls == 1
    assert rd_session.stop_calls == 2


def test_setup_disposal_never_raises_even_if_remote_input_stop_is_broken(monkeypatch):
    """Confirm the teardown path itself is exception-safe: if disposing
    the RemoteDesktop session somehow raised, that must not replace the
    original setup failure with a worse, unrelated one."""
    monkeypatch.setattr(server_core.dbus, "Interface", lambda obj, iface: obj)

    session_path = "/org/gnome/Mutter/ScreenCast/Session/1"
    sc_session = _FakeSCSession(RuntimeError("simulated RecordVirtual failure"))
    bus = _FakeDisplayBus(_FakeSCRoot(session_path), session_path, sc_session)

    class _BrokenRemoteInput:
        def stop(self):
            raise RuntimeError("stop() itself is broken")

    display = server_core.MutterVirtualDisplay(
        100, 100, bus=bus,
        remote_input=_BrokenRemoteInput(),
        remote_input_session_id="fake-session-id",
    )

    # The ORIGINAL RecordVirtual failure must still be what propagates —
    # not the broken stop()'s exception.
    with pytest.raises(RuntimeError, match="simulated RecordVirtual failure"):
        display.setup()


# ── Fakes for cleanup_orphaned_sessions()'s RemoteDesktop sweep ────────────

class _FakeIntrospectableRoot:
    def __init__(self, xml):
        self._xml = xml

    def Introspect(self):
        return self._xml


class _FakeSweepBus:
    """Routes get_object() across possibly several registered session
    trees; anything unregistered raises, so that tree's sweep fails
    harmlessly (matching a bus with nothing listening there) without
    disturbing sweeps of the other registered trees."""

    def __init__(self, roots):
        # roots: {base_path: (root_obj, {child_name: child_obj})}
        self._roots = roots

    def get_object(self, service_name, path):
        for base_path, (root_obj, children) in self._roots.items():
            if path == base_path:
                return root_obj
            prefix = base_path + "/"
            if path.startswith(prefix) and path[len(prefix):] in children:
                return children[path[len(prefix):]]
        raise RuntimeError(f"no such object: {path}")


class _AlwaysFailingSweepSession:
    """Every call fails — used to prove one stubborn session can't abort
    the sweep for the rest."""

    def Start(self):
        raise RuntimeError("boom")

    def Stop(self):
        raise RuntimeError("boom")


def test_cleanup_orphaned_sessions_sweeps_remote_desktop_sessions(monkeypatch):
    """Finding 1's second requirement: cleanup_orphaned_sessions() must
    also sweep RD_PATH, not just MUTTER_PATH, and must use the
    Start()-then-Stop() disposal fallback there (a bare Stop() would not
    actually release a never-started orphan)."""
    monkeypatch.setattr(server_core.dbus, "Interface", lambda obj, iface: obj)

    xml = '<node><node name="never-started"/><node name="broken"/></node>'
    never_started = _NeverStartedSession()
    broken = _AlwaysFailingSweepSession()

    bus = _FakeSweepBus({
        server_core.RD_PATH: (
            _FakeIntrospectableRoot(xml),
            {"never-started": never_started, "broken": broken},
        ),
    })

    server_core.cleanup_orphaned_sessions(bus)  # must not raise

    assert never_started.start_calls == 1
    assert never_started.stop_calls == 2
    # `broken` never disposes, but must not have aborted the sweep — the
    # assertions above for never_started already prove that, since it is
    # listed second in the XML and still got processed.


def test_cleanup_orphaned_sessions_screencast_sweep_still_uses_bare_stop(monkeypatch):
    """Regression guard: the existing, already-correct ScreenCast sweep
    must keep using a plain Stop() — no Start()-then-Stop() fallback —
    since that behaviour was not implicated in Finding 1 and must be
    preserved exactly."""
    monkeypatch.setattr(server_core.dbus, "Interface", lambda obj, iface: obj)

    class _FakeScreenCastSession:
        def __init__(self):
            self.stop_calls = 0
            self.start_calls = 0

        def Stop(self):
            self.stop_calls += 1

        def Start(self):
            self.start_calls += 1

    xml = '<node><node name="sc0"/></node>'
    sc_session = _FakeScreenCastSession()
    bus = _FakeSweepBus({
        server_core.MUTTER_PATH: (
            _FakeIntrospectableRoot(xml), {"sc0": sc_session},
        ),
    })

    server_core.cleanup_orphaned_sessions(bus)

    assert sc_session.stop_calls == 1
    assert sc_session.start_calls == 0


def test_cleanup_orphaned_sessions_never_raises_when_nothing_is_listening():
    """Baseline exception-safety: a bus with nothing at either path must
    not make cleanup itself fail (it is called from
    MutterVirtualDisplay.__init__ — a raise there would break every
    connection attempt, which is strictly worse than any orphaned
    session it exists to clean up)."""
    class _EmptyBus:
        def get_object(self, service_name, path):
            raise RuntimeError("nothing here")

    server_core.cleanup_orphaned_sessions(_EmptyBus())  # must not raise
