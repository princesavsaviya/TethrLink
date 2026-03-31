package com.tetherlink

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.os.Bundle
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowManager
import android.widget.Button
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
import android.content.Intent
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import android.content.SharedPreferences
import org.json.JSONObject
import java.io.DataInputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.Socket

/**
 * TetherLink – Main Activity (v0.6.0)
 *
 * Batch 2 UX improvements:
 *  - Connection quality indicator (green/yellow/red dot based on FPS)
 *  - Disconnect button (swipe down from top to reveal, tap to disconnect)
 *  - Onboarding screen (shown on first launch with setup instructions)
 */
class MainActivity : AppCompatActivity() {

    private val SERVER_PORT             = 8080
    private val MAGIC_HELLO             = "TLHELO".toByteArray()
    private val MAGIC_CHALLENGE         = "TLCHAL".toByteArray()
    private val MAGIC_RESPONSE          = "TLRESP".toByteArray()
    private val MAGIC_OK                = "TLOK__".toByteArray()
    private val MAGIC_REJECT            = "TLREJ_".toByteArray()
    private val DEVICE_ID: ByteArray by lazy { getOrCreateDeviceId() }
    private val DISCOVERY_PORT          = 8765
    private val AUTO_RECONNECT_DELAY_MS = 2000L
    private val PREFS_NAME              = "tetherlink"
    private val PREF_ONBOARDED          = "onboarded"

    // ── Views ─────────────────────────────────────────────────────────────────
    private lateinit var surfaceView:      SurfaceView
    private lateinit var overlayFps:       TextView
    private lateinit var qualityDot:       View
    private lateinit var disconnectBtn:    Button
    private lateinit var streamOverlay:    View
    private lateinit var discoveryLayout:  View
    private lateinit var statusText:       TextView
    private lateinit var progressBar:      ProgressBar
    private lateinit var serverNameText:   TextView
    private lateinit var serverInfoText:   TextView
    private lateinit var connectButton:    Button
    private lateinit var pairButton:       Button
    private lateinit var onboardingLayout: View

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

        surfaceView      = findViewById(R.id.surfaceView)
        overlayFps       = findViewById(R.id.overlayFps)
        qualityDot       = findViewById(R.id.qualityDot)
        disconnectBtn    = findViewById(R.id.disconnectBtn)
        streamOverlay    = findViewById(R.id.streamOverlay)
        discoveryLayout  = findViewById(R.id.discoveryLayout)
        statusText       = findViewById(R.id.statusText)
        progressBar      = findViewById(R.id.progressBar)
        serverNameText   = findViewById(R.id.serverNameText)
        serverInfoText   = findViewById(R.id.serverInfoText)
        connectButton    = findViewById(R.id.connectButton)
        pairButton       = findViewById(R.id.pairButton)
        onboardingLayout = findViewById(R.id.onboardingLayout)

        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enableImmersiveMode()

        // ── Disconnect button ─────────────────────────────────────────────────
        disconnectBtn.setOnClickListener {
            streamJob?.cancel()
            showDiscoveryScreen()
        }

        // ── Stream overlay toggle (swipe down to show/hide disconnect button) ─
        surfaceView.setOnClickListener {
            streamOverlay.visibility =
                if (streamOverlay.visibility == View.VISIBLE) View.GONE
                else View.VISIBLE
        }

        pairButton.setOnClickListener {
            startActivity(Intent(this, PairingActivity::class.java))
        }

        connectButton.setOnClickListener {
            val ip = discoveredIp ?: return@setOnClickListener
            startStreaming(ip)
        }

