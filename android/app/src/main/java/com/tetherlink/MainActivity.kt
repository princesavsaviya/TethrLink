package com.tetherlink

import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Bundle
import android.provider.Settings
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
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
import android.content.SharedPreferences
import android.util.Base64
import org.json.JSONObject
import java.io.DataInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.Socket

/**
 * TetherLink – Main Activity (v0.9.4)
 *
 * UI redesign:
 *  - Material You dark theme throughout
 *  - USB tethering status banner with deep-link to settings
 *  - Retractable stream overlay: persistent FPS pill, full panel on tap
 *  - Onboarding, discovery, and settings screens redesigned
 */
class MainActivity : AppCompatActivity() {

    private val SERVER_PORT             = 8080
    private val MAGIC_HELLO             = "TLHELO".toByteArray()
    private val MAGIC_OK                = "TLOK__".toByteArray()
    private val MAGIC_BUSY              = "TLBUSY".toByteArray()
    private val DEVICE_ID: ByteArray by lazy { getOrCreateDeviceId() }
    private val DISCOVERY_PORT          = 8765
    private val AUTO_RECONNECT_DELAY_MS = 2000L
    private val PREFS_NAME              = "tetherlink"
    private val PREF_ONBOARDED          = "onboarded"

    // ── Views ─────────────────────────────────────────────────────────────────
    private lateinit var surfaceView:        SurfaceView
    private lateinit var fpsPill:            LinearLayout
    private lateinit var overlayFps:         TextView
    private lateinit var qualityDot:         View
    private lateinit var streamOverlay:      FrameLayout
    private lateinit var overlayServerName:  TextView
    private lateinit var overlayFpsLarge:    TextView
    private lateinit var overlayResolution:  TextView
    private lateinit var overlayCodec:       TextView
    private lateinit var disconnectBtn:      Button
    private lateinit var discoveryLayout:    View
    private lateinit var tetheringBanner:    LinearLayout
    private lateinit var tetheringEnableBtn: Button
    private lateinit var progressBar:        ProgressBar
    private lateinit var statusText:         TextView
    private lateinit var serverCard:         LinearLayout
    private lateinit var serverNameText:     TextView
    private lateinit var serverInfoText:     TextView
    private lateinit var connectButton:      Button
    private lateinit var onboardingLayout:   View

    private lateinit var prefs: SharedPreferences
    private val ioScope   = CoroutineScope(Dispatchers.IO)
    private var streamJob: Job? = null
    private var listenJob: Job? = null

    private var discoveredIp:   String? = null
    private var discoveredName: String  = "TetherLink Server"
    private var discoveredRes:  String  = ""

    private var frameCount  = 0
    private var fpsLastTime = System.currentTimeMillis()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

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
        discoveryLayout   = findViewById(R.id.discoveryLayout)
        tetheringBanner   = findViewById(R.id.tetheringBanner)
        tetheringEnableBtn = findViewById(R.id.tetheringEnableBtn)
        progressBar       = findViewById(R.id.progressBar)
        statusText        = findViewById(R.id.statusText)
        serverCard        = findViewById(R.id.serverCard)
        serverNameText    = findViewById(R.id.serverNameText)
        serverInfoText    = findViewById(R.id.serverInfoText)
        connectButton     = findViewById(R.id.connectButton)
        onboardingLayout  = findViewById(R.id.onboardingLayout)

        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enableImmersiveMode()

        // Tap surfaceView → show overlay
        surfaceView.setOnClickListener {
            streamOverlay.visibility = View.VISIBLE
        }

        // Tap anywhere on overlay → dismiss it
        streamOverlay.setOnClickListener {
            streamOverlay.visibility = View.GONE
        }

        // Disconnect button — click consumed here, does not propagate to overlay
        disconnectBtn.setOnClickListener {
            streamJob?.cancel()
            showDiscoveryScreen()
        }

        connectButton.setOnClickListener {
            val ip = discoveredIp ?: return@setOnClickListener
            startStreaming(ip)
        }

