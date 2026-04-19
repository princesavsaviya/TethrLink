package com.tetherlink

import android.content.Intent
import android.graphics.Canvas
import android.os.Bundle
import android.provider.Settings
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import android.util.Base64
import org.json.JSONObject
import androidx.compose.ui.platform.ComposeView
import androidx.compose.runtime.mutableStateOf
import com.tetherlink.ui.NoUSBScreen
import com.tetherlink.ui.ScanningScreen
import com.tetherlink.ui.ServerFoundScreen
import com.tetherlink.ui.TetherOffScreen
import java.io.DataInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.Socket

/**
 * TetherLink — Main Activity
 *
 * State machine:
 *   NO_USB → NO_TETHERING → SCANNING → SERVER_FOUND → STREAMING
 */
class MainActivity : AppCompatActivity() {

    // ── Network constants ─────────────────────────────────────────────────────
    private val DEFAULT_SERVER_PORT   = 51137
    private val DISCOVERY_PORT        = 8765

    // ── Timeout constants ─────────────────────────────────────────────────────
    private val CONNECT_TIMEOUT_MS    = 5000
    private val STREAM_TIMEOUT_MS     = 3000
    private val SURFACE_TIMEOUT_MS    = 5000L
    private val SILENT_FRAME_LIMIT_MS = 3000L
    private val PRE_STREAM_DELAY_MS   = 500L
    private val AUTO_RECONNECT_DELAY  = 2000L
    private val READ_BUF_SIZE         = 1024 * 1024

    // ── Protocol magic bytes ──────────────────────────────────────────────────
    private val MAGIC_HELLO = "TLHELO".toByteArray()
    private val MAGIC_OK    = "TLOK__".toByteArray()
    private val MAGIC_BUSY  = "TLBUSY".toByteArray()

    // ── Device identity ───────────────────────────────────────────────────────
    private val DEVICE_ID: ByteArray by lazy { getOrCreateDeviceId() }

    private lateinit var composeUiContainer: ComposeView
    private val currentScreenState = mutableStateOf(1)

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
    private var discoveredName:     String  = "TetherLink Server"
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

    // ── Log lines for scanning screen ─────────────────────────────────────────
    private val logLines = mutableListOf<String>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Compose UI setup
        composeUiContainer = findViewById(R.id.composeUiContainer)
        composeUiContainer.setContent {
            when (currentScreenState.value) {
                1 -> NoUSBScreen()
                2 -> TetherOffScreen(onEnableTether = {
                    try {
                        startActivity(Intent("android.settings.TETHER_SETTINGS"))
                    } catch (_: Exception) {
                        startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
                    }
                })
                3 -> ScanningScreen(onServerFound = {})
                4 -> ServerFoundScreen(
                    hostname = discoveredHostname,
                    ip = discoveredIp ?: "",
                    system = discoveredSystem,
                    onStartExtending = {
                        discoveredIp?.let { ip -> startStreaming(ip, discoveredPort) }
                    }
                )
            }
        }

        // Screen 5
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

        surfaceView.setOnClickListener {
            streamOverlay.visibility = View.VISIBLE
        }
        streamOverlay.setOnClickListener {
            streamOverlay.visibility = View.GONE
        }
        disconnectBtn.setOnClickListener {
            streamJob?.cancel()
            startStateLoop()
        }

