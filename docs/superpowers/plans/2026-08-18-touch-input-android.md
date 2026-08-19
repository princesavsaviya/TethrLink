# Touch Input — Android Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture touches on the tablet and send them to the server so they drive the PC's pointer, completing the 2.0.0 feature.

**Architecture:** Pure Kotlin logic — coordinate normalisation, message encoding, gesture recognition — in plain classes with JVM unit tests that run without a device. The Android-framework layer (touch listener, lifecycle, socket) stays thin and calls into that logic.

**Tech Stack:** Kotlin, Android SDK 35 / minSdk 21, JUnit for JVM tests, gradle.

## Global Constraints

- The server side is **already complete and verified**. This plan changes only `android/`.
- **Coordinates are sent normalised to `[0,1]` in video space**, never as pixels. Only the client knows its own rendering geometry, and the two codecs render differently (below). The server multiplies by the stream size.
- **Never send input before the server confirms support.** An old server never reads from the client, so unsolicited input fills the socket buffer and blocks the client. Support is confirmed by the `TLOK2_` reply.
- Run Android unit tests with `cd android && ./gradlew test`. The Android SDK is at `~/Android/Sdk`; export `ANDROID_HOME` if gradle needs it.
- `adb` is available with the user's tablet attached. **Do not install a build to the device without saying so in your report** — it replaces the app they use.
- Do not change the video decode path, `StreamDecoder.kt`'s NAL handling, or the existing handshake fields. Touch is additive.

## Verified Wire Format

The server (already implemented) expects the client to append this **after** the existing 94-byte hello:

```
"TLX1"        4 bytes, literal marker
cap_len       1 byte, number of capability bytes that follow
capabilities  cap_len bytes; bit 0x01 = CAP_POINTER_INPUT
```

So advertising pointer input is exactly: `54 4C 58 31 01 01`.

The server replies:
- `TLOK2_` — input negotiated, send input freely
- `TLOK__` — no input (old server, or the user has touch disabled). **Send nothing.**

In both cases the following 9 bytes are the unchanged `(width, height, codec)` struct — big-endian `IIB`. Codec `1` = H.264, `2` = JPEG.

Input messages, client → server:

```
type:u8  length:u8  payload[length]
```

| Type | Payload | Meaning |
|---|---|---|
| `0x01` | `x:f32, y:f32` big-endian | pointer motion, normalised `[0,1]` |
| `0x02` | `button:u8, state:u8` | button; 0=left 1=right 2=middle, state 1=press 0=release |
| `0x03` | `dx:f32, dy:f32` big-endian | scroll axis |

Big-endian matches Java's `DataOutputStream` and `ByteBuffer` defaults, so no byte-order juggling is needed.

## Interaction Model (decided in the spec)

- Finger down → move pointer there, then press left. Finger up → release. A tap is a click; a drag is a drag.
- Long press → right click instead.
- Two-finger drag → scroll.
- **Timings come from the platform**, not constants: `ViewConfiguration.getLongPressTimeout()` (~500 ms) and `ViewConfiguration.get(ctx).scaledTouchSlop` (~8 dp). These match every other app on the device, scale with density, and honour accessibility settings.
- **Every touch clicks — there is no hover.** Accepted and inherent to absolute-pointer mode.

---

### Task 1: Coordinate normalisation

The subtle part, and pure logic — so it gets real tests.

**Files:**
- Create: `android/app/src/main/java/com/tethrlink/input/VideoGeometry.kt`
- Test: `android/app/src/test/java/com/tethrlink/input/VideoGeometryTest.kt`

**Interfaces:**
- Produces: `data class NormalisedPoint(val x: Float, val y: Float)`;
  `object VideoGeometry` with
  `fun normalise(touchX: Float, touchY: Float, viewW: Int, viewH: Int, videoW: Int, videoH: Int, letterboxed: Boolean): NormalisedPoint?`

The two codecs render differently and the mapping **must** account for it:

| Codec | Rendering | Mapping |
|---|---|---|
| H.264 | MediaCodec → `SurfaceView`, fills the view | the whole view is video |
| JPEG | `Canvas.drawFrame`, `scale = minOf(canW/bmpW, canH/bmpH)`, centred | black bars are **not** video |

For the letterboxed case, compute the drawn rectangle exactly as `drawFrame` does, subtract the offset, and divide by the drawn size. A touch landing on a black bar is **outside the video** and must return `null` — it is not a touch on the desktop, and clamping it would silently click the wrong place.

- [ ] **Step 1: Write the failing test**

Create `android/app/src/test/java/com/tethrlink/input/VideoGeometryTest.kt`:

