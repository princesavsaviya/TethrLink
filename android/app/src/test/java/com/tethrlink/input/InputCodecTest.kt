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
