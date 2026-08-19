package com.tethrlink

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ActivityInfo
import android.graphics.Canvas
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.view.MotionEvent
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.platform.ComposeView
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import android.util.Base64
import org.json.JSONObject
import com.tethrlink.input.GestureInterpreter
import com.tethrlink.input.InputCodec
import com.tethrlink.input.PointerAction
import com.tethrlink.input.VideoGeometry
import com.tethrlink.net.HelloMessage
import com.tethrlink.ui.ConnectionState
import com.tethrlink.ui.ConnectionScreen_OptionC
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.DataInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.Socket

/**
 * MainActivityV2 — drives ConnectionScreen_OptionC instead of four
 * individual screens. All streaming / discovery logic is unchanged.
 *
 * To activate: change android:name in AndroidManifest.xml to
 * ".MainActivityV2" (or swap with MainActivity there).
 */
class MainActivityV2 : AppCompatActivity() {

    // ── Network constants ─────────────────────────────────────────────────────
    private val DEFAULT_SERVER_PORT   = 51137
    private val DISCOVERY_PORT        = 8765

    // ── Timeout constants ─────────────────────────────────────────────────────
    private val CONNECT_TIMEOUT_MS    = 5000
    private val STREAM_TIMEOUT_MS     = 3000
    private val SURFACE_TIMEOUT_MS    = 5000L
    private val SILENT_FRAME_LIMIT_MS = 3000L
    private val PRE_STREAM_DELAY_MS   = 500L
    private val READ_BUF_SIZE         = 1024 * 1024

    // ── Protocol magic bytes ──────────────────────────────────────────────────
    private val MAGIC_HELLO = "TLHELO".toByteArray()
    private val MAGIC_OK    = "TLOK__".toByteArray()
    private val MAGIC_OK2   = "TLOK2_".toByteArray() // OK + server negotiated pointer input
    private val MAGIC_BUSY  = "TLBUSY".toByteArray()

    // ── Touch input ───────────────────────────────────────────────────────────
    // Polling interval for the long-press deadline: no touch event arrives
    // while a finger rests still, so this has to be checked on a timer.
    private val LONG_PRESS_POLL_INTERVAL_MS = 50L

    // ── Device identity ───────────────────────────────────────────────────────
    private val DEVICE_ID: ByteArray by lazy { getOrCreateDeviceId() }

    private lateinit var composeUiContainer: ComposeView

    // Single state drives all 4 setup screens — replaces the Int(1..4) flag
    private val connectionState = mutableStateOf<ConnectionState>(ConnectionState.NoUsb)

    // The tablet's own USB tether address, refreshed each time Scanning is
    // entered. Shown to the user as proof the cable/tethering half of the
    // link is working, independent of whether a server has been found.
    private val tetherAddressState = mutableStateOf<String?>(null)

    // The last server we actually completed a handshake with, kept for the
    // lifetime of the activity (not persisted across process death). Lets
    // the Scanning screen offer a direct connect that bypasses discovery
    // entirely, rather than only being tried silently after a disconnect
    // (see startStreaming's reconnect path below).
    private var lastKnownIp:   String? = null
    private var lastKnownPort: Int     = DEFAULT_SERVER_PORT
    private val hasRememberedServerState = mutableStateOf(false)

    // ── Screen 5: Streaming ───────────────────────────────────────────────────
    private lateinit var surfaceView:       SurfaceView
    private lateinit var fpsPill:           LinearLayout
    private lateinit var overlayFps:        TextView
    private lateinit var qualityDot:        View
    private lateinit var streamOverlay:     FrameLayout
    private lateinit var overlayServerName: TextView
    private lateinit var overlayFpsLarge:   TextView
    private lateinit var overlayResolution: TextView
    private lateinit var overlayCodec:      TextView
    private lateinit var disconnectBtn:     Button

    // ── Discovered server ─────────────────────────────────────────────────────
    private var discoveredIp:       String? = null
    private var discoveredPort:     Int     = DEFAULT_SERVER_PORT
    private var discoveredName:     String  = "TethrLink Server"
    private var discoveredHostname: String  = ""
    private var discoveredSystem:   String  = ""
    private var discoveredRes:      String  = ""

    // ── Coroutines ────────────────────────────────────────────────────────────
    private val ioScope        = CoroutineScope(Dispatchers.IO)
    private var streamJob:     Job? = null
    private var listenJob:     Job? = null
    private var stateJob:      Job? = null
    private var discoverySocket: DatagramSocket? = null