```kotlin
package com.tethrlink.input

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VideoGeometryTest {

    // ── H.264: the surface fills the view, so the whole view is video ────────

    @Test
    fun `fills view maps corners to unit square`() {
        val tl = VideoGeometry.normalise(0f, 0f, 2960, 1848, 1730, 1080, false)!!
        assertEquals(0f, tl.x, 1e-4f)
        assertEquals(0f, tl.y, 1e-4f)

        val br = VideoGeometry.normalise(2960f, 1848f, 2960, 1848, 1730, 1080, false)!!
        assertEquals(1f, br.x, 1e-4f)
        assertEquals(1f, br.y, 1e-4f)
    }

    @Test
    fun `fills view maps centre to centre`() {
        val c = VideoGeometry.normalise(1480f, 924f, 2960, 1848, 1730, 1080, false)!!
        assertEquals(0.5f, c.x, 1e-4f)
        assertEquals(0.5f, c.y, 1e-4f)
    }

    // ── JPEG: letterboxed, so black bars are not part of the video ───────────

    @Test
    fun `letterboxed ignores the bars and maps the drawn rect`() {
        // 16:9 video in a 16:10 view -> pillarbox/letterbox top and bottom.
        // scale = min(2960/1920, 1848/1080) = min(1.5417, 1.7111) = 1.5417
        // drawn = 1920*1.5417 x 1080*1.5417 = 2960 x 1665
        // offsetY = (1848 - 1665) / 2 = 91.5
        val top = VideoGeometry.normalise(1480f, 91.5f, 2960, 1848, 1920, 1080, true)!!
        assertEquals(0.5f, top.x, 1e-3f)
        assertEquals(0f, top.y, 1e-3f)

        val bottom = VideoGeometry.normalise(1480f, 1756.5f, 2960, 1848, 1920, 1080, true)!!
        assertEquals(1f, bottom.y, 1e-3f)
    }

    @Test
    fun `touch on a black bar is rejected rather than clamped`() {
        // Above the drawn rect (offsetY = 91.5): not a touch on the desktop.
        assertNull(VideoGeometry.normalise(1480f, 10f, 2960, 1848, 1920, 1080, true))
        // Below it.
        assertNull(VideoGeometry.normalise(1480f, 1840f, 2960, 1848, 1920, 1080, true))
    }

    @Test
    fun `letterboxed centre is still the centre`() {
        val c = VideoGeometry.normalise(1480f, 924f, 2960, 1848, 1920, 1080, true)!!
        assertEquals(0.5f, c.x, 1e-3f)
        assertEquals(0.5f, c.y, 1e-3f)
    }

    // ── robustness ──────────────────────────────────────────────────────────

    @Test
    fun `out of range touch is clamped when the video fills the view`() {
        val p = VideoGeometry.normalise(-50f, 5000f, 2960, 1848, 1730, 1080, false)!!
        assertEquals(0f, p.x, 1e-4f)
        assertEquals(1f, p.y, 1e-4f)
    }

    @Test
    fun `zero sized view is rejected`() {
        assertNull(VideoGeometry.normalise(10f, 10f, 0, 1848, 1730, 1080, false))
        assertNull(VideoGeometry.normalise(10f, 10f, 2960, 0, 1730, 1080, false))
    }

    @Test
    fun `zero sized video is rejected`() {
        assertNull(VideoGeometry.normalise(10f, 10f, 2960, 1848, 0, 1080, true))
    }
}
```

- [ ] **Step 2: Run the test and confirm it fails**

```bash
cd android && ./gradlew test --tests '*VideoGeometryTest*'
```
Expected: compilation failure — `VideoGeometry` does not exist.

If gradle cannot find the SDK, `export ANDROID_HOME=$HOME/Android/Sdk` first. If a `local.properties` with `sdk.dir` is needed, create it — it is gitignored.

- [ ] **Step 3: Implement**

Create `android/app/src/main/java/com/tethrlink/input/VideoGeometry.kt`:

