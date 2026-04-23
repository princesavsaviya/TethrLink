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

// Uses ConnectionState sealed class defined in ConnectionScreen_OptionA.kt (same package).
// All 4 states are represented as expanding step cards.
// The active step expands to show its full visualization and controls.

@Composable
fun ConnectionScreen_OptionC(
    state: ConnectionState,
    onEnableTether: () -> Unit,
    onStartExtending: () -> Unit
) {
    // Derived step flags
    val step1Done = state != ConnectionState.NoUsb
    val step2Done = state == ConnectionState.Scanning || state is ConnectionState.ServerFound
    val step3Done = state is ConnectionState.ServerFound

    val step1Active = state == ConnectionState.NoUsb
    val step2Active = state == ConnectionState.TetherOff
    val step3Active = state == ConnectionState.Scanning
    val step4Active = state is ConnectionState.ServerFound

    var pulseState by remember { mutableStateOf(0) }
    var logMessages by remember { mutableStateOf(listOf("USB tethering active...", "Starting broadcast listener...")) }

    val resolutions = remember { listOf("1920×1080", "2560×1440", "3840×2160", "1280×720") }
    val refreshRates = remember { listOf("60 Hz", "120 Hz", "144 Hz") }
    var selectedRes by remember { mutableIntStateOf(0) }
    var selectedHz by remember { mutableIntStateOf(0) }

    val inf = rememberInfiniteTransition(label = "conn_c")
    val radarRotation by inf.animateFloat(
        initialValue = 0f, targetValue = 360f,
        animationSpec = infiniteRepeatable(tween(5000, easing = LinearEasing)),
        label = "radar"
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

            // ── Step cards ───────────────────────────────────────────────────
            Column(
                modifier = Modifier
                    .weight(1f).fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 20.dp)
                    .padding(top = 8.dp, bottom = 32.dp),
                verticalArrangement = Arrangement.Center
            ) {
                Text(
                    "SETUP PROGRESS",
                    color = Color.White.copy(0.3f),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 0.08.sp,
                    modifier = Modifier.padding(start = 4.dp, bottom = 16.dp)
                )

                // ── Step 1: USB-C Connection ─────────────────────────────
                ConnStepCard_C(
                    stepNumber = 1,
                    title = "USB-C Connection",
                    subtitle = "Physical cable required",
                    isActive = step1Active,
                    isDone = step1Done
                ) {
                    // Mini pulse visualization — scales to card width
                    BoxWithConstraints(Modifier.fillMaxWidth().padding(vertical = 20.dp)) {
                        val viz = minOf(maxWidth * 0.75f, 160.dp)
                        val s = viz.value / 160f
                        Box(Modifier.size(viz).align(Alignment.Center), contentAlignment = Alignment.Center) {
                            Box(Modifier.size(155.dp * s).border(1.dp, Color(0xFF6366F1).copy(alpha = if (pulseState == 0) 0.4f else 0.08f), CircleShape))
                            Box(Modifier.size(115.dp * s).border(1.dp, Color(0xFF6366F1).copy(alpha = if (pulseState == 1) 0.5f else 0.12f), CircleShape))
                            Box(Modifier.size(80.dp * s).border(1.dp, Color(0xFF6366F1).copy(alpha = if (pulseState == 2) 0.6f else 0.18f), CircleShape))
                            Box(
                                modifier = Modifier.size(50.dp * s)
                                    .background(Brush.linearGradient(listOf(Color(0xFF6366F1).copy(if (pulseState == 3) 0.3f else 0.12f), Color(0xFF8B5CF6).copy(if (pulseState == 3) 0.3f else 0.12f))), CircleShape)
                                    .border(1.dp, Color(0xFF6366F1).copy(0.3f), CircleShape),
                                contentAlignment = Alignment.Center
                            ) {
                                Canvas(Modifier.size(22.dp * s)) {
                                    val path = Path().apply {
                                        moveTo(size.width * 0.6f, 0f); lineTo(size.width * 0.2f, size.height * 0.55f)
                                        lineTo(size.width * 0.45f, size.height * 0.55f); lineTo(size.width * 0.35f, size.height)
                                        lineTo(size.width * 0.8f, size.height * 0.4f); lineTo(size.width * 0.55f, size.height * 0.4f); close()
                                    }
                                    drawPath(path, Color(0xFF8B5CF6), style = Stroke(1.5.dp.toPx(), join = StrokeJoin.Round))
                                }
                            }
                        }
                    }
                    Text(
                        "Plug a USB-C cable between your Android\ndevice and your Linux machine.",
                        color = Color.White.copy(0.45f), fontSize = 13.sp, lineHeight = 20.sp,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth().padding(bottom = 20.dp)
                    )
                }

                ConnStepConnector_C(reached = step1Done)

                // ── Step 2: Enable USB Tethering ─────────────────────────
                ConnStepCard_C(
                    stepNumber = 2,
                    title = "Enable USB Tethering",
                    subtitle = "Android network settings",
                    isActive = step2Active,
                    isDone = step2Done
                ) {
                    // Power icon visualization — scales to card width
                    BoxWithConstraints(Modifier.fillMaxWidth().padding(vertical = 20.dp)) {
                        val viz = minOf(maxWidth * 0.75f, 160.dp)
                        val s = viz.value / 160f
                        Box(Modifier.size(viz).align(Alignment.Center), contentAlignment = Alignment.Center) {
                            Box(Modifier.size(155.dp * s).border(1.dp, Color(0xFF6366F1).copy(0.08f), CircleShape))
                            Box(Modifier.size(115.dp * s).border(1.dp, Color(0xFF6366F1).copy(0.13f), CircleShape))
                            Box(
                                modifier = Modifier.size(72.dp * s)
                                    .background(Brush.linearGradient(listOf(Color(0xFF6366F1).copy(0.18f), Color(0xFF8B5CF6).copy(0.22f))), CircleShape)
                                    .border(1.5.dp, Color(0xFF6366F1).copy(0.4f), CircleShape),
                                contentAlignment = Alignment.Center
                            ) {
                                Canvas(Modifier.size(34.dp * s)) {
                                    val sw = 2.dp.toPx(); val cx = size.width / 2f; val cy = size.height / 2f; val r = size.minDimension * 0.34f
                                    drawArc(Color(0xFF8B5CF6), -45f, 270f, false, Offset(cx - r, cy - r), Size(r * 2f, r * 2f), style = Stroke(sw, cap = StrokeCap.Round))
                                    drawLine(Brush.verticalGradient(listOf(Color(0xFF6366F1), Color(0xFF8B5CF6)), startY = cy - r * 1.35f, endY = cy - r * 0.1f), Offset(cx, cy - r * 1.35f), Offset(cx, cy - r * 0.1f), sw, StrokeCap.Round)
                                }
                            }
                        }
                    }
                    Text(
                        "USB-C is connected. Open Tethering settings\nto create the network link.",
                        color = Color.White.copy(0.45f), fontSize = 13.sp, lineHeight = 20.sp,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth().padding(bottom = 16.dp)
                    )
                    Box(
                        modifier = Modifier.fillMaxWidth()
                            .background(Brush.linearGradient(listOf(Color(0xFF6366F1), Color(0xFF8B5CF6))), RoundedCornerShape(12.dp))
                            .clickable { onEnableTether() }.padding(14.dp),
                        contentAlignment = Alignment.Center
                    ) { Text("Open USB Tethering Settings", color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.Bold) }
                    Text(
                        "Settings → Network → Hotspot & Tethering",
                        color = Color.White.copy(0.28f), fontSize = 11.sp, textAlign = TextAlign.Center,
                        modifier = Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 16.dp)
                    )
                }

                ConnStepConnector_C(reached = step2Done)

                // ── Step 3: Linux Server Scan ─────────────────────────────
                ConnStepCard_C(
                    stepNumber = 3,
                    title = "Scan for Linux Server",
                    subtitle = "Auto-discovery over USB network",
                    isActive = step3Active,
                    isDone = step3Done
                ) {
                    // Radar visualization — scales to card width
                    BoxWithConstraints(Modifier.fillMaxWidth().padding(vertical = 20.dp)) {
                        val viz = minOf(maxWidth * 0.75f, 160.dp)
                        val s = viz.value / 160f
                        Box(Modifier.size(viz).align(Alignment.Center), contentAlignment = Alignment.Center) {
                            Box(
                                modifier = Modifier.size(110.dp * s)
                                    .background(Color(0xFF6366F1).copy(0.05f), CircleShape)
                                    .border(1.dp, Color(0xFF6366F1).copy(0.15f), CircleShape),
                                contentAlignment = Alignment.Center
                            ) {
                                Canvas(Modifier.fillMaxSize().then(Modifier.graphicsLayer { rotationZ = radarRotation })) {
                                    drawCircle(brush = Brush.sweepGradient(0f to Color.Transparent, 0.5f to Color.Transparent, 1f to Color(0xFF8B5CF6).copy(0.4f)), radius = size.width / 4, style = Stroke(size.width / 2))
                                    drawLine(Color(0xFF8B5CF6).copy(0.8f), center, center.copy(x = center.x + size.width / 2), 2.dp.toPx())
                                }
                                Canvas(Modifier.fillMaxSize()) {
                                    drawCircle(Color(0xFF6366F1).copy(0.2f), (size.width /2) * 0.7f, style = Stroke(1.dp.toPx()))
                                    drawCircle(Color(0xFF6366F1).copy(0.2f), (size.width / 2) * 0.4f, style = Stroke(1.dp.toPx()))
                                    drawLine(Color(0xFF6366F1).copy(0.1f), Offset(center.x, 0f), Offset(center.x, size.height), 1.dp.toPx())
                                    drawLine(Color(0xFF6366F1).copy(0.1f), Offset(0f, center.y), Offset(size.width, center.y), 1.dp.toPx())
                                }
                            }
                        }
                    }
                    // Terminal log
                    Column(
                        modifier = Modifier.fillMaxWidth()
                            .background(Color.Black.copy(0.4f), RoundedCornerShape(10.dp))
                            .border(1.dp, Color(0xFF6366F1).copy(0.2f), RoundedCornerShape(10.dp))
                            .padding(12.dp)
                    ) {
                        Text("▸ TETHERLINK LOG", color = Color(0xFF8B5CF6).copy(0.7f), fontSize = 10.sp, letterSpacing = 0.06.sp)
                        Spacer(Modifier.height(6.dp))
                        logMessages.forEachIndexed { i, msg ->
                            val c = if (i == logMessages.size - 1) Color(0xFFA7F3D0).copy(0.8f) else Color.White.copy(0.3f)
                            Row {
                                Text("$ ", color = Color(0xFF6366F1).copy(0.6f), fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                                Text(msg, color = c, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                            }
                        }
                        Text("▋", color = Color(0xFF6366F1).copy(0.6f), fontSize = 10.sp, modifier = Modifier.alpha(if (cursorBlink > 0.5f) 1f else 0f))
                    }
                    Spacer(Modifier.height(16.dp))
                }

                ConnStepConnector_C(reached = step3Done)

                // ── Step 4: Server Found ──────────────────────────────────
                ConnStepCard_C(
                    stepNumber = 4,
                    title = "Server Connected",
                    subtitle = "Ready to extend display",
                    isActive = step4Active,
                    isDone = false
                ) {
                    val serverState = state as? ConnectionState.ServerFound ?: return@ConnStepCard_C

                    // Success banner
                    Box(
                        modifier = Modifier.fillMaxWidth().padding(top = 16.dp)
                            .background(Color(0xFF4ADE80).copy(0.1f), RoundedCornerShape(12.dp))
                            .border(1.dp, Color(0xFF4ADE80).copy(0.2f), RoundedCornerShape(12.dp))
                            .padding(12.dp)
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Box(
                                modifier = Modifier.size(36.dp)
                                    .background(Color(0xFF4ADE80).copy(0.15f), RoundedCornerShape(10.dp)),
                                contentAlignment = Alignment.Center
                            ) {
                                Canvas(Modifier.size(20.dp)) {
                                    val path = Path().apply {
                                        moveTo(size.width * 0.15f, size.height * 0.5f)
                                        lineTo(size.width * 0.42f, size.height * 0.75f)
                                        lineTo(size.width * 0.85f, size.height * 0.25f)
                                    }
                                    drawPath(path, Color(0xFF4ADE80), style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
                                }
                            }
                            Spacer(Modifier.width(12.dp))
                            Column {
                                Text("TethrLink Server Found!", color = Color(0xFF4ADE80), fontSize = 13.sp, fontWeight = FontWeight.Bold)
                                Text("Signal strength: Excellent", color = Color.White.copy(0.4f), fontSize = 11.sp)
                            }
                        }
                    }

                    Spacer(Modifier.height(12.dp))

                    // Server info
                    Column(
                        modifier = Modifier.fillMaxWidth()
                            .background(Color.White.copy(0.04f), RoundedCornerShape(12.dp))
                            .border(1.dp, Color.White.copy(0.08f), RoundedCornerShape(12.dp))
                            .padding(14.dp)
                    ) {
                        Text("SERVER INFO", color = Color.White.copy(0.35f), fontSize = 10.sp, letterSpacing = 1.sp)
                        Spacer(Modifier.height(8.dp))
                        ConnInfoRow_C("Hostname", serverState.hostname)
                        Box(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(0.05f)))
                        ConnInfoRow_C("IP Address", serverState.ip)
                        Box(Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(0.05f)))
                        ConnInfoRow_C("System", serverState.system)
                    }

                    Spacer(Modifier.height(16.dp))

                    // Start button
                    Box(
                        modifier = Modifier.fillMaxWidth()
                            .background(Brush.linearGradient(listOf(Color(0xFF6366F1), Color(0xFF8B5CF6))), RoundedCornerShape(14.dp))
                            .clickable { onStartExtending() }.padding(16.dp),
                        contentAlignment = Alignment.Center
                    ) { Text("Start Extending  →", color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold) }
                    Text(
                        "${resolutions[selectedRes]} · ${refreshRates[selectedHz]} · Right",
                        color = Color.White.copy(0.25f), fontSize = 11.sp,
                        modifier = Modifier.padding(top = 8.dp, bottom = 16.dp)
                    )
                }
            }
        }
    }
}

