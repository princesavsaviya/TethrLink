"""Scroll delta conversion.

Deltas arrive normalised, like pointer coordinates, so the wire format stays
resolution-independent. Feeding them straight to the compositor scrolled by
thousandths of a pixel, which is why scrolling appeared to do nothing.
"""

from server.core.remote_input import (
    AXIS_HORIZONTAL,
    AXIS_VERTICAL,
    SCROLL_DIRECTION,
    SCROLL_NOTCHES_PER_SCREEN,
    RemoteInput,
)


class FakeSession:
    def __init__(self):
        self.calls = []

    def NotifyPointerAxisDiscrete(self, axis, steps):
        self.calls.append((axis, steps))


def _ri():
    ri = RemoteInput.__new__(RemoteInput)
    ri._session = FakeSession()
    ri._scroll_accum_x = 0.0
    ri._scroll_accum_y = 0.0
    ri._held_buttons = set()
    return ri


def test_a_full_screen_drag_produces_the_configured_notches():
    ri = _ri()
    ri.axis(0.0, 1.0)
    axis, steps = ri._session.calls[0]
    assert axis == AXIS_VERTICAL
    assert abs(steps) == int(SCROLL_NOTCHES_PER_SCREEN)


def test_sub_notch_movement_is_carried_not_discarded():
    """The original bug in miniature: a delta too small to be one notch must
    accumulate rather than round away, or a slow drag never scrolls."""
    ri = _ri()
    tiny = (1.0 / SCROLL_NOTCHES_PER_SCREEN) / 4.0  # a quarter of one notch
    for _ in range(3):
        ri.axis(0.0, tiny)
    assert ri._session.calls == []       # still under one notch
    ri.axis(0.0, tiny)                   # the fourth completes it
    assert len(ri._session.calls) == 1


def test_direction_is_consistent_and_reverses():
    ri = _ri()
    ri.axis(0.0, 0.5)
    down = ri._session.calls[0][1]
    ri._session.calls.clear()
    ri.axis(0.0, -0.5)
    up = ri._session.calls[0][1]
    assert down * up < 0, "opposite drags must scroll opposite ways"
    # Sign follows SCROLL_DIRECTION, so flipping that constant flips both.
    assert (down < 0) == (SCROLL_DIRECTION < 0)


def test_horizontal_uses_the_horizontal_axis():
    ri = _ri()
    ri.axis(0.5, 0.0)
    assert ri._session.calls[0][0] == AXIS_HORIZONTAL


def test_zero_delta_dispatches_nothing():
    ri = _ri()
    ri.axis(0.0, 0.0)
    assert ri._session.calls == []


def test_no_session_is_a_no_op():
    ri = _ri()
    ri._session = None
    ri.axis(0.0, 1.0)   # must not raise


def test_reset_clears_a_partial_notch():
    ri = _ri()
    ri.axis(0.0, (1.0 / SCROLL_NOTCHES_PER_SCREEN) * 0.9)   # 90% of a notch
    ri.reset_scroll()
    ri.axis(0.0, (1.0 / SCROLL_NOTCHES_PER_SCREEN) * 0.9)   # would complete it if carried
    assert ri._session.calls == [], "a stale partial notch leaked into a new gesture"


def test_a_dbus_failure_is_swallowed():
    ri = _ri()

    class Broken:
        def NotifyPointerAxisDiscrete(self, axis, steps):
            raise RuntimeError("bus went away")

    ri._session = Broken()
    ri.axis(0.0, 1.0)   # must not raise