```kotlin
package com.tethrlink.input

/** A touch position expressed in video space, both axes in [0,1]. */
data class NormalisedPoint(val x: Float, val y: Float)

/**
 * Maps a touch on the panel to a position within the video.
 *
 * Coordinates go to the server normalised rather than as pixels, because only
 * this side knows how the video is laid out on screen — and the two codecs lay
 * it out differently. H.264 renders through MediaCodec into a SurfaceView that
 * fills the view, so the whole view is video. JPEG draws through a Canvas with
 * `scale = minOf(...)`, so it is letterboxed and the bars are not video at all.
 *
 * Sending pixels would force the server to know which codec is on screen and
 * would break the moment rendering changed.
 */
object VideoGeometry {

    fun normalise(
        touchX: Float,
        touchY: Float,
        viewW: Int,
        viewH: Int,
        videoW: Int,
        videoH: Int,
        letterboxed: Boolean,
    ): NormalisedPoint? {
        if (viewW <= 0 || viewH <= 0 || videoW <= 0 || videoH <= 0) return null

        if (!letterboxed) {
            // The surface is stretched to fill the view, so the mapping is
            // simply the fraction across the view. Out-of-range values are
            // clamped: a finger sliding off the edge should pin to the edge.
            return NormalisedPoint(
                clamp01(touchX / viewW),
                clamp01(touchY / viewH),
            )
        }

        // Mirror Canvas drawFrame's fit-inside maths exactly.
        val scale = minOf(viewW.toFloat() / videoW, viewH.toFloat() / videoH)
        val drawnW = videoW * scale
        val drawnH = videoH * scale
        val offsetX = (viewW - drawnW) / 2f
        val offsetY = (viewH - drawnH) / 2f

        val vx = (touchX - offsetX) / drawnW
        val vy = (touchY - offsetY) / drawnH

        // A touch on a black bar is not a touch on the desktop. Rejecting is
        // right; clamping would silently click somewhere the user never aimed.
        // The epsilon absorbs float error exactly at the boundary.
        val eps = 1e-3f
        if (vx < -eps || vx > 1f + eps || vy < -eps || vy > 1f + eps) return null

        return NormalisedPoint(clamp01(vx), clamp01(vy))
    }

    private fun clamp01(v: Float): Float = when {
        v < 0f -> 0f
        v > 1f -> 1f
        else -> v
    }
}
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
cd android && ./gradlew test --tests '*VideoGeometryTest*'
```
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/tethrlink/input/VideoGeometry.kt android/app/src/test/java/com/tethrlink/input/VideoGeometryTest.kt
git commit -m "feat(android): normalise touch coordinates to video space

The two codecs render differently — H.264 fills the surface while JPEG
letterboxes — so only the client can map a touch to a position in the video.
A touch on a black bar is rejected rather than clamped, since it is not a
touch on the desktop at all."
```

---

### Task 2: Input message encoding

**Files:**
- Create: `android/app/src/main/java/com/tethrlink/input/InputCodec.kt`
- Test: `android/app/src/test/java/com/tethrlink/input/InputCodecTest.kt`

**Interfaces:**
- Produces: `object InputCodec` with `EXT_BLOCK: ByteArray`,
  `fun motion(x: Float, y: Float): ByteArray`,
  `fun button(button: Int, pressed: Boolean): ByteArray`,
  `fun axis(dx: Float, dy: Float): ByteArray`;
  constants `BUTTON_LEFT = 0`, `BUTTON_RIGHT = 1`, `BUTTON_MIDDLE = 2`.

- [ ] **Step 1: Write the failing test**

Create `android/app/src/test/java/com/tethrlink/input/InputCodecTest.kt`:

```kotlin
package com.tethrlink.input

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.nio.ByteBuffer

class InputCodecTest {

    @Test
    fun `extension block advertises pointer input`() {
        // "TLX1" + cap_len(1) + CAP_POINTER_INPUT(0x01)
        assertArrayEquals(
            byteArrayOf(0x54, 0x4C, 0x58, 0x31, 0x01, 0x01),
            InputCodec.EXT_BLOCK,
        )
    }

    @Test
    fun `motion frames type length and big endian floats`() {
        val f = InputCodec.motion(0.25f, 0.75f)
        assertEquals(10, f.size)          // 2 header + 8 payload
        assertEquals(0x01.toByte(), f[0]) // MSG_POINTER_MOTION
        assertEquals(8.toByte(), f[1])
        val bb = ByteBuffer.wrap(f, 2, 8) // ByteBuffer is big-endian by default
        assertEquals(0.25f, bb.float, 1e-6f)
        assertEquals(0.75f, bb.float, 1e-6f)
    }

    @Test
    fun `button frames press and release`() {
        val press = InputCodec.button(InputCodec.BUTTON_LEFT, true)
        assertArrayEquals(byteArrayOf(0x02, 0x02, 0x00, 0x01), press)

        val release = InputCodec.button(InputCodec.BUTTON_RIGHT, false)
        assertArrayEquals(byteArrayOf(0x02, 0x02, 0x01, 0x00), release)
    }

