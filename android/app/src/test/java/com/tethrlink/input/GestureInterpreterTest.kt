package com.tethrlink.input

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * All coordinates here are normalised `[0,1]` video-space values, matching
 * what [VideoGeometry] produces — never raw pixels. `touchSlopPx` is passed
 * as `0.02f`; see [GestureInterpreter]'s class doc for why a value named for
 * pixels is used directly against normalised deltas in this test (the
 * caller is expected to have already converted it).
 *
 * Scroll deltas are computed by subtraction, so they are compared with a
 * float tolerance; everything else (positions and button state echoed back
 * verbatim from the call site) is compared for exact equality.
 */
class GestureInterpreterTest {

    private val longPressMs = 500L
    private val slop = 0.02f

    private fun interpreter() = GestureInterpreter(longPressMs, slop)

    @Test
    fun `down emits a move to that position then a left press, in that order`() {
        val g = interpreter()
        val actions = g.onDown(0.3f, 0.4f, 1_000L)
        assertEquals(
            listOf(
                PointerAction.Move(0.3f, 0.4f),
                PointerAction.Button(InputCodec.BUTTON_LEFT, true),
            ),
            actions,
        )
    }

    @Test
    fun `up releases the held left button`() {
        val g = interpreter()
        g.onDown(0.3f, 0.4f, 1_000L)
        val actions = g.onUp(1_050L)
        assertEquals(listOf(PointerAction.Button(InputCodec.BUTTON_LEFT, false)), actions)
    }

    @Test
    fun `movement beyond slop suppresses the pending long press`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        g.onMove(0.35f, 0.40f, 1_100L, 1) // dx = 0.05, well past the 0.02 slop
        val actions = g.checkLongPress(1_600L)
        assertEquals(emptyList<PointerAction>(), actions)
    }

    @Test
    fun `movement within slop does not suppress the pending long press`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        g.onMove(0.305f, 0.401f, 1_100L, 1) // dx,dy well inside the 0.02 slop
        val actions = g.checkLongPress(1_600L)
        assertEquals(
            listOf(
                PointerAction.Button(InputCodec.BUTTON_LEFT, false),
                PointerAction.Button(InputCodec.BUTTON_RIGHT, true),
                PointerAction.Button(InputCodec.BUTTON_RIGHT, false),
            ),
            actions,
        )
    }

    @Test
    fun `checkLongPress before the deadline does not fire`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        assertEquals(emptyList<PointerAction>(), g.checkLongPress(1_200L))
    }

    @Test
    fun `a long press releases left and issues a right press then release`() {
        val g = interpreter()
        val downActions = g.onDown(0.30f, 0.40f, 1_000L)
        val longPressActions = g.checkLongPress(1_500L)

        // The only left click in the whole gesture is the one from onDown;
        // the long press must not add a second one.
        assertEquals(1, downActions.count { it == PointerAction.Button(InputCodec.BUTTON_LEFT, true) })
        assertEquals(
            listOf(
                PointerAction.Button(InputCodec.BUTTON_LEFT, false),
                PointerAction.Button(InputCodec.BUTTON_RIGHT, true),
                PointerAction.Button(InputCodec.BUTTON_RIGHT, false),
            ),
            longPressActions,
        )
    }

    @Test
    fun `a second checkLongPress after firing does not fire again`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        val first = g.checkLongPress(1_500L)
        assertTrue(first.isNotEmpty())

        val second = g.checkLongPress(2_000L)
        assertEquals(emptyList<PointerAction>(), second)
    }

    @Test
    fun `up after a long press has fired emits nothing since nothing remains held`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        g.checkLongPress(1_500L)
        val actions = g.onUp(1_600L)
        assertEquals(emptyList<PointerAction>(), actions)
    }

    @Test
    fun `single pointer move beyond slop still tracks the finger, becoming a drag`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        val actions = g.onMove(0.50f, 0.60f, 1_100L, 1)
        assertEquals(listOf(PointerAction.Move(0.50f, 0.60f)), actions)
    }

    @Test
    fun `two pointers emit scroll and release the held button first`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        val actions = g.onMove(0.32f, 0.45f, 1_100L, 2)

        assertEquals(2, actions.size)
        assertEquals(PointerAction.Button(InputCodec.BUTTON_LEFT, false), actions[0])
        val scroll = actions[1] as PointerAction.Scroll
        assertEquals(0.02f, scroll.dx, 1e-4f)
        assertEquals(0.05f, scroll.dy, 1e-4f)
    }

    @Test
    fun `a second two pointer move only emits scroll, no repeated release`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        g.onMove(0.32f, 0.45f, 1_100L, 2)
        val actions = g.onMove(0.34f, 0.50f, 1_150L, 2)

        assertEquals(1, actions.size)
        val scroll = actions[0] as PointerAction.Scroll
        assertEquals(0.02f, scroll.dx, 1e-4f)
        assertEquals(0.05f, scroll.dy, 1e-4f)
    }

    @Test
    fun `checkLongPress does not fire once the gesture has become a scroll`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        g.onMove(0.32f, 0.45f, 1_100L, 2)
        val actions = g.checkLongPress(1_600L)
        assertEquals(emptyList<PointerAction>(), actions)
    }

    @Test
    fun `up after scrolling emits nothing since the button was already released`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        g.onMove(0.32f, 0.45f, 1_100L, 2)
        val actions = g.onUp(1_200L)
        assertEquals(emptyList<PointerAction>(), actions)
    }

    @Test
    fun `cancel releases the held left button`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        val actions = g.onCancel()
        assertEquals(listOf(PointerAction.Button(InputCodec.BUTTON_LEFT, false)), actions)
    }

    @Test
    fun `cancel with nothing held emits nothing`() {
        val g = interpreter()
        val actions = g.onCancel()
        assertEquals(emptyList<PointerAction>(), actions)
    }

    @Test
    fun `cancel after a long press has already fired emits nothing`() {
        val g = interpreter()
        g.onDown(0.30f, 0.40f, 1_000L)
        g.checkLongPress(1_500L)
        val actions = g.onCancel()
        assertEquals(emptyList<PointerAction>(), actions)
    }

    @Test
    fun `state does not leak from one gesture into the next`() {
        val g = interpreter()

        // First gesture: drag past slop, then release. Long press must not
        // have fired, and nothing should carry into the next gesture.
        g.onDown(0.30f, 0.40f, 1_000L)
        g.onMove(0.90f, 0.90f, 1_050L, 1)
        g.onUp(1_100L)

        // Second gesture, well separated in time: its own long press must be
        // free to fire on schedule, proving the deadline and slop anchor
        // reset rather than being inherited from the first gesture.
        g.onDown(0.10f, 0.10f, 5_000L)
        val actions = g.checkLongPress(5_500L)
        assertEquals(
            listOf(
                PointerAction.Button(InputCodec.BUTTON_LEFT, false),
                PointerAction.Button(InputCodec.BUTTON_RIGHT, true),
                PointerAction.Button(InputCodec.BUTTON_RIGHT, false),
            ),
            actions,
        )
    }
}