    private var frameCount  = 0
    private var fpsLastTime = System.currentTimeMillis()

    private val logLines = mutableListOf<String>()

    // ── Touch input state (set up per connection, torn down on disconnect) ────
    // Written from the streaming coroutine (IO dispatcher), read from the main
    // thread inside the touch listener and the long-press poll — volatile for
    // cross-thread visibility.
    @Volatile private var inputSupported: Boolean = false
    @Volatile private var inputOutputStream: java.io.OutputStream? = null
    @Volatile private var gestureInterpreter: GestureInterpreter? = null
    @Volatile private var streamWidthPx: Int = 0
    @Volatile private var streamHeightPx: Int = 0
    @Volatile private var streamLetterboxed: Boolean = false

    // Guards the shared socket output stream so a batch of pointer actions is
    // written as one contiguous unit rather than interleaved with another.
    private val inputWriteLock = Object()

    // True from a down that landed on video until the matching up/cancel, so a
    // down rejected by VideoGeometry (touch on a letterbox bar) can't leave a
    // later move/up calling into GestureInterpreter without a matching down.
    // Volatile: reset from the streaming coroutine's teardown (IO dispatcher)
    // but read/written from the main-thread touch listener.
    @Volatile private var touchGestureActive = false

    // ── Foreground / lock suspension (cooperative, not a security boundary —
    // the real boundary is server-side; a modified client could ignore this) ──
    // True whenever input must not be sent even though a session is active:
    // backgrounded, split-screen, or the screen locked. Touched only from the
    // main thread (lifecycle callbacks, the registered receiver, and the
    // touch listener), so unlike the fields above these don't need @Volatile.
    private var inputSuspended = false
    private var isActivityResumed = false
    private var screenOffReceiver: BroadcastReceiver? = null

