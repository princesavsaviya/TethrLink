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
