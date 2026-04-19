package com.tetherlink.ui

import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tetherlink.R
import kotlinx.coroutines.delay

@Composable
fun ScanningScreen(onServerFound: () -> Unit) {
    var dots by remember { mutableStateOf(0) }
    var logMessages by remember { mutableStateOf(listOf("USB tethering active...", "Starting broadcast listener...")) }
    val infiniteTransition = rememberInfiniteTransition()
    val rotation by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 360f,
        animationSpec = infiniteRepeatable(
            animation = tween(5000, easing = LinearEasing),
            repeatMode = RepeatMode.Restart
        )
    )
    LaunchedEffect(Unit) {
        while (true) {
            dots = (dots + 1) % 4
            delay(500)
        }
    }
    
    LaunchedEffect(Unit) {
        val extraLogs = listOf(
            "Binding to 192.168.42.0/24...",
            "Listening on UDP port 5353...",
            "Waiting for TetherLink broadcast...",
            "No server found yet, retrying..."
        )
        for (msg in extraLogs) {
            delay(1800)
            logMessages = (logMessages + listOf(msg)).takeLast(4)
        }
    }
    
    val backgroundBrush = Brush.verticalGradient(
        colors = listOf(Color(0xFF070714), Color(0xFF0B0B1F))
    )
    
    Box(modifier = Modifier
        .fillMaxSize()
        .background(backgroundBrush)) {
        Column(modifier = Modifier.fillMaxSize()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                androidx.compose.foundation.Image(
                    painter = painterResource(id = R.drawable.ic_launcher_foreground),
                    contentDescription = "Logo",
                    modifier = Modifier
                        .size(28.dp)
                        .clip(RoundedCornerShape(8.dp))
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text("TetherLink", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.Bold)
            }
            
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp)
                    .background(Color.White.copy(alpha = 0.04f), RoundedCornerShape(12.dp))
                    .border(1.dp, Color.White.copy(alpha = 0.08f), RoundedCornerShape(12.dp))
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(modifier = Modifier
                        .size(7.dp)
                        .background(Color(0xFF4ADE80), CircleShape))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("USB-C", color = Color.White.copy(alpha = 0.5f), fontSize = 11.sp)
                }
                Spacer(modifier = Modifier.width(12.dp))
                Box(modifier = Modifier
                    .width(1.dp)
                    .height(14.dp)
                    .background(Color.White.copy(alpha = 0.1f)))
                Spacer(modifier = Modifier.width(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(modifier = Modifier
                        .size(7.dp)
                        .background(Color(0xFF4ADE80), CircleShape))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("Tethering", color = Color.White.copy(alpha = 0.5f), fontSize = 11.sp)
                }
                Spacer(modifier = Modifier.width(12.dp))
                Box(modifier = Modifier
                    .width(1.dp)
                    .height(14.dp)
                    .background(Color.White.copy(alpha = 0.1f)))
                Spacer(modifier = Modifier.width(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    val infiniteTransition = rememberInfiniteTransition()
                    val alpha by infiniteTransition.animateFloat(
                        initialValue = 0.4f,
                        targetValue = 1f,
                        animationSpec = infiniteRepeatable(animation = tween(600), repeatMode = RepeatMode.Reverse)
                    )
                    Box(modifier = Modifier
                        .size(7.dp)
                        .background(Color(0xFF6366F1).copy(alpha = alpha), CircleShape))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("Scanning", color = Color.White.copy(alpha = 0.5f), fontSize = 11.sp)
                }
            }
            
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 24.dp)
                    .padding(bottom = 16.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Box(
                    modifier = Modifier
                        .size(200.dp)
                        .padding(bottom = 24.dp),
                    contentAlignment = Alignment.Center
                ) {
                    // Existing Ripples
                    for (i in 0..1) {
                        RippleLayer(delayMillis = i * 75000)
                    }

                    // The Radar Container
                    Box(
                        modifier = Modifier
                            .size(140.dp) // Slightly larger to contain the radar rings
                            .background(Color(0xFF6366F1).copy(alpha = 0.05f), CircleShape)
                            .border(1.dp, Color(0xFF6366F1).copy(alpha = 0.15f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Canvas(modifier = Modifier
                            .fillMaxSize()
                            .graphicsLayer { rotationZ = rotation }) {
                            val sweepGradient = Brush.sweepGradient(
                                0f to Color.Transparent,
                                0.5f to Color.Transparent,
                                1.0f to Color(0xFF8B5CF6).copy(alpha = 0.4f)
                            )

                            // 1. The Rotating Sweep (The "Laser" part)
                            drawCircle(
                                brush = sweepGradient,
                                radius = size.width / 4,
                                style = Stroke(width = size.width / 2) // Fills the circle with the gradient
                            )

                            // 2. The Leading Edge (The bright line at the front of the sweep)
                            drawLine(
                                color = Color(0xFF8B5CF6).copy(alpha = 0.8f),
                                start = center,
                                end = center.copy(x = center.x + (size.width / 2)),
                                strokeWidth = 2.dp.toPx()
                            )
                        }

                        // 3. Static Elements (Crosshairs that don't rotate)
                        Canvas(modifier = Modifier.fillMaxSize()) {
                            // Rings
                            drawCircle(color = Color(0xFF6366F1).copy(alpha = 0.2f), radius = (size.width / 2) * 0.7f, style = Stroke(width = 1.dp.toPx()))
                            drawCircle(color = Color(0xFF6366F1).copy(alpha = 0.2f), radius = (size.width / 2) * 0.4f, style = Stroke(width = 1.dp.toPx()))

                            // Vertical & Horizontal crosshairs
                            drawLine(
                                color = Color(0xFF6366F1).copy(alpha = 0.1f),
                                start = Offset(center.x, 0f),
                                end = Offset(center.x, size.height),
                                strokeWidth = 1.dp.toPx()
                            )
                            drawLine(
                                color = Color(0xFF6366F1).copy(alpha = 0.1f),
                                start = Offset(0f, center.y),
                                end = Offset(size.width, center.y),
                                strokeWidth = 1.dp.toPx()
                            )
                        }

                        // 4. The Core (The "Node" icon in the middle)
                        Box(
                            modifier = Modifier
                                .size(0.dp)
                                .background(Color(0xFF070714), CircleShape)
                                .border(1.5.dp, Color(0xFF6366F1), CircleShape),
                            contentAlignment = Alignment.Center
                        ) {
                            Box(modifier = Modifier
                                .size(8.dp)
                                .background(Color(0xFF6366F1), CircleShape))
                        }
                    }
                }
                
                Text(
                    text = "Scanning for Server",
                    color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.Bold
                )
                Spacer(modifier = Modifier.height(8.dp))
                Text(
                    "Make sure the TetherLink server is running on\nyour Linux machine.",
                    color = Color.White.copy(alpha = 0.4f), fontSize = 13.sp, textAlign = TextAlign.Center
                )
                
                Spacer(modifier = Modifier.height(24.dp))
                
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color.Black.copy(alpha = 0.4f), RoundedCornerShape(12.dp))
                        .border(
                            1.dp,
                            Color(0xFF6366F1).copy(alpha = 0.2f),
                            RoundedCornerShape(12.dp)
                        )
                        .padding(14.dp)
                ) {
                    Text("▸ TETHERLINK LOG", color = Color(0xFF8B5CF6).copy(alpha = 0.7f), fontSize = 10.sp, letterSpacing = 0.06.sp)
                    Spacer(modifier = Modifier.height(6.dp))
                    
                    logMessages.forEachIndexed { index, msg ->
                        val color = if (index == logMessages.size - 1) Color(0xFFA7F3D0).copy(alpha = 0.8f) else Color.White.copy(alpha = 0.3f)
                        Row {
                            Text("$ ", color = Color(0xFF6366F1).copy(alpha = 0.6f), fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                            Text(msg, color = color, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                        }
                    }
                    val blink = rememberInfiniteTransition().animateFloat(
                        initialValue = 0f, targetValue = 1f,
                        animationSpec = infiniteRepeatable(animation = keyframes { durationMillis = 1000; 0f at 500 }, repeatMode = RepeatMode.Restart)
                    )
                    Text("▋", color = Color(0xFF6366F1).copy(alpha = 0.6f), fontSize = 11.sp, modifier = Modifier.alpha(if (blink.value > 0.5f) 1f else 0f))
                }
                
                Spacer(modifier = Modifier.height(16.dp))
            }
        }
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

@androidx.compose.ui.tooling.preview.Preview(showBackground = true, backgroundColor = 0xFF000000)
@Composable
fun PreviewScanningScreen() {
    ScanningScreen(onServerFound = {})
}
