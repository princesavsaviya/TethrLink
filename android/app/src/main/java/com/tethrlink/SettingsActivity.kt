package com.tethrlink

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Button
import android.widget.ImageButton
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.google.android.material.slider.Slider

/**
 * TethrLink – Settings Activity (v0.9.4)
 *
 * Stores user preferences for FPS, JPEG quality, and codec.
 * These are saved locally and will be synced to the server in a future version.
 */
class SettingsActivity : AppCompatActivity() {

    companion object {
        const val PREFS_NAME      = "tethrlink_settings"
        const val KEY_FPS         = "pref_fps"
        const val KEY_QUALITY     = "pref_quality"
        const val KEY_CODEC       = "pref_codec"
        const val DEFAULT_FPS     = 60
        const val DEFAULT_QUALITY = 85
        const val DEFAULT_CODEC   = "jpeg"
        const val APP_VERSION     = "v0.9.4"
        const val GITHUB_URL      = "https://github.com/princesavsaviya/TethrLink"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        enableImmersiveMode()

        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        val sliderFps     = findViewById<Slider>(R.id.sliderFps)
        val tvFpsValue    = findViewById<TextView>(R.id.tvFpsValue)
        val sliderQuality = findViewById<Slider>(R.id.sliderQuality)
        val tvQualityValue = findViewById<TextView>(R.id.tvQualityValue)
        val btnCodecJpeg  = findViewById<Button>(R.id.btnCodecJpeg)
        val btnCodecH264  = findViewById<Button>(R.id.btnCodecH264)
        val tvVersion     = findViewById<TextView>(R.id.tvVersion)

        // ── Load saved values ─────────────────────────────────────────────────
        val savedFps     = prefs.getInt(KEY_FPS, DEFAULT_FPS)
        val savedQuality = prefs.getInt(KEY_QUALITY, DEFAULT_QUALITY)
        val savedCodec   = prefs.getString(KEY_CODEC, DEFAULT_CODEC) ?: DEFAULT_CODEC

        sliderFps.value    = savedFps.toFloat()
        tvFpsValue.text    = savedFps.toString()
        sliderQuality.value = savedQuality.toFloat()
        tvQualityValue.text = savedQuality.toString()
        tvVersion.text     = APP_VERSION

        updateCodecButtons(btnCodecJpeg, btnCodecH264, savedCodec)

        // ── Listeners ─────────────────────────────────────────────────────────
        sliderFps.addOnChangeListener { _, value, _ ->
            val fps = value.toInt()
            tvFpsValue.text = fps.toString()
            prefs.edit().putInt(KEY_FPS, fps).apply()
        }

        sliderQuality.addOnChangeListener { _, value, _ ->
            val quality = value.toInt()
            tvQualityValue.text = quality.toString()
            prefs.edit().putInt(KEY_QUALITY, quality).apply()
        }

        btnCodecJpeg.setOnClickListener {
            prefs.edit().putString(KEY_CODEC, "jpeg").apply()
            updateCodecButtons(btnCodecJpeg, btnCodecH264, "jpeg")
        }

        btnCodecH264.setOnClickListener {
            prefs.edit().putString(KEY_CODEC, "h264").apply()
            updateCodecButtons(btnCodecJpeg, btnCodecH264, "h264")
        }

        findViewById<Button>(R.id.btnGithub).setOnClickListener {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(GITHUB_URL)))
        }

        findViewById<ImageButton>(R.id.btnBack).setOnClickListener {
            finish()
        }
    }

    private fun updateCodecButtons(btnJpeg: Button, btnH264: Button, selected: String) {
        btnJpeg.setBackgroundResource(
            if (selected == "jpeg") R.drawable.bg_codec_selected
            else R.drawable.bg_codec_unselected
        )
        btnH264.setBackgroundResource(
            if (selected == "h264") R.drawable.bg_codec_selected
            else R.drawable.bg_codec_unselected
        )
        btnJpeg.setTextColor(
            resources.getColor(
                if (selected == "jpeg") R.color.brand_light else R.color.text_hint, theme
            )
        )
        btnH264.setTextColor(
            resources.getColor(
                if (selected == "h264") R.color.brand_light else R.color.text_hint, theme
            )
        )
    }

    private fun enableImmersiveMode() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }
}