    // ── Back press: reveal/dismiss the streaming overlay ─────────────────────
    // The surface has no tap-to-reveal click listener (see onCreate): a tap
    // on the video always risks being pointer input, so it can never
    // unambiguously mean "show the overlay" — not just while touch input is
    // active. Back press is therefore the only way to bring the overlay back
    // up while streaming; the overlay keeps its own tap-to-dismiss (also in
    // onCreate) so it can still be closed once revealed. Enabled only while
    // the streaming screen is showing (see showStreamingScreen/
    // showConnectionState), so back keeps its normal "leave the activity"
    // meaning everywhere else.
    private val overlayBackCallback = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {
            streamOverlay.visibility =
                if (streamOverlay.visibility == View.VISIBLE) View.GONE else View.VISIBLE
        }
    }

    private val inputHandler = Handler(Looper.getMainLooper())
    private val longPressPoll = object : Runnable {
        override fun run() {
            val interpreter = gestureInterpreter ?: return
            dispatchPointerActions(interpreter.checkLongPress(SystemClock.uptimeMillis()))
            inputHandler.postDelayed(this, LONG_PRESS_POLL_INTERVAL_MS)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        composeUiContainer = findViewById(R.id.composeUiContainer)
        composeUiContainer.setContent {
            ConnectionScreen_OptionC(
                state = connectionState.value,
                tetherAddress = tetherAddressState.value,
                hasRememberedServer = hasRememberedServerState.value,
                onEnableTether = {
                    try {
                        startActivity(Intent("android.settings.TETHER_SETTINGS"))
                    } catch (_: Exception) {
                        startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
                    }
                },
                onStartExtending = {
                    discoveredIp?.let { ip -> startStreaming(ip, discoveredPort) }
                },
                onConnectToLastKnown = {
                    lastKnownIp?.let { ip -> startStreaming(ip, lastKnownPort) }
                }
            )
        }

        surfaceView       = findViewById(R.id.surfaceView)
        fpsPill           = findViewById(R.id.fpsPill)
        overlayFps        = findViewById(R.id.overlayFps)
        qualityDot        = findViewById(R.id.qualityDot)
        streamOverlay     = findViewById(R.id.streamOverlay)
        overlayServerName = findViewById(R.id.overlayServerName)
        overlayFpsLarge   = findViewById(R.id.overlayFpsLarge)
        overlayResolution = findViewById(R.id.overlayResolution)
        overlayCodec      = findViewById(R.id.overlayCodec)
        disconnectBtn     = findViewById(R.id.disconnectBtn)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enableImmersiveMode()

        // No tap-to-reveal click listener on the surface: a tap on the video
        // must be unambiguous pointer input (when negotiated) rather than
        // competing with revealing the overlay. See overlayBackCallback
        // below for the (now sole) way to bring the overlay back up.
        surfaceView.setOnTouchListener { _, event -> handleSurfaceTouch(event) }
        streamOverlay.setOnClickListener { streamOverlay.visibility = View.GONE }
        disconnectBtn.setOnClickListener {
            streamJob?.cancel()
            startStateLoop()
        }

        onBackPressedDispatcher.addCallback(this, overlayBackCallback)

        // Belt-and-suspenders for the lock case: onPause (below) already
        // suspends input the moment the activity loses focus, which covers
        // locking in the normal case, but this reacts the instant the screen
        // actually turns off regardless of exactly how lifecycle callbacks
        // land. ACTION_SCREEN_OFF is a protected system broadcast, so
        // RECEIVER_NOT_EXPORTED is the correct (and required, on API 33+) flag.
        screenOffReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) = suspendInput()
        }
        ContextCompat.registerReceiver(
            this,
            screenOffReceiver,
            IntentFilter(Intent.ACTION_SCREEN_OFF),
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )

        startStateLoop()
    }

    override fun onResume() {
        super.onResume()
        isActivityResumed = true
        if (streamJob?.isActive != true) startStateLoop()
        resumeInputIfInFront()
    }

    override fun onPause() {
        super.onPause()
        isActivityResumed = false
        // Also covers onStop: Android always calls onPause before onStop, so
        // input is already released by the time the activity is fully hidden.
        suspendInput()
    }

    // Split-screen can change without a pause/resume in between (e.g.
    // dragging the divider), so it needs its own hook. The 1-arg overload is
    // deprecated in favour of the 2-arg one, but overriding this one is what
    // actually works from API 24 onward: the platform's default 2-arg
    // implementation (API 26+) forwards to this method, and API 24-25 —
    // which predate the 2-arg overload — call this one directly.
    @Suppress("DEPRECATION")
    override fun onMultiWindowModeChanged(isInMultiWindowMode: Boolean) {
        super.onMultiWindowModeChanged(isInMultiWindowMode)
        if (isInMultiWindowMode) suspendInput() else resumeInputIfInFront()
    }

    // ── State loop ────────────────────────────────────────────────────────────

    private fun startStateLoop() {
        stateJob?.cancel()
        listenJob?.cancel()
        stateJob = ioScope.launch {
            while (true) {
                if (isUsbTetherActive()) {
                    showConnectionState(ConnectionState.Scanning)
                    startDiscoveryListener()
                    return@launch
                }

                val usbConnected = isUsbConnected()
                val newConnState = if (usbConnected) ConnectionState.TetherOff else ConnectionState.NoUsb
                if (connectionState.value != newConnState) {
                    showConnectionState(newConnState)
                }
                delay(1000)
            }
        }
    }

    // ── Screen visibility helpers ─────────────────────────────────────────────

    // Shows one of the 4 setup states inside the Compose UI.
    private suspend fun showConnectionState(state: ConnectionState) = withContext(Dispatchers.Main) {
        connectionState.value = state
        // Refresh the diagnostic-only state Scanning displays. Computed here
        // rather than continuously so it stays cheap: it only needs to be
        // current at the moment the user is looking at the Scanning screen.
        if (state == ConnectionState.Scanning) {
            tetherAddressState.value = getUsbTetherAddress()
        }
        composeUiContainer.visibility = View.VISIBLE
        surfaceView.visibility        = View.GONE
        fpsPill.visibility            = View.GONE
        streamOverlay.visibility      = View.GONE
        overlayBackCallback.isEnabled = false
    }

    // Hides Compose UI and shows the native SurfaceView for streaming.
    private suspend fun showStreamingScreen() = withContext(Dispatchers.Main) {
        composeUiContainer.visibility = View.GONE
        surfaceView.visibility        = View.VISIBLE
        fpsPill.visibility            = View.GONE
        streamOverlay.visibility      = View.GONE
        overlayBackCallback.isEnabled = true
    }

    // ── USB / tethering detection ─────────────────────────────────────────────

    private fun isUsbConnected(): Boolean {
        val intent = registerReceiver(null, android.content.IntentFilter("android.hardware.usb.action.USB_STATE"))
        return intent?.extras?.getBoolean("connected") ?: false
    }

    private fun isUsbTetherActive(): Boolean {
        return try {
            java.net.NetworkInterface.getNetworkInterfaces()
                ?.asSequence()
                ?.any { iface ->
                    iface.isUp && !iface.isLoopback &&
                    (iface.name.startsWith("rndis") ||
                     iface.name.startsWith("usb")   ||
                     iface.name.startsWith("ncm"))  &&
                    iface.inetAddresses.asSequence()
                        .filterIsInstance<java.net.Inet4Address>()
                        .any { !it.isLoopbackAddress }
                } ?: false
        } catch (_: Exception) { false }
    }

    // Same interface walk as isUsbTetherActive(), but returns the tablet's
    // own address on that interface instead of just a yes/no. Lets the
    // Scanning screen show proof the cable/tethering half of the link is
    // genuinely up, independent of whether a server has been discovered.
    private fun getUsbTetherAddress(): String? {
        return try {
            java.net.NetworkInterface.getNetworkInterfaces()
                ?.asSequence()
                ?.filter { iface ->
                    iface.isUp && !iface.isLoopback &&
                    (iface.name.startsWith("rndis") ||
                     iface.name.startsWith("usb")   ||
                     iface.name.startsWith("ncm"))
                }
                ?.flatMap { iface ->
                    iface.inetAddresses.asSequence()
                        .filterIsInstance<java.net.Inet4Address>()
                        .filter { !it.isLoopbackAddress }
                }
                ?.firstOrNull()
                ?.hostAddress
        } catch (_: Exception) { null }
    }

    private fun isUsbTetherIp(ip: String): Boolean {
        return try {
            java.net.NetworkInterface.getNetworkInterfaces()
                ?.asSequence()
                ?.filter { iface ->
                    !iface.isLoopback && iface.isUp &&
                    !iface.name.startsWith("wlan") &&
                    !iface.name.startsWith("p2p")
                }
                ?.flatMap { iface ->
                    iface.inetAddresses.asSequence()
                        .filterIsInstance<java.net.Inet4Address>()
                        .filter { !it.isLoopbackAddress }
                        .map { it.hostAddress }
                }
                ?.any { addr ->
                    val p1 = addr?.split(".") ?: return@any false
                    val p2 = ip.split(".")
                    p1.size == 4 && p2.size == 4 &&
                    p1[0] == p2[0] && p1[1] == p2[1] && p1[2] == p2[2]
                } ?: false
        } catch (_: Exception) { true }
    }

    // ── Immersive mode ────────────────────────────────────────────────────────

    private fun enableImmersiveMode() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) enableImmersiveMode()
    }

    // ── Discovery ─────────────────────────────────────────────────────────────

    private fun appendLog(line: String) {
        logLines.add(line)
        if (logLines.size > 6) logLines.removeAt(0)
    }

    private fun startDiscoveryListener(autoConnectIp: String? = null,
                                       autoConnectPort: Int = DEFAULT_SERVER_PORT) {
        listenJob?.cancel()
        discoverySocket?.close()
        discoverySocket = null

        if (autoConnectIp == null) {
            discoveredIp   = null
            discoveredPort = DEFAULT_SERVER_PORT
        }

        logLines.clear()
        appendLog("USB tethering active...")
        appendLog("Starting broadcast listener...")
        appendLog("Listening on UDP port $DISCOVERY_PORT...")
        appendLog("Waiting for TethrLink broadcast...")

        listenJob = ioScope.launch {
            var pendingIp   = autoConnectIp
            var pendingPort = autoConnectPort
            var lastSeenTimestamp = System.currentTimeMillis()

            val watchdog = launch {
                while (true) {
                    delay(1500)
                    if (!isUsbTetherActive()) {
                        discoverySocket?.close()
                        startStateLoop()
                        return@launch
                    }
                    val now = System.currentTimeMillis()
                    // If server beacon stops arriving for 5 s, drop back to scanning
                    if (connectionState.value is ConnectionState.ServerFound &&
                        (now - lastSeenTimestamp) > 5000) {
                        showConnectionState(ConnectionState.Scanning)
                    }
                }
            }

            try {
                val socket = DatagramSocket(null).also {
                    it.reuseAddress = true
                    it.bind(InetSocketAddress(DISCOVERY_PORT))
                    it.broadcast = true
                }
                discoverySocket = socket
                val buf    = ByteArray(1024)
                val packet = DatagramPacket(buf, buf.size)

                while (streamJob?.isActive != true) {
                    try {
                        socket.receive(packet)
                    } catch (_: java.net.SocketException) {
                        break
                    }
                    val json = JSONObject(String(packet.data, 0, packet.length, Charsets.UTF_8))
                    if (json.optString("app") != "TethrLink") continue

                    lastSeenTimestamp = System.currentTimeMillis()

                    val ip       = packet.address.hostAddress ?: continue
                    val name     = json.optString("name", "TethrLink Server")
                    val hostname = json.optString("hostname", name)
                    val system   = json.optString("system", "Linux")
                    val res      = json.optString("resolution", "")
                    val port     = json.optInt("port", DEFAULT_SERVER_PORT)

                    if (!isUsbTetherIp(ip)) continue

                    appendLog("Server found: $hostname ($ip)")

                    if (pendingIp != null && ip == pendingIp) {
                        pendingIp = null
                        withContext(Dispatchers.Main) {
                            discoveredIp       = ip
                            discoveredPort     = port
                            discoveredName     = name
                            discoveredHostname = hostname
                            discoveredSystem   = system
                            discoveredRes      = res
                        }
                        delay(PRE_STREAM_DELAY_MS)
                        startStreaming(ip, port)
                        break
                    }

                    if (ip != discoveredIp || port != discoveredPort) {
                        discoveredIp       = ip
                        discoveredPort     = port
                        discoveredName     = name
                        discoveredHostname = hostname
                        discoveredSystem   = system
                        discoveredRes      = res
                        showConnectionState(ConnectionState.ServerFound(hostname, ip, system))
                    }
                }
            } catch (e: Exception) {
                if (!e.message.orEmpty().contains("Socket closed") &&
                    listenJob?.isActive == true) {
                    appendLog("Discovery error: ${e.message}")
                }
            } finally {
                watchdog.cancel()
                discoverySocket?.close()
                discoverySocket = null
            }
        }
    }

    // ── Streaming ─────────────────────────────────────────────────────────────

    private fun startStreaming(ip: String, port: Int, busyRetries: Int = 5) {
        listenJob?.cancel()
        streamJob = ioScope.launch {
            try {
                val socket = Socket()
                socket.connect(InetSocketAddress(ip, port), CONNECT_TIMEOUT_MS)
                val input = DataInputStream(socket.getInputStream())

                val deviceName = android.os.Build.MODEL.toByteArray()
                val screenW    = windowManager.currentWindowMetrics.bounds.width()
                val screenH    = windowManager.currentWindowMetrics.bounds.height()
                val screenDims = java.nio.ByteBuffer.allocate(8)
                    .putInt(screenW).putInt(screenH).array()

                val outStream = socket.getOutputStream()
                outStream.write(HelloMessage.build(MAGIC_HELLO, DEVICE_ID, screenDims, deviceName, InputCodec.EXT_BLOCK))
                outStream.flush()

                val responseHeader = ByteArray(6)
                input.readFully(responseHeader)
                when {
                    responseHeader.contentEquals(MAGIC_BUSY) -> {
                        socket.close()
                        if (busyRetries > 0) {
                            appendLog("Server busy, retrying… ($busyRetries attempts left)")
                            delay(2000)
                            startStreaming(ip, port, busyRetries - 1)
                        } else {
                            throw Exception("Server busy after all retries")
                        }
                        return@launch
                    }
                    // Input negotiated: the server saw our extension block and has
                    // touch enabled. Anything else (including a plain MAGIC_OK)
                    // means the server never reads input, so none must be sent.
                    responseHeader.contentEquals(MAGIC_OK2) -> {
                        inputSupported    = true
                        inputOutputStream = outStream
                    }
                    responseHeader.contentEquals(MAGIC_OK) -> {
                        inputSupported    = false
                        inputOutputStream = null
                    }
                    else -> throw Exception("Unexpected server response")
                }

                // A real handshake just completed, so this address is proven
                // reachable — remember it for the "Connect to last known
                // server" action on a future stuck scan, independent of the
                // reconnect-only autoConnectIp path below.
                lastKnownIp   = ip
                lastKnownPort = port
                withContext(Dispatchers.Main) { hasRememberedServerState.value = true }

                withContext(Dispatchers.Main) {
                    lockToLandscape()
                    showStreamingScreen()
                    surfaceView.visibility   = View.VISIBLE
                    streamOverlay.visibility = View.GONE
                    fpsPill.visibility       = View.GONE
                }

                val streamW   = input.readInt()
                val streamH   = input.readInt()
                val codecId   = input.read()
                val codecName = if (codecId == 1) "H.264" else "JPEG"

                withContext(Dispatchers.Main) {
                    overlayServerName.text = discoveredName
                    overlayResolution.text = "${streamW}×${streamH}"
                    overlayCodec.text      = codecName
                    overlayFps.text        = "-- FPS"
                    overlayFpsLarge.text   = "-- FPS"
                    fpsPill.visibility     = View.VISIBLE
                }

                socket.soTimeout = STREAM_TIMEOUT_MS

                val surfaceReady = CompletableDeferred<android.view.Surface>()
                withContext(Dispatchers.Main) {
                    if (surfaceView.holder.surface.isValid) {
                        surfaceReady.complete(surfaceView.holder.surface)
                    } else {
                        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
                            override fun surfaceCreated(h: SurfaceHolder) {
                                surfaceView.holder.removeCallback(this)
                                surfaceReady.complete(h.surface)
                            }
                            override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, h2: Int) {}
                            override fun surfaceDestroyed(h: SurfaceHolder) {
                                if (!surfaceReady.isCompleted)
                                    surfaceReady.completeExceptionally(Exception("Surface destroyed"))
                            }
                        })
                    }
                }

                val surface = withContext(Dispatchers.IO) {
                    kotlinx.coroutines.withTimeout(SURFACE_TIMEOUT_MS) { surfaceReady.await() }
                }

                // Only wire up touch once the server has actually negotiated
                // support — otherwise gestureInterpreter stays null and the
                // touch listener is simply a no-op (the surface has no
                // tap-to-reveal click listener to fall back to any more; the
                // overlay is reached via back press instead, see
                // overlayBackCallback).
                withContext(Dispatchers.Main) {
                    streamWidthPx     = streamW
                    streamHeightPx    = streamH
                    streamLetterboxed = (codecId != 1) // 1 = H.264 (fills view), else JPEG (letterboxed)
                    gestureInterpreter = if (inputSupported) {
                        val slopPx  = ViewConfiguration.get(this@MainActivityV2).scaledTouchSlop.toFloat()
                        val widthPx = surfaceView.width.takeIf { it > 0 } ?: 1
                        GestureInterpreter(
                            longPressMs = ViewConfiguration.getLongPressTimeout().toLong(),
                            touchSlopPx = slopPx / widthPx,
                        )
                    } else {
                        null
                    }
                }

                val latestBitmap = java.util.concurrent.atomic.AtomicReference<android.graphics.Bitmap?>()
                val decoder = StreamDecoder(
                    surface  = surface,
                    codec    = codecId,
                    width    = streamW,
                    height   = streamH,
                    onBitmap = { bmp -> latestBitmap.set(bmp) }
                )

                val renderJob = ioScope.launch(Dispatchers.Main) {
                    while (streamJob?.isActive == true) {
                        val bmp = latestBitmap.getAndSet(null)
                        if (bmp != null) drawFrame(bmp)
                        kotlinx.coroutines.delay(1)
                    }
                }

                var readBuf       = ByteArray(READ_BUF_SIZE)
                var lastFrameTime = System.currentTimeMillis()

                while (streamJob?.isActive == true) {
                    val frameSize = try {
                        input.readInt()
                    } catch (e: java.net.SocketTimeoutException) {
                        val silentMs = System.currentTimeMillis() - lastFrameTime
                        if (silentMs > SILENT_FRAME_LIMIT_MS)
                            throw Exception("No frames for ${silentMs / 1000}s")
                        continue
                    }
                    if (frameSize <= 0) continue

                    if (readBuf.size < frameSize) readBuf = ByteArray(frameSize)
                    input.readFully(readBuf, 0, frameSize)
                    lastFrameTime = System.currentTimeMillis()

                    decoder.decodeFrame(readBuf.copyOf(frameSize))
                    updateFps()
                }

                renderJob.cancel()
                decoder.release()
                socket.close()

            } catch (e: Exception) {
                // Ignore silent timeouts during recovery
            } finally {
                // Tear down touch state with the connection: no button must be
                // left "held" from this session, and no stale interpreter or
                // output stream must survive into the next one.
                inputSupported     = false
                inputOutputStream  = null
                gestureInterpreter = null
                touchGestureActive = false
                inputHandler.removeCallbacks(longPressPoll)

                if (isUsbTetherActive()) {
                    withContext(Dispatchers.Main) { unlockOrientation() }
                    showConnectionState(ConnectionState.Scanning)
                    startDiscoveryListener(autoConnectIp = ip, autoConnectPort = port)
                } else {
                    withContext(Dispatchers.Main) { unlockOrientation() }
                    startStateLoop()
                }
            }
        }
    }

    // ── Rendering ─────────────────────────────────────────────────────────────

    private fun drawFrame(bitmap: android.graphics.Bitmap) {
        val holder: SurfaceHolder = surfaceView.holder
        val canvas: Canvas = holder.lockCanvas() ?: return
        try {
            canvas.drawColor(android.graphics.Color.BLACK)
            val bmpW = bitmap.width.toFloat()
            val bmpH = bitmap.height.toFloat()
            val canW = canvas.width.toFloat()
            val canH = canvas.height.toFloat()
            val scale = minOf(canW / bmpW, canH / bmpH)
            val drawW = bmpW * scale
            val drawH = bmpH * scale
            val left  = (canW - drawW) / 2f
            val top   = (canH - drawH) / 2f
            val dst   = android.graphics.RectF(left, top, left + drawW, top + drawH)
            canvas.drawBitmap(bitmap, null, dst, null)
        } finally {
            holder.unlockCanvasAndPost(canvas)
        }
    }

    // ── Touch input ───────────────────────────────────────────────────────────
    //
    // `gestureInterpreter` is null whenever the server hasn't negotiated input
    // (old server, or the user's touch toggle is off), so every branch below
    // returns `false` in that case — the event is simply left unconsumed
    // (the surface has no click listener to fall back to; the overlay is
    // reached via back press while streaming, see overlayBackCallback).

    private fun handleSurfaceTouch(event: MotionEvent): Boolean {
        val interpreter = gestureInterpreter ?: return false
        // Suspended (backgrounded, split-screen, or locked): don't start or
        // continue a gesture. Anything that was held has already been
        // released by suspendInput(); returning false here just leaves the
        // tap unconsumed, same as when input was never negotiated in the
        // first place.
        if (inputSuspended) return false
        val viewW = surfaceView.width
        val viewH = surfaceView.height
        if (viewW <= 0 || viewH <= 0) return false

        val nowMs = event.eventTime // SystemClock.uptimeMillis() time base

        fun normalisedAt(index: Int) = VideoGeometry.normalise(
            event.getX(index), event.getY(index),
            viewW, viewH,
            streamWidthPx, streamHeightPx,
            streamLetterboxed,
        )

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                // A down landing on a letterbox bar is not a touch on the
                // desktop (see VideoGeometry) — don't start a gesture for it,
                // so a later move/up for this same pointer is also ignored
                // below instead of being fed to an interpreter that never saw
                // the matching down.
                val p = normalisedAt(0)
                if (p == null) {
                    touchGestureActive = false
                    return false
                }
                touchGestureActive = true
                inputHandler.removeCallbacks(longPressPoll)
                dispatchPointerActions(interpreter.onDown(p.x, p.y, nowMs))
                inputHandler.postDelayed(longPressPoll, LONG_PRESS_POLL_INTERVAL_MS)
            }

            MotionEvent.ACTION_MOVE,
            MotionEvent.ACTION_POINTER_DOWN,
            MotionEvent.ACTION_POINTER_UP -> {
                if (!touchGestureActive) return false
                // Track pointer 0 for position; a finger that has strayed onto
                // a letterbox bar simply stops updating position until it
                // returns, rather than releasing or desyncing state.
                val p = normalisedAt(0) ?: return true
                dispatchPointerActions(interpreter.onMove(p.x, p.y, nowMs, event.pointerCount))
            }

            MotionEvent.ACTION_UP -> {
                if (!touchGestureActive) return false
                touchGestureActive = false
                inputHandler.removeCallbacks(longPressPoll)
                dispatchPointerActions(interpreter.onUp(nowMs))
            }

            MotionEvent.ACTION_CANCEL -> {
                val wasActive = touchGestureActive
                touchGestureActive = false
                inputHandler.removeCallbacks(longPressPoll)
                if (!wasActive) return false
                dispatchPointerActions(interpreter.onCancel())
            }

            else -> return false
        }
        return true
    }

    /**
     * Encodes and sends a batch of [PointerAction]s as one contiguous write.
     * Batching (rather than one write per action) keeps e.g. a down's
     * move-then-press pair from being interleaved with another batch on the
     * shared stream.
     *
     * The actual write is dispatched onto [ioScope] (IO dispatcher) rather
     * than performed inline, so a stalled socket — however unlikely once the
     * server has confirmed it reads input — blocks a background thread, never
     * the main thread that also drives the video render loop.
     */
    private fun dispatchPointerActions(actions: List<PointerAction>) {
        if (actions.isEmpty() || !inputSupported) return
        val out = inputOutputStream ?: return
        val frames = actions.map { action ->
            when (action) {
                is PointerAction.Move   -> InputCodec.motion(action.x, action.y)
                is PointerAction.Button -> InputCodec.button(action.button, action.pressed)
                is PointerAction.Scroll -> InputCodec.axis(action.dx, action.dy)
            }
        }
        ioScope.launch {
            try {
                synchronized(inputWriteLock) {
                    for (frame in frames) out.write(frame)
                    out.flush()
                }
            } catch (_: Exception) {
                // An input write failure must never take the video path down
                // with it. Video is the product; input is an addition.
            }
        }
    }

    // ── Foreground / lock suspension ─────────────────────────────────────────

    private fun isScreenLocked(): Boolean =
        (getSystemService(KEYGUARD_SERVICE) as? KeyguardManager)?.isKeyguardLocked ?: false

    private fun isInMultiWindowModeCompat(): Boolean =
        android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.N && isInMultiWindowMode

    /**
     * Releases anything held and clears gesture state, so a button is never
     * left down on the PC just because the app stopped being in front. Safe
     * to call at any time, session or no session: with no interpreter
     * ([gestureInterpreter] null) it degrades to just setting the flag and
     * cancelling the poll, and [GestureInterpreter.onCancel] itself is a
     * no-op when nothing is held.
     */
    private fun suspendInput() {
        inputSuspended = true
        inputHandler.removeCallbacks(longPressPoll)
        touchGestureActive = false
        val interpreter = gestureInterpreter ?: return
        dispatchPointerActions(interpreter.onCancel())
    }

    /**
     * Lifts the suspension, but only if the activity is genuinely in front
     * right now. Called from more than one lifecycle hook, each of which only
     * knows about its own cause — this re-checks all three so that clearing
     * one (e.g. the lock screen) can't override another that's still true
     * (e.g. still in split-screen).
     */
    private fun resumeInputIfInFront() {
        if (isActivityResumed && !isInMultiWindowModeCompat() && !isScreenLocked()) {
            inputSuspended = false
        }
    }

    // ── Orientation helpers ───────────────────────────────────────────────────

    private fun lockToLandscape() {
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
    }

    private fun unlockOrientation() {
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_FULL_USER
    }

    // ── FPS ───────────────────────────────────────────────────────────────────

    private suspend fun updateFps() {
        frameCount++
        val now = System.currentTimeMillis()
        if (now - fpsLastTime >= 1000) {
            val fps = frameCount
            frameCount  = 0
            fpsLastTime = now
            withContext(Dispatchers.Main) {
                val fpsText = "$fps FPS"
                overlayFps.text      = fpsText
                overlayFpsLarge.text = fpsText
                qualityDot.setBackgroundResource(
                    when {
                        fps >= 25 -> R.drawable.dot_green
                        fps >= 15 -> R.drawable.dot_yellow
                        else      -> R.drawable.dot_red
                    }
                )
            }
        }
    }

    // ── Device ID ─────────────────────────────────────────────────────────────

    private fun getOrCreateDeviceId(): ByteArray {
        val prefs = getSharedPreferences("tethrlink_device", MODE_PRIVATE)
        val saved = prefs.getString("device_id", null)
        if (saved != null) return Base64.decode(saved, Base64.DEFAULT)
        val id = java.util.UUID.randomUUID().toString().replace("-", "")
            .substring(0, 16).toByteArray()
        prefs.edit().putString("device_id", Base64.encodeToString(id, Base64.DEFAULT)).apply()
        return id
    }

    override fun onDestroy() {
        super.onDestroy()
        stateJob?.cancel()
        streamJob?.cancel()
        listenJob?.cancel()
        ioScope.cancel()
        inputHandler.removeCallbacks(longPressPoll)
        screenOffReceiver?.let { unregisterReceiver(it) }
        screenOffReceiver = null
    }
}
