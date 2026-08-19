package com.tethrlink.net

/**
 * Builds the wire-format "hello" the client sends when it opens a
 * streaming connection.
 *
 * Layout: magic(6) + deviceId(16) + screenDims(8) + name(64, zero-padded)
 * = [FIXED_LEN] bytes, then whatever extension block the caller appends.
 *
 * The name field is fixed-width — not just the device model's raw bytes —
 * so the extension always starts at the same offset regardless of how
 * long the model string is. Before this, a short model name (e.g. "SM-X920",
 * 7 bytes) left the hello 57 bytes short of the 94 the server expects
 * before an extension, so the extension marker landed inside the name
 * field's variable tail instead of at a fixed offset and was parsed as
 * part of the name (see server_core.py: `hello[30:]` up to 64 bytes).
 *
 * The name is truncated to [NAME_FIELD_LEN] bytes first — so a device
 * model longer than that can never push the extension past the expected
 * offset — then right-padded with zero bytes out to exactly
 * [NAME_FIELD_LEN]. The server already strips trailing nulls and
 * truncates to 64 bytes itself
 * (`hello[30:].decode(...).strip("\x00")[:64]`), so an old server sees
 * exactly the same name it always did; only a new server that keeps
 * reading past offset [FIXED_LEN] sees the extension.
 */
object HelloMessage {
    const val NAME_FIELD_LEN = 64
    const val FIXED_LEN = 6 + 16 + 8 + NAME_FIELD_LEN // magic + deviceId + screenDims + name = 94

    fun build(
        magic: ByteArray,
        deviceId: ByteArray,
        screenDims: ByteArray,
        deviceName: ByteArray,
        extension: ByteArray,
    ): ByteArray {
        val nameField = ByteArray(NAME_FIELD_LEN) // zero-initialised: padding is implicit
        val copyLen = minOf(deviceName.size, NAME_FIELD_LEN)
        System.arraycopy(deviceName, 0, nameField, 0, copyLen)
        return magic + deviceId + screenDims + nameField + extension
    }
}
