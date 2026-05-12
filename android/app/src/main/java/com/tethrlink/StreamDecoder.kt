package com.tethrlink

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaCodec
import android.media.MediaFormat
import android.util.Log
import android.view.Surface
import java.util.concurrent.LinkedBlockingQueue

/**
 * TethrLink Stream Decoder (v0.9.1)
 * H.264: MediaCodec async mode with unified drain logic
 * JPEG:  BitmapFactory → onBitmap callback
 */

@Volatile private var waitingForSps = true

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
        // Increased capacity to handle network bursts
        private const val QUEUE_CAPACITY = 60
    }

    @Volatile private var released = false
    private var mediaCodec: MediaCodec? = null

    // We only queue NALs. Codec indices are managed in a separate queue.
    private val nalQueue = LinkedBlockingQueue<ByteArray>(QUEUE_CAPACITY)
    private val availableInputBufferIndices = LinkedBlockingQueue<Int>()

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
                // Generous input size to prevent overflow/truncation
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 1024 * 1024)
                setInteger(MediaFormat.KEY_LOW_LATENCY, 1)
            }

            val mc = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)

            mc.setCallback(object : MediaCodec.Callback() {

                override fun onInputBufferAvailable(codec: MediaCodec, index: Int) {
                    if (released) return
                    availableInputBufferIndices.offer(index)
                    drainQueue()
                }

                override fun onOutputBufferAvailable(
                    codec: MediaCodec, index: Int, info: MediaCodec.BufferInfo
                ) {
                    if (released) return
                    try {
                        codec.releaseOutputBuffer(index, true) // render to Surface
                    } catch (e: Exception) {
                        Log.w(TAG, "Output Error: ${e.message}")
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


    private fun getNalType(data: ByteArray): Int {
        if (data.size >= 4) {
            if (data[0] == 0.toByte() && data[1] == 0.toByte() && data[2] == 1.toByte()) {
                return data[3].toInt() and 0x1F
            } else if (data.size >= 5 && data[0] == 0.toByte() && data[1] == 0.toByte() && data[2] == 0.toByte() && data[3] == 1.toByte()) {
                return data[4].toInt() and 0x1F
            }
        }
        return -1
    }

    private fun splitNalUnits(data: ByteArray): List<ByteArray> {
        val nals = mutableListOf<ByteArray>()
        val indices = mutableListOf<Int>()
        var i = 0
        while (i < data.size - 2) {
            if (data[i] == 0.toByte() && data[i+1] == 0.toByte()) {
                if (data[i+2] == 1.toByte()) {
                    indices.add(i)
                    i += 3
                    continue
                } else if (i < data.size - 3 && data[i+2] == 0.toByte() && data[i+3] == 1.toByte()) {
                    indices.add(i)
                    i += 4
                    continue
                }
            }
            i++
        }
        if (indices.isEmpty()) return listOf(data)
        for (j in 0 until indices.size) {
            val startIdx = indices[j]
            val endIdx = if (j + 1 < indices.size) indices[j + 1] else data.size
            val len = endIdx - startIdx
            if (len > 0) nals.add(data.copyOfRange(startIdx, endIdx))
        }
        return nals
    }

    fun decodeFrame(data: ByteArray) {
        if (released) return
        when (codec) {
            CODEC_H264 -> {
                try {
                    val nals = splitNalUnits(data)
                    var configSize = 0
                    var videoSize = 0
                    val configList = mutableListOf<ByteArray>()
                    val videoList = mutableListOf<ByteArray>()

                    // Separate the Config NALs from the Video Slice NALs
                    for (nal in nals) {
                        val nalType = getNalType(nal)
                        if (nalType == 7 || nalType == 8) {
                            configList.add(nal)
                            configSize += nal.size
                        } else {
                            videoList.add(nal)
                            videoSize += nal.size
                        }
                    }

                    // 1. Queue pure Config Data
                    if (configList.isNotEmpty()) {
                        val configData = ByteArray(configSize)
                        var offset = 0
                        for (nal in configList) {
                            System.arraycopy(nal, 0, configData, offset, nal.size)
                            offset += nal.size
                        }
                        nalQueue.put(configData)
                    }

                    // 2. Queue pure Video Data (ALL slices perfectly glued together!)
                    if (videoList.isNotEmpty()) {
                        val videoData = ByteArray(videoSize)
                        var offset = 0
                        for (nal in videoList) {
                            System.arraycopy(nal, 0, videoData, offset, nal.size)
                            offset += nal.size
                        }
                        nalQueue.put(videoData)
                    }

                    drainQueue()
                } catch (e: InterruptedException) {
                    Thread.currentThread().interrupt()
                }
            }
            CODEC_JPEG -> decodeJpeg(data)
        }
    }

    @Synchronized
    private fun drainQueue() {
        val mc = mediaCodec ?: return
        while (true) {
            val index = availableInputBufferIndices.poll() ?: break
            val data = nalQueue.poll()
            if (data == null) {
                availableInputBufferIndices.offer(index)
                break
            }
            feedInputBuffer(mc, index, data)
        }
    }

    private fun feedInputBuffer(codec: MediaCodec, index: Int, data: ByteArray) {
        try {
            val buf = codec.getInputBuffer(index) ?: return
            if (data.size > buf.capacity()) {
                codec.queueInputBuffer(index, 0, 0, 0, 0)
                return
            }

            buf.clear()
            buf.put(data)

            var flags = 0
            val nalType = getNalType(data)

            // Check if this combined buffer is our pure config buffer
            val isConfig = (nalType == 7 || nalType == 8)

            if (isConfig) {
                flags = MediaCodec.BUFFER_FLAG_CODEC_CONFIG
                waitingForSps = false
                Log.d(TAG, "Queued pure CODEC_CONFIG buffer.")
            } else if (waitingForSps) {
                // Drop slices until initialization
                codec.queueInputBuffer(index, 0, 0, 0, 0)
                return
            }

            // Feed the buffer (Flag 2 for config, Flag 0 for the glued full picture!)
            codec.queueInputBuffer(index, 0, data.size, System.nanoTime() / 1000, flags)

        } catch (e: Exception) {
            Log.w(TAG, "Input Error: ${e.message}")
        }
    }
    private fun decodeJpeg(data: ByteArray) {
        BitmapFactory.decodeByteArray(data, 0, data.size)?.let { onBitmap?.invoke(it) }
    }

    fun release() {
        released = true
        nalQueue.clear()
        availableInputBufferIndices.clear()
        try {
            mediaCodec?.stop()
            mediaCodec?.release()
        } catch (e: Exception) {
            Log.w(TAG, "Release Error: ${e.message}")
        }
        mediaCodec = null
    }
}