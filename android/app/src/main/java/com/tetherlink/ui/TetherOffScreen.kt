package com.tetherlink.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tetherlink.R

@Composable
fun TetherOffScreen(onEnableTether: () -> Unit) {
    val backgroundBrush = Brush.verticalGradient(
        colors = listOf(Color(0xFF070714), Color(0xFF0B0B1F))
    )

    Box(modifier = Modifier.fillMaxSize().background(backgroundBrush)) {
        Column(modifier = Modifier.fillMaxSize()) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                androidx.compose.foundation.Image(
                    painter = painterResource(id = R.drawable.ic_launcher_foreground),
                    contentDescription = "Logo",
                    modifier = Modifier.size(28.dp).clip(RoundedCornerShape(8.dp))
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
                Box(modifier = Modifier.size(8.dp).background(Color(0xFF4ADE80), CircleShape))
                Spacer(modifier = Modifier.width(8.dp))
                Text("USB-C Connected", color = Color.White.copy(alpha = 0.6f), fontSize = 12.sp)
                Spacer(modifier = Modifier.weight(1f))
                Box(
                    modifier = Modifier
                        .background(Color(0xFFEF4444).copy(alpha = 0.15f), RoundedCornerShape(6.dp))
                        .border(1.dp, Color(0xFFEF4444).copy(alpha = 0.3f), RoundedCornerShape(6.dp))
                        .padding(horizontal = 8.dp, vertical = 2.dp)
                ) {
                    Text("Tethering OFF", color = Color(0xFFF87171), fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                }
            }

            Column(
                modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 24.dp).padding(bottom = 16.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center
            ) {
                Box(
                    modifier = Modifier
                        .size(88.dp)
                        .background(Color(0xFFFBBF24).copy(alpha = 0.1f), CircleShape)
                        .border(1.5.dp, Color(0xFFFBBF24).copy(alpha = 0.25f), CircleShape)
                        .padding(bottom = 5.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text("⚠️", fontSize = 30.sp)
                }

                Spacer(modifier = Modifier.height(20.dp))
                Text("USB Tethering Disabled", color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Spacer(modifier = Modifier.height(8.dp))
                Text("TetherLink needs USB Tethering to establish a\nnetwork connection with your Linux machine.",
                    color = Color.White.copy(alpha = 0.45f), fontSize = 13.sp, textAlign = TextAlign.Center, lineHeight = 20.sp)

                Spacer(modifier = Modifier.height(28.dp))

                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Brush.linearGradient(listOf(Color(0xFF6366F1), Color(0xFF8B5CF6))), RoundedCornerShape(14.dp))
                        .clickable { onEnableTether() }
                        .padding(16.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text("Open USB Tethering Settings", color = Color.White, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                }

                Spacer(modifier = Modifier.height(10.dp))
                Text("Settings → Network → Hotspot & Tethering → USB Tethering", color = Color.White.copy(alpha = 0.3f), fontSize = 11.sp)

                Spacer(modifier = Modifier.height(28.dp))

                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(Color.White.copy(alpha = 0.04f), RoundedCornerShape(16.dp))
                        .border(1.dp, Color.White.copy(alpha = 0.08f), RoundedCornerShape(16.dp))
                        .padding(16.dp)
                ) {
                    Text("SETUP CHECKLIST", color = Color.White.copy(alpha = 0.35f), fontSize = 11.sp, letterSpacing = 0.05.sp)
                    Spacer(modifier = Modifier.height(10.dp))
                    ChecklistItem("USB-C cable connected", true, true)
                    ChecklistItem("USB Tethering enabled", false, false)
                }
            }
        }
    }
}

@Composable
fun ChecklistItem(label: String, done: Boolean, showBorder: Boolean) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (done) {
            Box(modifier = Modifier.size(16.dp).border(1.5.dp, Color.Green.copy(alpha = 0.8f), CircleShape).background(Color.Green.copy(alpha = 0.5f), CircleShape))
        } else {
            Box(modifier = Modifier.size(16.dp).border(1.5.dp, Color.Red.copy(alpha = 0.8f), CircleShape).background(Color.Red.copy(alpha = 0.5f), CircleShape))
        }
        Spacer(modifier = Modifier.width(12.dp))
        Text(label, color = if (done) Color.White.copy(alpha = 0.7f) else Color.White.copy(alpha = 0.35f), fontSize = 13.sp)
    }
    if (showBorder) {
        Box(modifier = Modifier.fillMaxWidth().height(1.dp).background(Color.White.copy(alpha = 0.05f)))
    }
}

@androidx.compose.ui.tooling.preview.Preview(showBackground = true, backgroundColor = 0xFF000000)
@Composable
fun PreviewTetherOffScreen() {
    TetherOffScreen(onEnableTether = {})
}