        findViewById<android.widget.ImageButton>(R.id.settingsBtn).setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        // ── Onboarding ────────────────────────────────────────────────────────
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(PREF_ONBOARDED, false)) {
            showOnboarding()
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

    // ── Onboarding ────────────────────────────────────────────────────────────

    private fun showOnboarding() {
        onboardingLayout.visibility = View.VISIBLE
        discoveryLayout.visibility  = View.GONE
        surfaceView.visibility      = View.GONE
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

    /**
     * Returns true if the given IP belongs to a USB tethering interface.
     * Excludes WiFi (wlan) interfaces to enforce USB-only mode.
     */
    private fun getOrCreateDeviceId(): ByteArray {
        val prefs = getSharedPreferences("tetherlink_device", MODE_PRIVATE)
        val saved = prefs.getString("device_id", null)
        if (saved != null) return Base64.decode(saved, Base64.DEFAULT)
        val id = java.util.UUID.randomUUID().toString().replace("-", "")
            .substring(0, 16).toByteArray()
        prefs.edit().putString("device_id", Base64.encodeToString(id, Base64.DEFAULT)).apply()
        return id
    }

    private fun getStoredSecret(): ByteArray? {
        val prefs = getSharedPreferences("tetherlink_security", MODE_PRIVATE)
        val saved = prefs.getString("paired_secret", null) ?: return null
        return Base64.decode(saved, Base64.DEFAULT)
    }

    fun storeSecret(secret: ByteArray) {
        val prefs = getSharedPreferences("tetherlink_security", MODE_PRIVATE)
        prefs.edit().putString("paired_secret", Base64.encodeToString(secret, Base64.DEFAULT)).apply()
    }

    private fun computeHmac(nonce: ByteArray, secret: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret, "HmacSHA256"))
        return mac.doFinal(nonce)
    }

    private fun isUsbTetherIp(ip: String): Boolean {
        return try {
            java.net.NetworkInterface.getNetworkInterfaces()
                ?.asSequence()
                ?.filter { iface ->
                    // Exclude loopback and WiFi interfaces
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
                    // Check if IP is on same /24 subnet as this USB interface
                    val parts1 = addr?.split(".") ?: return@any false
                    val parts2 = ip.split(".")
                    parts1.size == 4 && parts2.size == 4 &&
                            parts1[0] == parts2[0] &&
                            parts1[1] == parts2[1] &&
                            parts1[2] == parts2[2]
                } ?: false
        } catch (_: Exception) { true } // allow if check fails
    }

    private fun startDiscoveryListener(autoConnectIp: String? = null) {
        listenJob?.cancel()
        listenJob = ioScope.launch {
            setStatus("Searching for TetherLink server…")
            showProgress(true)
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

                    // Only connect via USB tethering — ignore WiFi broadcasts
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
                            serverNameText.text = "💻  $name"
                            serverInfoText.text = buildString {
                                append(ip)
                                if (res.isNotEmpty()) append("  •  $res")
                            }
                            connectButton.visibility = View.VISIBLE
                            statusText.text = "Server found — tap to connect"
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
                discoveryLayout.visibility = View.GONE
                onboardingLayout.visibility = View.GONE
                surfaceView.visibility     = View.VISIBLE
                streamOverlay.visibility   = View.GONE
            }

            try {
                val socket = Socket()
                socket.connect(InetSocketAddress(ip, SERVER_PORT), 5000)
                val input = DataInputStream(socket.getInputStream())

                // ── 3-way HMAC handshake ─────────────────────────────
                val secret = getStoredSecret()
                if (secret == null) {
                    throw Exception("Not paired. Tap '🔗 Pair with PC' first.")
                }

                // Step 1: Send HELLO + device_id + screen_w + screen_h + device_name
                val deviceName = android.os.Build.MODEL.toByteArray()
                val screenW = windowManager.currentWindowMetrics.bounds.width()
                val screenH = windowManager.currentWindowMetrics.bounds.height()
                val screenDims = java.nio.ByteBuffer.allocate(8)
                    .putInt(screenW).putInt(screenH).array()
                socket.getOutputStream().write(MAGIC_HELLO + DEVICE_ID + screenDims + deviceName)
                socket.getOutputStream().flush()

                // Step 2: Receive CHALLENGE
                val challengeHeader = ByteArray(6)
                input.readFully(challengeHeader)
                if (!challengeHeader.contentEquals(MAGIC_CHALLENGE)) {
                    throw Exception("Unexpected response from server")
                }
                val nonce = ByteArray(16)
                input.readFully(nonce)

                // Step 3: Send HMAC response
                val hmacBytes = computeHmac(nonce, secret)
                socket.getOutputStream().write(MAGIC_RESPONSE + hmacBytes)
                socket.getOutputStream().flush()

                // Step 4: Receive OK + resolution + codec
                val resultHeader = ByteArray(6)
                input.readFully(resultHeader)
                if (resultHeader.contentEquals(MAGIC_REJECT)) {
                    throw Exception("Authentication failed — wrong key or server busy")
                }
                if (!resultHeader.contentEquals(MAGIC_OK)) {
                    throw Exception("Unexpected server response")
                }
                // ─────────────────────────────────────────────────────────────

                val streamW = input.readInt()
                val streamH = input.readInt()
                val codecId = input.read()  // 1=H264, 2=JPEG
                val codecName = if (codecId == 1) "H.264" else "JPEG"
                showToast("Connected to $discoveredName — ${streamW}×${streamH} $codecName")

                // Keep-alive: detect silent disconnects within 3s
                socket.soTimeout = 3000

                // Use SurfaceHolder.Callback to wait for the surface to be
                // created/ready AFTER surfaceView becomes visible
                val surfaceReady = CompletableDeferred<android.view.Surface>()
                withContext(Dispatchers.Main) {
                    if (surfaceView.holder.surface.isValid) {
                        // Surface already valid (e.g. reconnect)
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

                // Wait up to 5 seconds for surface
                val surface = withContext(Dispatchers.IO) {
                    kotlinx.coroutines.withTimeout(5000) { surfaceReady.await() }
                }

                // Initialize decoder based on codec
                val latestBitmap = java.util.concurrent.atomic.AtomicReference<Bitmap?>()
                val decoder = StreamDecoder(
                    surface  = surface,
                    codec    = codecId,
                    width    = streamW,
                    height   = streamH,
                    onBitmap = { bmp -> latestBitmap.set(bmp) }
                )



                // Render coroutine for JPEG mode
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
            overlayFps.visibility       = View.GONE
            qualityDot.visibility       = View.GONE
            discoveryLayout.visibility  = View.VISIBLE
            connectButton.visibility    = View.GONE
            serverNameText.text         = ""
            serverInfoText.text         = ""
            discoveredIp                = null
        }
        // Restart discovery so Connect button reappears when server is found
        startDiscoveryListener()
    }

    // ── Rendering ─────────────────────────────────────────────────────────────

    private fun drawFrame(bitmap: Bitmap) {
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
                overlayFps.text = "$fps FPS"
                // ── Connection quality dot ─────────────────────────────────
                qualityDot.visibility = View.VISIBLE
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