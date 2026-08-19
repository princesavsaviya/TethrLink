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
