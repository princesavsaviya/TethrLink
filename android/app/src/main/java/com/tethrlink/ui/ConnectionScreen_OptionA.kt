package com.tethrlink.ui

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tethrlink.R
import kotlinx.coroutines.delay

// All 4 connection states in a single animated screen.
// Center visualization, badge, headline, and action section all morph between states.

sealed class ConnectionState {
    object NoUsb : ConnectionState()
    object TetherOff : ConnectionState()
    object Scanning : ConnectionState()
    data class ServerFound(val hostname: String, val ip: String, val system: String) : ConnectionState()
}

@Composable
fun ConnectionScreen_OptionA(
    state: ConnectionState,
    onEnableTether: () -> Unit,
    onStartExtending: () -> Unit
) {
    var pulseState by remember { mutableStateOf(0) }
    var logMessages by remember { mutableStateOf(listOf("USB tethering active...", "Starting broadcast listener...")) }

    val resolutions = remember { listOf("1920×1080", "2560×1440", "3840×2160", "1280×720") }
    val refreshRates = remember { listOf("60 Hz", "120 Hz", "144 Hz") }
    var selectedRes by remember { mutableIntStateOf(0) }
    var selectedHz by remember { mutableIntStateOf(0) }

    val inf = rememberInfiniteTransition(label = "conn_a")
    val radarRotation by inf.animateFloat(
        initialValue = 0f, targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(5000, easing = LinearEasing)),
        label = "radar"
    )
    val scanDotAlpha by inf.animateFloat(
        initialValue = 0.4f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(600), RepeatMode.Reverse),
        label = "scanDot"
    )
    val cursorBlink by inf.animateFloat(
        initialValue = 0f, targetValue = 1f,
        animationSpec = infiniteRepeatable(keyframes { durationMillis = 1000; 0f at 500 }),
        label = "cursor"
    )

    LaunchedEffect(Unit) {
        while (true) { pulseState = (pulseState + 1) % 4; delay(700) }
    }
    LaunchedEffect(state) {
        if (state == ConnectionState.Scanning) {
            logMessages = listOf("USB tethering active...", "Starting broadcast listener...")
            val extras = listOf(
                "Binding to 192.168.42.0/24...",
                "Listening on UDP port 5353...",
                "Waiting for TethrLink broadcast...",
                "No server found yet, retrying..."
            )
            for (msg in extras) { delay(1800); logMessages = (logMessages + msg).takeLast(4) }
        }
    }

    val bg = Brush.verticalGradient(listOf(Color(0xFF070714), Color(0xFF0B0B1F)))

    Box(Modifier.fillMaxSize().background(bg)) {
        Column(Modifier.fillMaxSize()) {

            // ── Header ──────────────────────────────────────────────────────
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    androidx.compose.foundation.Image(
                        painter = painterResource(R.drawable.ic_launcher_foreground),
                        contentDescription = "Logo",
                        modifier = Modifier.size(28.dp).clip(RoundedCornerShape(8.dp))
                    )
                    Spacer(Modifier.width(8.dp))
                    Text("TethrLink", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.01).sp)
                }
                Box(Modifier.background(Color.White.copy(0.07f), RoundedCornerShape(20.dp)).padding(horizontal = 10.dp, vertical = 4.dp)) {
                    Text("v1.0", color = Color.White.copy(0.4f), fontSize = 11.sp)
                }
            }

            // ── Status bar (hidden in NoUsb, evolves through other states) ──
            AnimatedVisibility(
                visible = state != ConnectionState.NoUsb,
                enter = fadeIn(tween(400)) + slideInVertically(tween(400)) { -it },
                exit = fadeOut(tween(300)) + slideOutVertically(tween(300)) { -it }
            ) {
                AnimatedContent(
                    targetState = state,
                    transitionSpec = { fadeIn(tween(300)) togetherWith fadeOut(tween(200)) },
                    label = "statusBar"
                ) { s ->
                    ConnStatusBar_A(s, scanDotAlpha)
                }
            }

            // ── Main content ─────────────────────────────────────────────────
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 24.dp)
                    .padding(top = 8.dp, bottom = 32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                // Center visualization morphs between all 4 states
                AnimatedContent(
                    targetState = state,
                    transitionSpec = { fadeIn(tween(700)) togetherWith fadeOut(tween(500)) },
                    label = "viz"
                ) { s -> ConnCenterViz_A(s, pulseState, radarRotation) }

                Spacer(Modifier.height(8.dp))

                // Status badge
                AnimatedContent(
                    targetState = state,
                    transitionSpec = { fadeIn(tween(400)) togetherWith fadeOut(tween(300)) },
                    label = "badge"
                ) { s -> ConnBadge_A(s, scanDotAlpha) }

                Spacer(Modifier.height(16.dp))

                // Headline slides up on each state change
                AnimatedContent(
                    targetState = state,
                    transitionSpec = {
                        (fadeIn(tween(400)) + slideInVertically(tween(400)) { it / 3 }) togetherWith
                                (fadeOut(tween(250)) + slideOutVertically(tween(250)) { -it / 3 })
                    },
                    label = "headline"
                ) { s ->
                    Text(
                        text = when (s) {
                            ConnectionState.NoUsb -> "Waiting for USB-C"
                            ConnectionState.TetherOff -> "Enable USB Tethering"
                            ConnectionState.Scanning -> "Scanning for Server"
                            is ConnectionState.ServerFound -> "Server Found!"
                        },
                        color = if (s is ConnectionState.ServerFound) Color(0xFF4ADE80) else Color.White,
                        fontSize = 24.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = (-0.02).sp
                    )
                }

                Spacer(Modifier.height(10.dp))

                // Body text
                AnimatedContent(
                    targetState = state,
                    transitionSpec = { fadeIn(tween(500)) togetherWith fadeOut(tween(300)) },
                    label = "body"
                ) { s ->
                    Text(
                        text = when (s) {
                            ConnectionState.NoUsb -> "Connect your USB-C cable between your\nAndroid device and Linux machine to get\nstarted."
                            ConnectionState.TetherOff -> "USB-C is connected. Now enable USB Tethering\nin Android Settings to create a network\nlink with your Linux machine."
                            ConnectionState.Scanning -> "Make sure the TethrLink server is running\non your Linux machine."
                            is ConnectionState.ServerFound -> "Signal strength: Excellent  ·  ${s.ip}"
                        },
                        color = Color.White.copy(alpha = 0.45f),
                        fontSize = 14.sp,
                        lineHeight = 22.sp,
                        textAlign = TextAlign.Center
                    )
                }

                Spacer(Modifier.height(24.dp))

                // Action section — completely different content per state
                AnimatedContent(
                    targetState = state,
                    transitionSpec = { fadeIn(tween(500)) togetherWith fadeOut(tween(350)) },
                    label = "action"
                ) { s ->
                    when (s) {
                        ConnectionState.NoUsb -> ConnChecklist_A(doneSteps = 0)

                        ConnectionState.TetherOff -> Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Box(
                                modifier = Modifier.fillMaxWidth()
                                    .background(Brush.linearGradient(listOf(Color(0xFF6366F1), Color(0xFF8B5CF6))), RoundedCornerShape(14.dp))
                                    .clickable { onEnableTether() }
                                    .padding(16.dp),
                                contentAlignment = Alignment.Center
                            ) { Text("Open USB Tethering Settings", color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.Bold) }
                            Spacer(Modifier.height(8.dp))
                            Text("Settings → Network → Hotspot & Tethering", color = Color.White.copy(0.3f), fontSize = 11.sp)
                            Spacer(Modifier.height(20.dp))
                            ConnChecklist_A(doneSteps = 1)
                        }

                        ConnectionState.Scanning -> Column(
                            modifier = Modifier.fillMaxWidth()
                                .background(Color.Black.copy(0.4f), RoundedCornerShape(12.dp))
                                .border(1.dp, Color(0xFF6366F1).copy(0.2f), RoundedCornerShape(12.dp))
                                .padding(14.dp)
                        ) {
                            Text("▸ TETHRLINK LOG", color = Color(0xFF8B5CF6).copy(0.7f), fontSize = 10.sp, letterSpacing = 0.06.sp)
                            Spacer(Modifier.height(6.dp))
                            logMessages.forEachIndexed { i, msg ->
                                val c = if (i == logMessages.size - 1) Color(0xFFA7F3D0).copy(0.8f) else Color.White.copy(0.3f)
                                Row {
                                    Text("$ ", color = Color(0xFF6366F1).copy(0.6f), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                                    Text(msg, color = c, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                                }
                            }
                            Text("▋", color = Color(0xFF6366F1).copy(0.6f), fontSize = 11.sp, modifier = Modifier.alpha(if (cursorBlink > 0.5f) 1f else 0f))
                        }

                        is ConnectionState.ServerFound -> Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                            Column(
                                modifier = Modifier.fillMaxWidth()
                                    .background(Color.White.copy(0.04f), RoundedCornerShape(16.dp))
                                    .border(1.dp, Color.White.copy(0.08f), RoundedCornerShape(16.dp))
                                    .padding(16.dp)
                            ) {
                                Text("SERVER INFO", color = Color.White.copy(0.35f), fontSize = 10.sp, letterSpacing = 1.sp)
                                Spacer(Modifier.height(10.dp))
                                ConnInfoRow_A("Hostname", s.hostname)
                                Box(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(0.05f)))
                                ConnInfoRow_A("IP Address", s.ip)
                                Box(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(0.05f)))
                                ConnInfoRow_A("System", s.system)
                            }
                            Spacer(Modifier.height(20.dp))
                            Box(
                                modifier = Modifier.fillMaxWidth()
                                    .background(Brush.linearGradient(listOf(Color(0xFF6366F1), Color(0xFF8B5CF6))), RoundedCornerShape(14.dp))
                                    .clickable { onStartExtending() }
                                    .padding(16.dp),
                                contentAlignment = Alignment.Center
                            ) { Text("Start Extending  →", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold) }
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "${resolutions[selectedRes]} · ${refreshRates[selectedHz]} · Right",
                                color = Color.White.copy(0.25f), fontSize = 11.sp
                            )
                        }
                    }
                }
            }
        }
    }
}