    @Test
    fun `axis frames scroll deltas`() {
        val f = InputCodec.axis(-1.5f, 2f)
        assertEquals(10, f.size)
        assertEquals(0x03.toByte(), f[0])
        val bb = ByteBuffer.wrap(f, 2, 8)
        assertEquals(-1.5f, bb.float, 1e-6f)
        assertEquals(2f, bb.float, 1e-6f)
    }

    @Test
    fun `every frame declares its own payload length`() {
        // The server skips unknown types using this byte, so it must always
        // equal the real payload size.
        listOf(
            InputCodec.motion(0f, 0f),
            InputCodec.button(InputCodec.BUTTON_LEFT, true),
            InputCodec.axis(0f, 0f),
        ).forEach { f ->
            assertEquals(f.size - 2, f[1].toInt())
        }
    }
}
```

- [ ] **Step 2: Run and confirm it fails**

```bash
cd android && ./gradlew test --tests '*InputCodecTest*'
```

- [ ] **Step 3: Implement**

Create `android/app/src/main/java/com/tethrlink/input/InputCodec.kt`:

```kotlin
package com.tethrlink.input

import java.nio.ByteBuffer

/**
 * Wire format for pointer input, client -> server.
 *
 * Framing is `type:u8 length:u8 payload[length]`. The explicit length lets the
 * server skip a message type it does not recognise instead of losing sync, so
 * new message types can be added later without breaking an older peer.
 *
 * Multi-byte values are big-endian, which is also ByteBuffer's default, so no
 * byte-order juggling is needed on either side.
 */
object InputCodec {

    private const val MSG_MOTION: Byte = 0x01
    private const val MSG_BUTTON: Byte = 0x02
    private const val MSG_AXIS: Byte = 0x03

    const val BUTTON_LEFT = 0
    const val BUTTON_RIGHT = 1
    const val BUTTON_MIDDLE = 2

    /**
     * Appended after the fixed 94-byte hello to advertise pointer input.
     *
     * Safe against older servers: they read exactly 94 bytes and never read
     * further, so these extra bytes are simply discarded with the socket.
     */
    val EXT_BLOCK: ByteArray = byteArrayOf(
        0x54, 0x4C, 0x58, 0x31, // "TLX1"
        0x01,                   // cap_len
        0x01,                   // CAP_POINTER_INPUT
    )

    fun motion(x: Float, y: Float): ByteArray = frame(MSG_MOTION) { it.putFloat(x).putFloat(y) }

    fun axis(dx: Float, dy: Float): ByteArray = frame(MSG_AXIS) { it.putFloat(dx).putFloat(dy) }

    fun button(button: Int, pressed: Boolean): ByteArray =
        byteArrayOf(MSG_BUTTON, 2, button.toByte(), if (pressed) 1 else 0)

    private inline fun frame(type: Byte, fill: (ByteBuffer) -> Unit): ByteArray {
        val payload = ByteBuffer.allocate(8)
        fill(payload)
        val out = ByteArray(2 + 8)
        out[0] = type
        out[1] = 8
        System.arraycopy(payload.array(), 0, out, 2, 8)
        return out
    }
}
```

- [ ] **Step 4: Run and confirm it passes** — 5 tests.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/tethrlink/input/InputCodec.kt android/app/src/test/java/com/tethrlink/input/InputCodecTest.kt
git commit -m "feat(android): encode input messages and the capability block"
```

---

### Task 3: Gesture recognition

Turns raw touch events into pointer intent. Pure logic driven by injected timing so it is testable without a device.

**Files:**
- Create: `android/app/src/main/java/com/tethrlink/input/GestureInterpreter.kt`
- Test: `android/app/src/test/java/com/tethrlink/input/GestureInterpreterTest.kt`

**Interfaces:**
- Produces: `sealed class PointerAction` with `Move(x, y)`, `Button(button, pressed)`, `Scroll(dx, dy)`;
  `class GestureInterpreter(longPressMs: Long, touchSlopPx: Float)` with
  `fun onDown(x, y, nowMs): List<PointerAction>`,
  `fun onMove(x, y, nowMs, pointerCount: Int): List<PointerAction>`,
  `fun onUp(nowMs): List<PointerAction>`,
  `fun onCancel(): List<PointerAction>`,
  `fun checkLongPress(nowMs): List<PointerAction>`.

Behaviour required:
- **Down** emits a move to that position, then a left press.
- **Move within slop** before the long-press deadline does not cancel the long press; movement beyond slop does, and the gesture becomes a drag.
- **Long press** (no movement beyond slop, `longPressMs` elapsed) releases left and issues a right press/release instead.
- **Up** releases whatever is held.
- **Two pointers** switch to scroll: emit `Scroll` deltas rather than motion, and release any held button first so a drag does not turn into a selection.
- **Cancel** releases everything held — the app losing focus must never leave a button down.

