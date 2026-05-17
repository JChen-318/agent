package com.androidagent.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.ClipData
import android.content.ClipboardManager
import android.graphics.Path
import android.graphics.Point
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import com.androidagent.App
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class GestureExecutor(private val service: AccessibilityService) {
    companion object {
        private const val TAG = "${App.TAG}:Gesture"
    }

    fun click(x: Int, y: Int): Boolean {
        return dispatchGesture(x, y, x, y, 100)
    }

    fun longPress(x: Int, y: Int, durationMs: Int = 1000): Boolean {
        return dispatchGesture(x, y, x, y, durationMs)
    }

    fun swipe(x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int = 300): Boolean {
        return dispatchGesture(x1, y1, x2, y2, durationMs)
    }

    fun scrollForward(): Boolean {
        val displayMetrics = service.resources.displayMetrics
        val w = displayMetrics.widthPixels
        val h = displayMetrics.heightPixels
        val startX = w / 2
        val startY = (h * 0.7).toInt()
        val endY = (h * 0.3).toInt()
        return dispatchGesture(startX, startY, startX, endY, 200)
    }

    fun scrollBackward(): Boolean {
        val displayMetrics = service.resources.displayMetrics
        val w = displayMetrics.widthPixels
        val h = displayMetrics.heightPixels
        val startX = w / 2
        val startY = (h * 0.3).toInt()
        val endY = (h * 0.7).toInt()
        return dispatchGesture(startX, startY, startX, endY, 200)
    }

    fun typeText(text: String, clearFirst: Boolean = true): Boolean {
        val root = service.rootInActiveWindow ?: return false

        // Find focused editable node
        val focused = findFocusedEditable(root)
        root.recycle()

        if (focused != null) {
            if (clearFirst) {
                val args = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, "")
                }
                focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
            }

            // Paste text via clipboard
            val clipboard = service.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as ClipboardManager
            val clip = ClipData.newPlainText("agent_input", text)
            clipboard.setPrimaryClip(clip)
            focused.performAction(AccessibilityNodeInfo.ACTION_PASTE)
            focused.recycle()
            return true
        }

        return false
    }

    fun performGlobalAction(action: Int): Boolean {
        return service.performGlobalAction(action)
    }

    private fun dispatchGesture(
        x1: Int, y1: Int, x2: Int, y2: Int, durationMs: Int
    ): Boolean {
        val path = Path().apply {
            moveTo(x1.toFloat(), y1.toFloat())
            if (x1 != x2 || y1 != y2) {
                lineTo(x2.toFloat(), y2.toFloat())
            }
        }

        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs.toLong()))
            .build()

        val latch = CountDownLatch(1)
        var success = false

        val dispatched = service.dispatchGesture(gesture, null, null) {
            success = it
            latch.countDown()
        }

        if (dispatched) {
            latch.await(5, TimeUnit.SECONDS)
        }

        return dispatched && success
    }

    private fun findFocusedEditable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isFocused && node.isEditable) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findFocusedEditable(child)
            if (found != null) return found
            child.recycle()
        }
        return null
    }
}