        // Deep-link to Android tethering settings
        tetheringEnableBtn.setOnClickListener {
            try {
                startActivity(Intent("android.settings.TETHER_SETTINGS"))
            } catch (_: Exception) {
                startActivity(Intent(Settings.ACTION_WIRELESS_SETTINGS))
            }
        }

        findViewById<ImageButton>(R.id.settingsBtn).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // Onboarding
        if (!prefs.getBoolean(PREF_ONBOARDED, false)) {
            onboardingLayout.visibility = View.VISIBLE
        } else {
            discoveryLayout.visibility = View.VISIBLE
            startDiscoveryListener()
        }

        findViewById<Button>(R.id.onboardingDoneBtn).setOnClickListener {
            prefs.edit().putBoolean(PREF_ONBOARDED, true).apply()
            onboardingLayout.visibility = View.GONE
            discoveryLayout.visibility  = View.VISIBLE
            startDiscoveryListener()
        }
    }

    override fun onResume() {
        super.onResume()
        if (discoveryLayout.visibility == View.VISIBLE) {
            tetheringBanner.visibility = if (isUsbTetherActive()) View.GONE else View.VISIBLE
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

    // ── USB tethering detection ───────────────────────────────────────────────

    /**
     * Returns true if a USB tethering interface (rndis, usb, ncm) is up
     * and has an IPv4 address. Used to show/hide the tethering banner.
     */
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
                    val parts1 = addr?.split(".") ?: return@any false
                    val parts2 = ip.split(".")
                    parts1.size == 4 && parts2.size == 4 &&
                            parts1[0] == parts2[0] &&
                            parts1[1] == parts2[1] &&
                            parts1[2] == parts2[2]
                } ?: false
        } catch (_: Exception) { true }
    }

    private fun startDiscoveryListener(autoConnectIp: String? = null) {
        listenJob?.cancel()
        listenJob = ioScope.launch {
            setStatus("Searching for server…")
            showProgress(true)

            withContext(Dispatchers.Main) {
                tetheringBanner.visibility = if (isUsbTetherActive()) View.GONE else View.VISIBLE
            }

            var autoConnect = autoConnectIp
            try {
                val socket = DatagramSocket(DISCOVERY_PORT)
                socket.broadcast = true
                val buf    = ByteArray(1024)
                val packet = DatagramPacket(buf, buf.size)

                while (listenJob?.isActive == true && streamJob?.isActive != true) {
                    socket.receive(packet)
                    val json = JSONObject(
                        String(packet.data, 0, packet.length, Charsets.UTF_8)
                    )
                    if (json.optString("app") != "TetherLink") continue

                    val ip   = packet.address.hostAddress ?: continue
                    val name = json.optString("name", "TetherLink Server")
                    val res  = json.optString("resolution", "")

                    if (!isUsbTetherIp(ip)) continue

                    if (autoConnect != null && ip == autoConnect) {
                        autoConnect = null
                        withContext(Dispatchers.Main) {
                            discoveredIp   = ip
                            discoveredName = name
                            discoveredRes  = res
                        }
                        setStatus("Reconnecting to $name…")
                        delay(500)
                        startStreaming(ip)
                        break
                    }

                    if (ip != discoveredIp) {
                        discoveredIp   = ip
                        discoveredName = name
                        discoveredRes  = res
                        withContext(Dispatchers.Main) {
                            showProgress(false)
                            serverNameText.text = name
                            serverInfoText.text = buildString {
                                append(ip)
                                if (res.isNotEmpty()) append("  ·  $res")
                            }
                            serverCard.visibility    = View.VISIBLE
                            connectButton.visibility = View.VISIBLE
                            statusText.text          = ""
                        }
                    }
                }
                socket.close()
            } catch (e: Exception) {
                if (listenJob?.isActive == true) setStatus("Discovery error: ${e.message}")
            }
        }
    }

    // ── Streaming ─────────────────────────────────────────────────────────────

    private fun startStreaming(ip: String) {
        listenJob?.cancel()
        streamJob = ioScope.launch {
            withContext(Dispatchers.Main) {
                discoveryLayout.visibility  = View.GONE
                onboardingLayout.visibility = View.GONE
                surfaceView.visibility      = View.VISIBLE
                streamOverlay.visibility    = View.GONE
                fpsPill.visibility          = View.GONE
            }

            try {
                val socket = Socket()
                socket.connect(InetSocketAddress(ip, SERVER_PORT), 5000)
                val input = DataInputStream(socket.getInputStream())

                // ── Handshake ─────────────────────────────────────────────────
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
                    responseHeader.contentEquals(MAGIC_BUSY) ->
                        throw Exception("Server is busy — another device is connected")
                    !responseHeader.contentEquals(MAGIC_OK) ->
                        throw Exception("Unexpected response from server")
                }
                // ─────────────────────────────────────────────────────────────

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

                showToast("Connected to $discoveredName — ${streamW}×${streamH} $codecName")
                socket.soTimeout = 3000

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
                    kotlinx.coroutines.withTimeout(5000) { surfaceReady.await() }
                }

                val latestBitmap = java.util.concurrent.atomic.AtomicReference<android.graphics.Bitmap?>()
                val decoder = StreamDecoder(
                    surface  = surface,
                    codec    = codecId,
                    width    = streamW,
                    height   = streamH,
                    onBitmap = { bmp -> latestBitmap.set(bmp) }
                )

                val renderJob = kotlinx.coroutines.GlobalScope.launch(Dispatchers.Main) {
                    while (streamJob?.isActive == true) {
                        val bmp = latestBitmap.getAndSet(null)
                        if (bmp != null) drawFrame(bmp)
                        kotlinx.coroutines.delay(1)
                    }
                }

                var readBuf       = ByteArray(1024 * 1024)
                var lastFrameTime = System.currentTimeMillis()

                while (streamJob?.isActive == true) {
                    val frameSize = try {
                        input.readInt()
                    } catch (e: java.net.SocketTimeoutException) {
                        val silentMs = System.currentTimeMillis() - lastFrameTime
                        if (silentMs > 3000) throw Exception("No frames for ${silentMs/1000}s")
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
                val lastIp = ip
                showDiscoveryScreen()
                setStatus("Disconnected — reconnecting…")
                delay(AUTO_RECONNECT_DELAY_MS)
                startDiscoveryListener(autoConnectIp = lastIp)
            }
        }
    }

    private fun showDiscoveryScreen() {
        runOnUiThread {
            surfaceView.visibility      = View.GONE
            streamOverlay.visibility    = View.GONE
            fpsPill.visibility          = View.GONE
            discoveryLayout.visibility  = View.VISIBLE
            serverCard.visibility       = View.GONE
            connectButton.visibility    = View.GONE
            serverNameText.text         = ""
            serverInfoText.text         = ""
            discoveredIp                = null
            tetheringBanner.visibility  = if (isUsbTetherActive()) View.GONE else View.VISIBLE
        }
        startDiscoveryListener()
    }

    // ── Rendering ─────────────────────────────────────────────────────────────

    private fun drawFrame(bitmap: android.graphics.Bitmap) {
        val holder: SurfaceHolder = surfaceView.holder
        val canvas: Canvas = holder.lockCanvas() ?: return
        try {
            val dst = android.graphics.RectF(
                0f, 0f,
                canvas.width.toFloat(),
                canvas.height.toFloat()
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

    // ── Helpers ───────────────────────────────────────────────────────────────

    private suspend fun setStatus(msg: String) = withContext(Dispatchers.Main) {
        statusText.text = msg
    }

    private suspend fun showProgress(show: Boolean) = withContext(Dispatchers.Main) {
        progressBar.visibility = if (show) View.VISIBLE else View.GONE
    }

    private suspend fun showToast(msg: String) = withContext(Dispatchers.Main) {
        Toast.makeText(this@MainActivity, msg, Toast.LENGTH_SHORT).show()
    }

    override fun onDestroy() {
        super.onDestroy()
        streamJob?.cancel()
        listenJob?.cancel()
        ioScope.cancel()
    }
}