        startStateLoop()
    }

    override fun onResume() {
        super.onResume()
        if (streamJob?.isActive != true) startStateLoop()
    }

    // ── State loop ────────────────────────────────────────────────────────────

    private fun startStateLoop() {
        stateJob?.cancel()
        listenJob?.cancel()
        stateJob = ioScope.launch {
            while (true) {
                if (isUsbTetherActive()) {
                    showScreen(3)
                    startDiscoveryListener()
                    return@launch
                }
                
                val usbConnected = isUsbConnected()
                withContext(Dispatchers.Main) {
                    val newState = if (usbConnected) 2 else 1
                    if (currentScreenState.value != newState) {
                        showScreen(newState)
                    }
                }
                delay(1000)
            }
        }
    }

    private suspend fun showScreen(n: Int) = withContext(Dispatchers.Main) {
        currentScreenState.value = n
        composeUiContainer.visibility = if (n <= 4) View.VISIBLE else View.GONE
        surfaceView.visibility       = if (n == 5) View.VISIBLE else View.GONE
        fpsPill.visibility           = View.GONE
        streamOverlay.visibility     = View.GONE
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
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
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
        // Force-close any previous socket so port 8765 is freed immediately.
        // socket.receive() is a blocking call that ignores coroutine cancellation,
        // so the only way to unblock it is to close the socket from outside.
        discoverySocket?.close()
        discoverySocket = null

        // Reset so the first broadcast after reconnect always triggers Screen 4.
        if (autoConnectIp == null) {
            discoveredIp   = null
            discoveredPort = DEFAULT_SERVER_PORT
        }

        logLines.clear()
        appendLog("USB tethering active...")
        appendLog("Starting broadcast listener...")
        appendLog("Listening on UDP port $DISCOVERY_PORT...")
        appendLog("Waiting for TetherLink broadcast...")

        listenJob = ioScope.launch {
            var pendingIp   = autoConnectIp
            var pendingPort = autoConnectPort
            var lastSeenTimestamp = System.currentTimeMillis()

            // USB watchdog: if tethering drops while scanning or on server-found
            // screen, cancel discovery and return to home immediately.
            val watchdog = launch {
                while (true) {
                    delay(1500)
                    if (!isUsbTetherActive()) {
                        discoverySocket?.close()
                        startStateLoop()
                        return@launch
                    }
                    val now = System.currentTimeMillis()
                    if (currentScreenState.value == 4 && (now - lastSeenTimestamp) > 5000) {
                        showScreen(3)
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
                        // Socket was closed by watchdog or startDiscoveryListener
                        // re-entry — exit the loop cleanly.
                        break
                    }
                    val json = JSONObject(
                        String(packet.data, 0, packet.length, Charsets.UTF_8)
                    )
                    if (json.optString("app") != "TetherLink") continue

                    lastSeenTimestamp = System.currentTimeMillis()

                    val ip       = packet.address.hostAddress ?: continue
                    val name     = json.optString("name", "TetherLink Server")
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
                        withContext(Dispatchers.Main) {
                            showScreen(4)
                        }
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

                socket.getOutputStream().write(MAGIC_HELLO + DEVICE_ID + screenDims + deviceName)
                socket.getOutputStream().flush()

                val responseHeader = ByteArray(6)
                input.readFully(responseHeader)
                when {
                    responseHeader.contentEquals(MAGIC_BUSY) -> {
                        socket.close()
                        if (busyRetries > 0) {
                            // Server is finishing teardown of the previous session.
                            // Wait 2s and try again — the lock will be free by then.
                            appendLog("Server busy, retrying… ($busyRetries attempts left)")
                            delay(2000)
                            startStreaming(ip, port, busyRetries - 1)
                        } else {
                            throw Exception("Server busy after all retries")
                        }
                        return@launch
                    }
                    !responseHeader.contentEquals(MAGIC_OK) ->
                        throw Exception("Unexpected server response")
                }

                // Only switch to the stream screen after handshake succeeds.
                // Showing Screen 5 before TLOK causes a black-screen flash on every
                // TLBUSY retry and makes the UI loop visibly between screens.
                withContext(Dispatchers.Main) {
                    showScreen(5)
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
                // Auto-reconnect: if tethering is still up, go straight to scanning
                // (skip the state loop so we never flash the "No Tethering" screen).
                // If tethering went down, fall back to the normal state loop.
                if (isUsbTetherActive()) {
                    showScreen(3)
                    startDiscoveryListener(autoConnectIp = ip, autoConnectPort = port)
                } else {
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
            val dst = android.graphics.RectF(
                0f, 0f, canvas.width.toFloat(), canvas.height.toFloat()
            )
            canvas.drawColor(android.graphics.Color.BLACK)
            canvas.drawBitmap(bitmap, null, dst, null)
        } finally {
            holder.unlockCanvasAndPost(canvas)
        }
    }

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
        val prefs = getSharedPreferences("tetherlink_device", MODE_PRIVATE)
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
    }
}
