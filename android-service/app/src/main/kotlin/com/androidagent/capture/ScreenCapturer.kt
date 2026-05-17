package com.androidagent.capture

import android.graphics.Bitmap
import android.util.Base64
import com.androidagent.service.AgentAccessibilityService
import java.io.ByteArrayOutputStream

/**
 * Helper for capturing and encoding screenshots from the accessibility service.
 * For full MediaProjection-based capture (e.g., video recording), extend here.
 */
object ScreenCapturer {

    fun captureAsBitmap(): Bitmap? {
        val service = AgentAccessibilityService.instance ?: return null
        return try {
            service.takeScreenshot()
        } catch (e: Exception) {
            null
        }
    }

    fun captureAsBase64(quality: Int = 70): String? {
        val bitmap = captureAsBitmap() ?: return null
        val stream = ByteArrayOutputStream()
        bitmap.compress(Bitmap.CompressFormat.JPEG, quality, stream)
        val result = Base64.encodeToString(stream.toByteArray(), Base64.NO_WRAP)
        bitmap.recycle()
        return result
    }
}