// ── Status bar ────────────────────────────────────────────────────────────────

@Composable
private fun ConnStatusBar_A(state: ConnectionState, scanDotAlpha: Float) {
    Row(
        modifier = Modifier.fillMaxWidth()
            .padding(horizontal = 16.dp).padding(bottom = 4.dp)
            .background(Color.White.copy(0.04f), RoundedCornerShape(12.dp))
            .border(1.dp, Color.White.copy(0.08f), RoundedCornerShape(12.dp))
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        StatusPill_A("USB-C", Color(0xFF4ADE80), 1f)
        StatusDivider_A()
        StatusPill_A(
            "Tethering",
            if (state == ConnectionState.TetherOff) Color(0xFFEF4444) else Color(0xFF4ADE80),
            if (state == ConnectionState.TetherOff) scanDotAlpha else 1f
        )
        if (state == ConnectionState.Scanning || state is ConnectionState.ServerFound) {
            StatusDivider_A()
            StatusPill_A(
                if (state is ConnectionState.ServerFound) "Connected" else "Scanning",
                if (state is ConnectionState.ServerFound) Color(0xFF4ADE80) else Color(0xFF6366F1),
                if (state == ConnectionState.Scanning) scanDotAlpha else 1f
            )
        }
        if (state == ConnectionState.TetherOff) {
            Spacer(Modifier.weight(1f))
            Box(
                modifier = Modifier
                    .background(Color(0xFFEF4444).copy(0.15f), RoundedCornerShape(6.dp))
                    .border(1.dp, Color(0xFFEF4444).copy(0.3f), RoundedCornerShape(6.dp))
                    .padding(horizontal = 8.dp, vertical = 2.dp)
            ) { Text("Tethering OFF", color = Color(0xFFF87171), fontSize = 11.sp, fontWeight = FontWeight.SemiBold) }
        }
    }
}