// ── Step card ─────────────────────────────────────────────────────────────────

@Composable
private fun ConnStepCard_C(
    stepNumber: Int,
    title: String,
    subtitle: String,
    isActive: Boolean,
    isDone: Boolean,
    body: (@Composable ColumnScope.() -> Unit)? = null
) {
    val borderColor by animateColorAsState(
        targetValue = when {
            isDone -> Color(0xFF4ADE80).copy(0.35f)
            isActive -> Color(0xFF6366F1).copy(0.45f)
            else -> Color.White.copy(0.07f)
        },
        animationSpec = tween(600), label = "border$stepNumber"
    )
    val bgAlpha by animateFloatAsState(
        targetValue = if (isActive) 0.06f else 0.03f,
        animationSpec = tween(600), label = "bg$stepNumber"
    )
    val circleColor by animateColorAsState(
        targetValue = if (isDone) Color(0xFF4ADE80) else if (isActive) Color(0xFF6366F1) else Color(0xFF6366F1).copy(0.3f),
        animationSpec = tween(600), label = "circle$stepNumber"
    )
    val titleAlpha by animateFloatAsState(
        targetValue = if (isDone || isActive) 1f else 0.4f,
        animationSpec = tween(600), label = "title$stepNumber"
    )

    Column(
        modifier = Modifier.fillMaxWidth()
            .animateContentSize(spring(stiffness = Spring.StiffnessMediumLow))
            .background(Color.White.copy(bgAlpha), RoundedCornerShape(16.dp))
            .border(1.dp, borderColor, RoundedCornerShape(16.dp))
            .padding(16.dp)
    ) {
        // Header row
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Box(
                modifier = Modifier.size(32.dp)
                    .background(circleColor.copy(0.15f), CircleShape)
                    .border(1.5.dp, circleColor.copy(0.5f), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                if (isDone) {
                    Canvas(Modifier.size(14.dp)) {
                        val path = Path().apply { moveTo(size.width * 0.1f, size.height * 0.5f); lineTo(size.width * 0.4f, size.height * 0.8f); lineTo(size.width * 0.9f, size.height * 0.2f) }
                        drawPath(path, Color(0xFF4ADE80), style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
                    }
                } else { Text("$stepNumber", color = circleColor, fontSize = 13.sp, fontWeight = FontWeight.Bold) }
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(title, color = Color.White.copy(titleAlpha), fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                Text(subtitle, color = Color.White.copy(0.35f), fontSize = 12.sp)
            }
            if (isActive) Box(Modifier.size(8.dp).background(Color(0xFF6366F1), CircleShape))
            if (isDone) Box(
                Modifier.background(Color(0xFF4ADE80).copy(0.12f), RoundedCornerShape(6.dp))
                    .border(1.dp, Color(0xFF4ADE80).copy(0.3f), RoundedCornerShape(6.dp))
                    .padding(horizontal = 8.dp, vertical = 3.dp)
            ) { Text("Done", color = Color(0xFF4ADE80), fontSize = 11.sp, fontWeight = FontWeight.SemiBold) }
        }

        // Expandable body
        AnimatedVisibility(
            visible = isActive && body != null,
            enter = fadeIn(tween(400)) + expandVertically(tween(500, easing = FastOutSlowInEasing), expandFrom = Alignment.Top),
            exit = fadeOut(tween(300)) + shrinkVertically(tween(400, easing = FastOutSlowInEasing), shrinkTowards = Alignment.Top)
        ) {
            Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                Box(Modifier.fillMaxWidth().padding(top = 14.dp).height(1.dp).background(Color.White.copy(0.07f)))
                body?.invoke(this)
            }
        }
    }
}

// ── Step connector ────────────────────────────────────────────────────────────

@Composable
private fun ConnStepConnector_C(reached: Boolean) {
    val color by animateColorAsState(
        targetValue = if (reached) Color(0xFF4ADE80).copy(0.4f) else Color(0xFF6366F1).copy(0.2f),
        animationSpec = tween(700), label = "conn"
    )
    Box(Modifier.padding(start = 31.dp).width(2.dp).height(20.dp).background(color, RoundedCornerShape(1.dp)))
}

// ── Server info row ───────────────────────────────────────────────────────────

@Composable
private fun ConnInfoRow_C(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = Color.White.copy(0.4f), fontSize = 12.sp)
        Text(value, color = Color.White.copy(0.8f), fontSize = 12.sp, fontWeight = FontWeight.Medium)
    }
}

// ── Previews ─────────────────────────────────────────────────────────────────

@Preview(showBackground = true, backgroundColor = 0xFF070714)
@Composable fun PreviewConnC_NoUSB() = ConnectionScreen_OptionC(ConnectionState.NoUsb, {}, {})

@Preview(showBackground = true, backgroundColor = 0xFF070714)
@Composable fun PreviewConnC_TetherOff() = ConnectionScreen_OptionC(ConnectionState.TetherOff, {}, {})

@Preview(showBackground = true, backgroundColor = 0xFF070714)
@Composable fun PreviewConnC_Scanning() = ConnectionScreen_OptionC(ConnectionState.Scanning, {}, {})

@Preview(showBackground = true, backgroundColor = 0xFF070714)
@Composable fun PreviewConnC_ServerFound() = ConnectionScreen_OptionC(ConnectionState.ServerFound("prince-desktop", "192.168.42.129", "Linux x86_64"), {}, {})
