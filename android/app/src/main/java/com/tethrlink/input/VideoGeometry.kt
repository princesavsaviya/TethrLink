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
