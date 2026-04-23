package com.tethrlink

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaCodec
import android.media.MediaFormat
import android.util.Log
import android.view.Surface
import java.util.concurrent.LinkedBlockingQueue

/**
 * TethrLink Stream Decoder (v0.9.0)
 * H.264: MediaCodec async mode with input queue
 * JPEG:  BitmapFactory → onBitmap callback
 */
class StreamDecoder(
    private val surface: Surface,
    val codec: Int,
    private val width: Int,
    private val height: Int,
    private val onBitmap: ((Bitmap) -> Unit)? = null
) {
    companion object {
        const val CODEC_H264 = 1
        const val CODEC_JPEG = 2
        private const val TAG = "StreamDecoder"
        private const val QUEUE_CAPACITY = 8
    }

    @Volatile private var released = false
    private var mediaCodec: MediaCodec? = null

    // Queue of NAL units waiting to be fed to the codec
    private val nalQueue = LinkedBlockingQueue<ByteArray>(QUEUE_CAPACITY)

    init {
        if (codec == CODEC_H264) setupH264Decoder()
    }

    private fun setupH264Decoder() {
        if (!surface.isValid) {
            Log.e(TAG, "Surface invalid")
            return
        }
        try {
            val format = MediaFormat.createVideoFormat(
                MediaFormat.MIMETYPE_VIDEO_AVC, width, height
            ).apply {
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 512 * 1024)
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            }

            val mc = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)

            mc.setCallback(object : MediaCodec.Callback() {

                override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {
                    if (released) return
                    val nalUnit = nalQueue.poll() ?: return
                    try {
                        val buf = codec.getInputBuffer(index) ?: return
                        buf.clear()
                        // AVCC format: MediaCodec accepts it directly
                        val data = if (nalUnit.size > buf.capacity()) {
                            nalUnit.copyOf(buf.capacity()) // truncate if needed
                        } else {
                            nalUnit
                        }
                        buf.put(data)
                        codec.queueInputBuffer(
                            index, 0, data.size,
                            System.nanoTime() / 1000, 0
                        )
                    } catch (e: Exception) {
                        Log.w(TAG, "Input: ${e.message}")
                    }
                }

                override fun onOutputBufferAvailable(
                    codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo
                ) {
                    if (released) return
                    try {
                        codec.releaseOutputBuffer(index, true) // render to Surface
                    } catch (e: Exception) {
                        Log.w(TAG, "Output: ${e.message}")
                    }
                }

                override fun onError(codec: MediaCodec, e: MediaCodec.CodecException) {
                    Log.e(TAG, "Codec error: ${e.diagnosticInfo} " +
                            "recoverable=${e.isRecoverable} transient=${e.isTransient}")
                    if (!e.isTransient) released = true
                }

                override fun onOutputFormatChanged(codec: MediaCodec, format: MediaFormat) {
                    Log.i(TAG, "Format changed: $format")
                }
            })

            mc.configure(format, surface, null, 0)
            mc.start()
            mediaCodec = mc
            Log.i(TAG, "H.264 async decoder started ${width}x${height}")

        } catch (e: Exception) {
            Log.e(TAG, "Setup failed: ${e.message}")
        }
    }

    fun decodeFrame(data: ByteArray) {
        if (released) return
        when (codec) {
            CODEC_H264 -> {
                // Offer to queue — drop if full (maintain low latency)
                nalQueue.offer(data)
            }
            CODEC_JPEG -> decodeJpeg(data)
        }
    }

    private fun decodeJpeg(data: ByteArray) {
        BitmapFactory.decodeByteArray(data, 0, data.size)?.let { onBitmap?.invoke(it) }
    }

    fun release() {
        released = true
        nalQueue.clear()
        try {
            mediaCodec?.stop()
            mediaCodec?.release()
        } catch (e: Exception) {
            Log.w(TAG, "Release: ${e.message}")
        }
        mediaCodec = null
    }
}