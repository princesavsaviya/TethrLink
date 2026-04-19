package com.tetherlink.ui

import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tetherlink.R
import kotlinx.coroutines.delay

@Composable
fun NoUSBScreen() {
    var pulseState by remember { mutableStateOf(0) }
    LaunchedEffect(Unit) {
        while (true) {
            pulseState = (pulseState + 1) % 4
            delay(700)
        }
    }
    
    val backgroundBrush = Brush.verticalGradient(
        colors = listOf(Color(0xFF070714), Color(0xFF0B0B1F))
    )
    
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(backgroundBrush)
    ) {
        Column(
            modifier = Modifier.fillMaxSize()
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 20.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    androidx.compose.foundation.Image(
                        painter = painterResource(id = R.drawable.ic_launcher_foreground),
                        contentDescription = "Logo",
                        modifier = Modifier
                            .size(28.dp)
                            .clip(RoundedCornerShape(8.dp))
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = "TetherLink",
                        color = Color.White,
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = (-0.01).sp
                    )
                }
                Box(
                    modifier = Modifier
                        .background(
                            Color(0xFFFFFFFF).copy(alpha = 0.07f),
                            RoundedCornerShape(20.dp)
                        )
                        .padding(horizontal = 10.dp, vertical = 4.dp)
                ) {
                    Text("v1.0", color = Color.White.copy(alpha = 0.4f), fontSize = 11.sp)
                }
            }
            
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = 24.dp)
                    .padding(bottom = 32.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Box(
                    modifier = Modifier
                        .size(400.dp)
                        .padding(bottom = 40.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Box(modifier = Modifier
                        .size(225.dp)
                        .border(
                            1.5.dp,
                            Color(0xFF6366F1).copy(alpha = if (pulseState == 0) 0.4f else 0.1f),
                            CircleShape
                        ))
                    Box(modifier = Modifier
                        .size(175.dp)
                        .border(
                            1.5.dp,
                            Color(0xFF6366F1).copy(alpha = if (pulseState == 1) 0.5f else 0.15f),
                            CircleShape
                        ))
                    Box(modifier = Modifier
                        .size(125.dp)
                        .border(
                            1.5.dp,
                            Color(0xFF6366F1).copy(alpha = if (pulseState == 2) 0.6f else 0.2f),
                            CircleShape
                        ))
                    
                    Box(modifier = Modifier
                        .size(75.dp)
                        .background(
                            Brush.linearGradient(
                                colors = listOf(
                                    Color(0xFF6366F1).copy(if (pulseState == 3) 0.3f else 0.15f),
                                    Color(0xFF8B5CF6).copy(if (pulseState == 3) 0.3f else 0.15f)
                                )
                            ), CircleShape
                        )
                        .border(1.5.dp, Color(0xFF6366F1).copy(alpha = 0.35f), CircleShape),
                        contentAlignment = Alignment.Center
                    ) {
                        Canvas(modifier = Modifier.size(34.dp)) {
                            val path = Path().apply {
                                moveTo(size.width * 0.6f, 0f)
                                lineTo(size.width * 0.2f, size.height * 0.55f)
                                lineTo(size.width * 0.45f, size.height * 0.55f)
                                lineTo(size.width * 0.35f, size.height)
                                lineTo(size.width * 0.8f, size.height * 0.4f)
                                lineTo(size.width * 0.55f, size.height * 0.4f)
                                close()
                            }
                            drawPath(
                                path = path,
                                color = Color(0xFF8B5CF6),
                                style = Stroke(width = 2.dp.toPx(), join = StrokeJoin.Round)
                            )
                        }
                    }
                }
                
                Spacer(modifier = Modifier.height(40.dp))
                Row(
                    modifier = Modifier
                        .background(
                            Color(0xFFEF4444).copy(alpha = 0.12f),
                            RoundedCornerShape(20.dp)
                        )
                        .border(
                            1.dp,
                            Color(0xFFEF4444).copy(alpha = 0.25f),
                            RoundedCornerShape(20.dp)
                        )
                        .padding(horizontal = 14.dp, vertical = 5.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(modifier = Modifier
                        .size(7.dp)
                        .background(Color(0xFFEF4444), CircleShape))
                    Spacer(modifier = Modifier.width(6.dp))
                    Text("No USB Connection", color = Color(0xFFF87171), fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                }
                
                Spacer(modifier = Modifier.height(16.dp))
                
                Text(
                    text = "Waiting for USB-C",
                    color = Color.White,
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = (-0.02).sp
                )
                
                Spacer(modifier = Modifier.height(10.dp))
                
                Text(
                    text = "Connect your USB-C cable between your\nAndroid device and Linux machine to get\nstarted.",
                    color = Color.White.copy(alpha = 0.45f),
                    fontSize = 14.sp,
                    lineHeight = 22.sp,
                    textAlign = TextAlign.Center
                )
                
                Spacer(modifier = Modifier.height(36.dp))
                
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color.White.copy(alpha = 0.04f), RoundedCornerShape(16.dp))
                        .border(1.dp, Color.White.copy(alpha = 0.08f), RoundedCornerShape(16.dp))
                        .padding(16.dp)
                ) {
                    StepItem("1", "Connect USB-C cable", showBorder = true)
                    StepItem("2", "Enable USB Tethering", showBorder = true)
                    StepItem("3", "Run TetherLink on Linux", showBorder = false)
                }
            }
        }
    }
}

@Composable
fun StepItem(step: String, label: String, showBorder: Boolean) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(26.dp)
                .background(Color(0xFF6366F1).copy(alpha = 0.15f), CircleShape)
                .border(1.5.dp, Color(0xFF6366F1).copy(alpha = 0.3f), CircleShape),
            contentAlignment = Alignment.Center
        ) {
            Text(step, color = Color(0xFF8B5CF6).copy(alpha = 0.8f), fontSize = 12.sp, fontWeight = FontWeight.Bold)
        }
        Spacer(modifier = Modifier.width(12.dp))
        Text(label, color = Color.White.copy(alpha = 0.5f), fontSize = 13.sp)
    }
    if (showBorder) {
        Box(modifier = Modifier
            .fillMaxWidth()
            .height(1.dp)
            .background(Color.White.copy(alpha = 0.05f)))
    }
}

@androidx.compose.ui.tooling.preview.Preview
@Composable
fun PreviewNoUSBScreen() {
    NoUSBScreen()
}