@Composable
private fun StatusPill_A(label: String, dotColor: Color, dotAlpha: Float) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(7.dp).background(dotColor.copy(alpha = dotAlpha), CircleShape))
        Spacer(Modifier.width(6.dp))
        Text(label, color = Color.White.copy(0.5f), fontSize = 11.sp)
    }
}

@Composable
private fun StatusDivider_A() {
    Spacer(Modifier.width(12.dp))
    Box(Modifier.width(1.dp).height(14.dp).background(Color.White.copy(0.1f)))
    Spacer(Modifier.width(12.dp))
}

// ── Center visualization ──────────────────────────────────────────────────────

@Composable
private fun ConnCenterViz_A(state: ConnectionState, pulseState: Int, radarRotation: Float) {
    // Scale the entire visualization to 85% of available width, capped at 280dp.
    // This keeps rings proportional on both phones (360dp) and tablets (800dp+).
    BoxWithConstraints(Modifier.fillMaxWidth()) {
        val viz = minOf(maxWidth * 0.85f, 280.dp)
        val s = viz.value / 280f  // scale ratio applied to every fixed dp size

        Box(Modifier.size(viz).align(Alignment.Center), contentAlignment = Alignment.Center) {
            when (state) {
                ConnectionState.NoUsb -> {
                    Box(Modifier.size(225.dp * s).border(1.5.dp, Color(0xFF6366F1).copy(alpha = if (pulseState == 0) 0.4f else 0.1f), CircleShape))
                    Box(Modifier.size(175.dp * s).border(1.5.dp, Color(0xFF6366F1).copy(alpha = if (pulseState == 1) 0.5f else 0.15f), CircleShape))
                    Box(Modifier.size(125.dp * s).border(1.5.dp, Color(0xFF6366F1).copy(alpha = if (pulseState == 2) 0.6f else 0.2f), CircleShape))
                    Box(
                        modifier = Modifier.size(75.dp * s)
                            .background(Brush.linearGradient(listOf(Color(0xFF6366F1).copy(if (pulseState == 3) 0.3f else 0.15f), Color(0xFF8B5CF6).copy(if (pulseState == 3) 0.3f else 0.15f))), CircleShape)
                            .border(1.5.dp, Color(0xFF6366F1).copy(0.35f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Canvas(Modifier.size(34.dp * s)) {
                            val path = Path().apply {
                                moveTo(size.width * 0.6f, 0f); lineTo(size.width * 0.2f, size.height * 0.55f)
                                lineTo(size.width * 0.45f, size.height * 0.55f); lineTo(size.width * 0.35f, size.height)
                                lineTo(size.width * 0.8f, size.height * 0.4f); lineTo(size.width * 0.55f, size.height * 0.4f); close()
                            }
                            drawPath(path, Color(0xFF8B5CF6), style = Stroke(2.dp.toPx(), join = StrokeJoin.Round))
                        }
                    }
                }

                ConnectionState.TetherOff -> {
                    Box(Modifier.size(200.dp * s).border(1.dp, Color(0xFF6366F1).copy(0.1f), CircleShape))
                    Box(Modifier.size(140.dp * s).border(1.dp, Color(0xFF6366F1).copy(0.18f), CircleShape))
                    Box(
                        modifier = Modifier.size(88.dp * s)
                            .background(Brush.linearGradient(listOf(Color(0xFF6366F1).copy(0.18f), Color(0xFF8B5CF6).copy(0.22f))), CircleShape)
                            .border(1.5.dp, Color(0xFF6366F1).copy(0.4f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Canvas(Modifier.size(38.dp * s)) {
                            val sw = 2.2.dp.toPx(); val cx = size.width / 2f; val cy = size.height / 2f; val r = size.minDimension * 0.34f
                            drawArc(Color(0xFF8B5CF6), startAngle = -45f, sweepAngle = 270f, useCenter = false, topLeft = Offset(cx - r, cy - r), size = Size(r * 2f, r * 2f), style = Stroke(sw, cap = StrokeCap.Round))
                            drawLine(Brush.verticalGradient(listOf(Color(0xFF6366F1), Color(0xFF8B5CF6)), startY = cy - r * 1.35f, endY = cy - r * 0.1f), Offset(cx, cy - r * 1.35f), Offset(cx, cy - r * 0.1f), sw, StrokeCap.Round)
                        }
                    }
                }

                ConnectionState.Scanning -> {
                    RippleLayer(delayMillis = 0)
                    RippleLayer(delayMillis = 900)
                    Box(
                        modifier = Modifier.size(140.dp * s)
                            .background(Color(0xFF6366F1).copy(0.05f), CircleShape)
                            .border(1.dp, Color(0xFF6366F1).copy(0.15f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Canvas(Modifier.fillMaxSize().then(Modifier.graphicsLayer { rotationZ = radarRotation })) {
                            drawCircle(brush = Brush.sweepGradient(0f to Color.Transparent, 0.5f to Color.Transparent, 1f to Color(0xFF8B5CF6).copy(0.4f)), radius = size.width / 4, style = Stroke(size.width / 2))
                            drawLine(Color(0xFF8B5CF6).copy(0.8f), center, center.copy(x = center.x + size.width / 2), 2.dp.toPx())
                        }
                        Canvas(Modifier.fillMaxSize()) {
                            drawCircle(Color(0xFF6366F1).copy(0.2f), (size.width / 2) * 0.7f, style = Stroke(1.dp.toPx()))
                            drawCircle(Color(0xFF6366F1).copy(0.2f), (size.width / 2) * 0.4f, style = Stroke(1.dp.toPx()))
                            drawLine(Color(0xFF6366F1).copy(0.1f), Offset(center.x, 0f), Offset(center.x, size.height), 1.dp.toPx())
                            drawLine(Color(0xFF6366F1).copy(0.1f), Offset(0f, center.y), Offset(size.width, center.y), 1.dp.toPx())
                        }
                    }
                }

                is ConnectionState.ServerFound -> {
                    Box(Modifier.size(200.dp * s).border(1.dp, Color(0xFF4ADE80).copy(0.1f), CircleShape))
                    Box(Modifier.size(140.dp * s).border(1.dp, Color(0xFF4ADE80).copy(0.18f), CircleShape))
                    Box(
                        modifier = Modifier.size(88.dp * s)
                            .background(Brush.linearGradient(listOf(Color(0xFF4ADE80).copy(0.15f), Color(0xFF22C55E).copy(0.2f))), CircleShape)
                            .border(1.5.dp, Color(0xFF4ADE80).copy(0.45f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Canvas(Modifier.size(38.dp * s)) {
                            val path = Path().apply {
                                moveTo(size.width * 0.18f, size.height * 0.5f)
                                lineTo(size.width * 0.42f, size.height * 0.73f)
                                lineTo(size.width * 0.82f, size.height * 0.28f)
                            }
                            drawPath(path, Color(0xFF4ADE80), style = Stroke(2.5.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
                        }
                    }
                }
            }
        }
    }
}

// ── Status badge ──────────────────────────────────────────────────────────────

@Composable
private fun ConnBadge_A(state: ConnectionState, scanDotAlpha: Float) {
    val accentColor = when (state) {
        ConnectionState.NoUsb, ConnectionState.TetherOff -> Color(0xFFEF4444)
        ConnectionState.Scanning -> Color(0xFF6366F1)
        is ConnectionState.ServerFound -> Color(0xFF4ADE80)
    }
    val textColor = when (state) {
        ConnectionState.NoUsb, ConnectionState.TetherOff -> Color(0xFFF87171)
        ConnectionState.Scanning -> Color(0xFF818CF8)
        is ConnectionState.ServerFound -> Color(0xFF86EFAC)
    }
    val label = when (state) {
        ConnectionState.NoUsb -> "No USB Connection"
        ConnectionState.TetherOff -> "Tethering Disabled"
        ConnectionState.Scanning -> "Scanning..."
        is ConnectionState.ServerFound -> "Server Found"
    }
    val dotAlpha = if (state == ConnectionState.Scanning) scanDotAlpha else 1f

    Row(
        modifier = Modifier
            .background(accentColor.copy(0.12f), RoundedCornerShape(20.dp))
            .border(1.dp, accentColor.copy(0.25f), RoundedCornerShape(20.dp))
            .padding(horizontal = 14.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(7.dp).background(accentColor.copy(alpha = dotAlpha), CircleShape))
        Spacer(Modifier.width(6.dp))
        Text(label, color = textColor, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
    }
}

// ── Checklist ─────────────────────────────────────────────────────────────────

@Composable
private fun ConnChecklist_A(doneSteps: Int) {
    Column(
        modifier = Modifier.fillMaxWidth()
            .background(Color.White.copy(0.04f), RoundedCornerShape(16.dp))
            .border(1.dp, Color.White.copy(0.08f), RoundedCornerShape(16.dp))
            .padding(16.dp)
    ) {
        ConnStepItem_A("1", "Connect USB-C cable", done = doneSteps >= 1, active = doneSteps == 0, showBorder = true)
        ConnStepItem_A("2", "Enable USB Tethering", done = doneSteps >= 2, active = doneSteps == 1, showBorder = true)
        ConnStepItem_A("3", "Run TethrLink on Linux", done = doneSteps >= 3, active = doneSteps == 2, showBorder = false)
    }
}

@Composable
private fun ConnStepItem_A(step: String, label: String, done: Boolean, active: Boolean, showBorder: Boolean) {
    val circleColor by animateColorAsState(
        targetValue = if (done) Color(0xFF4ADE80) else if (active) Color(0xFF6366F1) else Color(0xFF6366F1).copy(0.3f),
        animationSpec = tween(600), label = "c$step"
    )
    val textAlpha by animateFloatAsState(
        targetValue = if (done) 0.85f else if (active) 0.7f else 0.35f,
        animationSpec = tween(600), label = "t$step"
    )
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(26.dp).background(circleColor.copy(0.15f), CircleShape).border(1.5.dp, circleColor.copy(0.5f), CircleShape), contentAlignment = Alignment.Center) {
            if (done) {
                Canvas(Modifier.size(12.dp)) {
                    val path = Path().apply { moveTo(size.width * 0.1f, size.height * 0.5f); lineTo(size.width * 0.4f, size.height * 0.8f); lineTo(size.width * 0.9f, size.height * 0.2f) }
                    drawPath(path, Color(0xFF4ADE80), style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
                }
            } else { Text(step, color = circleColor, fontSize = 12.sp, fontWeight = FontWeight.Bold) }
        }
        Spacer(Modifier.width(12.dp))
        Text(label, color = Color.White.copy(textAlpha), fontSize = 13.sp)
    }
    if (showBorder) Box(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(0.05f)))
}

// ── Server info row ───────────────────────────────────────────────────────────

@Composable
private fun ConnInfoRow_A(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 9.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = Color.White.copy(0.4f), fontSize = 12.sp)
        Text(value, color = Color.White.copy(0.8f), fontSize = 12.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
fun RippleLayer(delayMillis: Int) {
    var isRunning by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        delay(delayMillis.toLong())
        isRunning = true
    }

    if (isRunning) {
        val infiniteTransition = rememberInfiniteTransition()
        val scale by infiniteTransition.animateFloat(
            initialValue = 0.2f, targetValue = 1.8f,
            animationSpec = infiniteRepeatable(animation = tween(1800, easing = LinearOutSlowInEasing), repeatMode = RepeatMode.Restart)
        )
        val alpha by infiniteTransition.animateFloat(
            initialValue = 5f, targetValue = 0f,
            animationSpec = infiniteRepeatable(animation = tween(1800, easing = LinearOutSlowInEasing), repeatMode = RepeatMode.Restart)
        )
        Box(
            modifier = Modifier
                .size(80.dp)
                .scale(scale)
                .alpha(alpha)
                .border(1.5.dp, Color(0xFF6366F1).copy(alpha = 0.3f), CircleShape)
        )
    }
}

// ── Previews ─────────────────────────────────────────────────────────────────

@Preview(showBackground = true, backgroundColor = 0xFF070714)
@Composable fun PreviewConnA_NoUSB() = ConnectionScreen_OptionA(ConnectionState.NoUsb, {}, {})

@Preview(showBackground = true, backgroundColor = 0xFF070714)
@Composable fun PreviewConnA_TetherOff() = ConnectionScreen_OptionA(ConnectionState.TetherOff, {}, {})

@Preview(showBackground = true, backgroundColor = 0xFF070714)
@Composable fun PreviewConnA_Scanning() = ConnectionScreen_OptionA(ConnectionState.Scanning, {}, {})

@Preview(showBackground = true, backgroundColor = 0xFF070714)
@Composable fun PreviewConnA_ServerFound() = ConnectionScreen_OptionA(ConnectionState.ServerFound("prince-desktop", "192.168.42.129", "Linux x86_64"), {}, {})
