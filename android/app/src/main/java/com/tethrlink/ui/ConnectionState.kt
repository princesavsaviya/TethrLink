package com.tethrlink.ui

// The 4 states of the connection setup flow, driven by MainActivityV2 and
// rendered by ConnectionScreen_OptionC.
sealed class ConnectionState {
    object NoUsb : ConnectionState()
    object TetherOff : ConnectionState()
    object Scanning : ConnectionState()
    data class ServerFound(val hostname: String, val ip: String, val system: String) : ConnectionState()
}
