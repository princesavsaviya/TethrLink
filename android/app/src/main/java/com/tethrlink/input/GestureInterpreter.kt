package com.tethrlink.input

/**
 * The pointer intent a gesture produces, in the order the caller should send
 * it. Coordinates are normalised `[0,1]` video-space values (see
 * [VideoGeometry]) — this class does no pixel geometry.
 */
sealed class PointerAction {
    data class Move(val x: Float, val y: Float) : PointerAction()
    data class Button(val button: Int, val pressed: Boolean) : PointerAction()
    data class Scroll(val dx: Float, val dy: Float) : PointerAction()
}

/**
 * Turns raw touch events into pointer intent for an absolute-pointer model
 * (§1, §6 of the touch input design): a finger down positions the pointer
 * and clicks, a hold converts the click to a right-click, and a second
 * finger switches to scrolling.
 *
 * Pure Kotlin, no Android imports, so it runs on the plain JVM. `longPressMs`
 * and `touchSlopPx` are supplied by the caller instead of being read from
 * `ViewConfiguration` here, precisely so this stays testable without a
 * device or Robolectric; the caller (a later task) is the one that owns an
 * Android `Context` and can read the platform values.
 *
 * ### Reconciling `touchSlopPx` with normalised coordinates
 *
 * [onDown] and [onMove] receive coordinates already normalised to `[0,1]` in
 * video space. `touchSlopPx` is named for its platform source —
 * `ViewConfiguration.getScaledTouchSlop()` — which *is* a pixel count on that
 * API. Comparing a raw pixel count against a normalised delta directly would
 * make the threshold meaningless: the same pixel slop is a huge fraction of
 * a phone's video rectangle and a tiny fraction of a 4K panel's.
 *
 * This class does not have the view's pixel dimensions available to it (and,
 * being pure Kotlin with no Android imports, has no way to obtain them), so
 * it cannot perform that conversion itself. The resolution is therefore the
 * second option the task calls out: **the caller converts before
 * construction** — e.g. `pixelSlop / viewWidthPx` — and passes an
 * already-normalised threshold in. This class stores the value unchanged
 * and compares it directly against normalised deltas; the "Px" in the
 * parameter name documents where the raw value originates, not the units it
 * is used in here.
 */
class GestureInterpreter(
    private val longPressMs: Long,
    private val touchSlopPx: Float,
) {

    /** Which button is currently held down, or `null` if none is. */
    private var heldButton: Int? = null

    /** True from `onDown` until either slop is exceeded or the deadline fires. */
    private var longPressPending = false

    /** True once a second pointer has turned this gesture into a scroll. Sticky
     *  for the rest of the gesture, so it can't flicker back into a drag if
     *  the pointer count momentarily drops. */
    private var isScrolling = false

    // Anchor for the slop check: fixed at the down position for the whole
    // gesture, never updated by intermediate moves.
    private var anchorX = 0f
    private var anchorY = 0f

    // Last reported position, used as the baseline for the next scroll delta.
    private var lastX = 0f
    private var lastY = 0f

    private var downTimeMs = 0L

    fun onDown(x: Float, y: Float, nowMs: Long): List<PointerAction> {
        anchorX = x
        anchorY = y
        lastX = x
        lastY = y
        downTimeMs = nowMs
        longPressPending = true
        isScrolling = false
        heldButton = InputCodec.BUTTON_LEFT

        return listOf(
            PointerAction.Move(x, y),
            PointerAction.Button(InputCodec.BUTTON_LEFT, true),
        )
    }

    fun onMove(x: Float, y: Float, nowMs: Long, pointerCount: Int): List<PointerAction> {
        if (pointerCount >= 2 || isScrolling) {
            val actions = mutableListOf<PointerAction>()

            if (!isScrolling) {
                // Entering scroll mode: release whatever is held first, so a
                // drag in progress doesn't turn into a text selection while
                // the user is trying to scroll with a second finger.
                isScrolling = true
                longPressPending = false
                heldButton?.let {
                    actions.add(PointerAction.Button(it, false))
                    heldButton = null
                }
            }

            actions.add(PointerAction.Scroll(x - lastX, y - lastY))
            lastX = x
            lastY = y
            return actions
        }

        // Single-pointer motion: this is absolute-pointer mode, so the
        // pointer always tracks the finger directly, whether or not slop has
        // been exceeded yet. Slop only gates the long press below.
        if (longPressPending) {
            val dx = x - anchorX
            val dy = y - anchorY
            if (dx * dx + dy * dy > touchSlopPx * touchSlopPx) {
                longPressPending = false
            }
        }

        lastX = x
        lastY = y
        return listOf(PointerAction.Move(x, y))
    }

    fun onUp(nowMs: Long): List<PointerAction> {
        val actions = releaseHeld()
        resetState()
        return actions
    }

    fun onCancel(): List<PointerAction> {
        val actions = releaseHeld()
        resetState()
        return actions
    }

    fun checkLongPress(nowMs: Long): List<PointerAction> {
        if (!longPressPending) return emptyList()
        if (nowMs - downTimeMs < longPressMs) return emptyList()

        // Consume the deadline first: whatever happens below, a second call
        // must not fire again.
        longPressPending = false

        val actions = mutableListOf<PointerAction>()
        heldButton?.let {
            actions.add(PointerAction.Button(it, false))
            heldButton = null
        }
        actions.add(PointerAction.Button(InputCodec.BUTTON_RIGHT, true))
        actions.add(PointerAction.Button(InputCodec.BUTTON_RIGHT, false))
        return actions
    }

    private fun releaseHeld(): List<PointerAction> {
        val button = heldButton ?: return emptyList()
        return listOf(PointerAction.Button(button, false))
    }

    private fun resetState() {
        heldButton = null
        longPressPending = false
        isScrolling = false
    }
}
