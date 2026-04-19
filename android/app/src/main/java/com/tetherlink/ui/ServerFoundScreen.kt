package com.tetherlink.ui

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tetherlink.R

@Composable
fun ServerFoundScreen(
    hostname: String,
    ip: String,
    system: String,
    onStartExtending: () -> Unit
) {
    val resolutions = listOf("1920×1080", "2560×1440", "3840×2160", "1280×720")
    val refreshRates = listOf("60 Hz", "120 Hz", "144 Hz")

    var selectedRes by remember { mutableIntStateOf(0) }
    var selectedHz by remember { mutableIntStateOf(0) }
    var positionRight by remember { mutableStateOf(true) }

    val backgroundBrush = Brush.verticalGradient(
        colors = listOf(Color(0xFF070714), Color(0xFF0B0B1F))
    )

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(backgroundBrush)
            .verticalScroll(rememberScrollState())
    ) {
        // Header
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Image(
                painter = painterResource(id = R.drawable.ic_launcher_foreground),
                contentDescription = "Logo",
                modifier = Modifier
                    .size(28.dp)
                    .clip(RoundedCornerShape(8.dp))
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text("TetherLink", color = Color.White, fontSize = 18.sp, fontWeight = FontWeight.Bold)
        }

        // Server Found Banner
        Box(
            modifier = Modifier
                .padding(16.dp)
                .fillMaxWidth()
                .background(Color(0xFF4ADE80).copy(0.12f), RoundedCornerShape(14.dp))
                .border(1.dp, Color(0xFF4ADE80).copy(0.25f), RoundedCornerShape(14.dp))
                .padding(14.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(40.dp)
                        .background(Color(0xFF4ADE80).copy(0.15f), RoundedCornerShape(10.dp)),
                    contentAlignment = Alignment.Center
                ) {
                    Icon(
                        Icons.Default.CheckCircle,
                        contentDescription = null,
                        tint = Color(0xFF4ADE80)
                    )
                }
                Spacer(modifier = Modifier.width(12.dp))
                Column {
                    Text(
                        "TetherLink Server Found!",
                        color = Color(0xFF4ADE80),
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        "Signal strength: Excellent",
                        color = Color.White.copy(0.4f),
                        fontSize = 11.sp
                    )
                }
            }
        }

        // Server Info Card
        InfoCard(hostname, ip, system)


        Spacer(modifier = Modifier.weight(1f))

        // Start Button
        Column(modifier = Modifier.padding(16.dp)) {
            Button(
                onClick = onStartExtending,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(56.dp),
                shape = RoundedCornerShape(16.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color.Transparent),
                contentPadding = PaddingValues()
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(
                            Brush.linearGradient(
                                listOf(
                                    Color(0xFF6366F1),
                                    Color(0xFF8B5CF6)
                                )
                            )
                        )
                        .padding(horizontal = 16.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            "Start Extending",
                            color = Color.White,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Icon(
                            Icons.Default.KeyboardArrowRight,
                            contentDescription = null,
                            tint = Color.White
                        )
                    }
                }
            }
            Text(
                text = "${resolutions[selectedRes]} · ${refreshRates[selectedHz]} · ${if (positionRight) "Right" else "Left"}",
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                textAlign = TextAlign.Center,
                color = Color.White.copy(0.25f),
                fontSize = 11.sp
            )
        }
    }
}

@Composable
fun InfoCard(hostname: String, ip: String, system: String) {
    Column(
        modifier = Modifier
            .padding(horizontal = 16.dp, vertical = 6.dp)
            .fillMaxWidth()
            .background(Color.White.copy(0.04f), RoundedCornerShape(16.dp))
            .border(1.dp, Color.White.copy(0.08f), RoundedCornerShape(16.dp))
            .padding(16.dp)
    ) {
        Text("SERVER INFO", color = Color.White.copy(0.35f), fontSize = 10.sp, letterSpacing = 1.sp)
        Spacer(modifier = Modifier.height(10.dp))
        InfoRow(Icons.Default.Build, "Hostname", hostname)
        HorizontalDivider(color = Color.White.copy(0.05f))
        InfoRow(Icons.Default.Info, "IP Address", ip)
        HorizontalDivider(color = Color.White.copy(0.05f))
        InfoRow(Icons.Default.Settings, "System", system)
    }
}

@Composable
fun InfoRow(icon: ImageVector, label: String, value: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, tint = Color(0xFF8B5CF6).copy(0.7f), modifier = Modifier.size(14.dp))
            Spacer(modifier = Modifier.width(10.dp))
            Text(label, color = Color.White.copy(0.4f), fontSize = 12.sp)
        }
        Text(
            value,
            color = Color.White.copy(0.75f),
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium
        )
    }
}

// Fixed FlowRow wrapper if you still want to keep it as a separate function:
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FlowRow(
    modifier: Modifier = Modifier,
    content: @Composable FlowRowScope.() -> Unit
) {
    androidx.compose.foundation.layout.FlowRow(modifier = modifier, content = content)
}

@androidx.compose.ui.tooling.preview.Preview
@Composable
fun PreviewScreen() {
    ServerFoundScreen("Prince","192.168.42.3","Linux",{ })
}