Timings are **passed in** rather than read from `ViewConfiguration` here, so the logic is testable on the JVM; the caller supplies the platform values.

- [ ] **Step 1: Write tests first** covering: down emits move-then-press; up emits release; movement beyond slop suppresses the long press; a long press with no movement converts to a right click and does not also emit a left click; two pointers emit scroll and release a held button first; cancel releases everything; and a second `checkLongPress` after one has fired does not fire twice.

- [ ] **Step 2: Run and confirm failure.**

- [ ] **Step 3: Implement** to satisfy the tests.

- [ ] **Step 4: Run and confirm pass.**

- [ ] **Step 5: Commit.**

---

### Task 4: Negotiate and send

Wires the pure logic into the existing connection.

**Files:**
- Modify: `android/app/src/main/java/com/tethrlink/MainActivityV2.kt`

- [ ] **Step 1: Advertise support in the hello**

Append `InputCodec.EXT_BLOCK` to the existing hello write. The current line is:

```kotlin
socket.getOutputStream().write(MAGIC_HELLO + DEVICE_ID + screenDims + deviceName)
```

- [ ] **Step 2: Detect the reply**

The client reads a fixed 6-byte magic. Add `MAGIC_OK2 = "TLOK2_"` and treat it exactly like `MAGIC_OK` for streaming purposes, recording that input is supported. **Anything other than `TLOK2_` means send no input at all** — an old server never reads from the client, so unsolicited input would fill the socket buffer and block.

- [ ] **Step 3: Send input**

Add a small sender that writes an encoded frame to the socket's output stream. Guard every write so a failure cannot kill the video read loop, and serialise writes — the stream is shared.

- [ ] **Step 4: Hook up touch**

Set a touch listener on `surfaceView`, feed events through `GestureInterpreter`, normalise via `VideoGeometry` — `letterboxed = (codec == JPEG)`, using the codec from the handshake and the video dimensions the server reported — and send the resulting actions. Post a delayed check for the long-press deadline, since no touch event arrives while a finger rests still.

Read `ViewConfiguration.getLongPressTimeout()` and `ViewConfiguration.get(context).scaledTouchSlop` and pass them in.

- [ ] **Step 5: Build and confirm it compiles**

```bash
cd android && ./gradlew assembleDebug
```

- [ ] **Step 6: Commit.**

---

### Task 5: Suspend input when the app is not in front

**Files:**
- Modify: `android/app/src/main/java/com/tethrlink/MainActivityV2.kt`

Cooperative controls from the spec: they prevent accidents, and are not a security boundary since a modified client could ignore them.

- [ ] **Step 1:** Stop sending input when the activity is not resumed — backgrounded, split-screen (`isInMultiWindowMode`), or the screen locked.
- [ ] **Step 2:** On suspension, send a release for anything held and clear gesture state, so no button is ever left down on the PC.
- [ ] **Step 3:** Resume cleanly, with no stale gesture state carried across.
- [ ] **Step 4:** Verify with `adb shell input keyevent KEYCODE_HOME` mid-drag that a release was sent.
- [ ] **Step 5: Commit.**

---

### Task 6: End-to-end verification

- [ ] **Step 1:** `cd android && ./gradlew test` — all JVM tests pass.
- [ ] **Step 2:** `./gradlew assembleDebug` succeeds.
- [ ] **Step 3:** **Ask before installing.** Installing replaces the app the user relies on. With consent: `adb install -r app/build/outputs/apk/debug/app-debug.apk`.
- [ ] **Step 4:** With the server running and `TETHRLINK_TOUCH=1`, confirm the log shows input negotiated and the reply was `TLOK2_`.
- [ ] **Step 5:** Confirm on hardware: dragging a finger moves the PC pointer on the virtual display; a tap clicks; a long press opens a context menu; a two-finger drag scrolls.
- [ ] **Step 6:** Confirm touch does **not** work when the server toggle is off — the reply should be `TLOK__` and the client should send nothing.
- [ ] **Step 7:** Confirm video is unaffected: `dropped` and `overflows` stay at zero while input flows.
- [ ] **Step 8:** Record results in the verification log and commit.

---

## Out of Scope

- Real multi-touch (`NotifyTouchDown`/`Motion`/`Up`) — a separate capability, not a fix to this one.
- Keyboard (2.2.0) and audio (2.1.0).
- An on-screen indicator that input is active.
- Aspect correction for the H.264 SurfaceView. Edge-aligned geometry already matches the panel's aspect, so there is nothing to correct today; it would only matter if a user forced a mismatched `TETHRLINK_RES`.
