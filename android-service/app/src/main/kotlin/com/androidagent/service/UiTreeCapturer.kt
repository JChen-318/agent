package com.androidagent.service

import android.accessibilityservice.AccessibilityService
import android.graphics.Rect
import android.util.Log
import android.view.accessibility.AccessibilityNodeInfo
import com.androidagent.App
import com.androidagent.model.UiNode

class UiTreeCapturer(private val service: AccessibilityService) {
    companion object {
        private const val TAG = "${App.TAG}:UI"
        private const val MAX_DEPTH = 20
        private const val MAX_TEXT_LEN = 128
    }

    fun capture(maxDepth: Int = MAX_DEPTH): UiNode {
        val root = service.rootInActiveWindow
        return if (root != null) {
            val tree = serializeNode(root, depth = 0, maxDepth = maxDepth)
            root.recycle()
            tree
        } else {
            UiNode(type = "Root", text = "[No active window]")
        }
    }

    fun findNodeByText(text: String): Pair<Int, Int>? {
        val root = service.rootInActiveWindow ?: return null
        val node = findByText(root, text)
        root.recycle()

        if (node != null) {
            val rect = Rect()
            node.getBoundsInScreen(rect)
            val cx = rect.centerX()
            val cy = rect.centerY()
            node.recycle()
            return Pair(cx, cy)
        }
        return null
    }

    private fun serializeNode(
        node: AccessibilityNodeInfo,
        depth: Int,
        maxDepth: Int
    ): UiNode {
        val children = mutableListOf<UiNode>()

        if (depth < maxDepth) {
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                if (child.isVisibleToUser) {
                    children.add(serializeNode(child, depth + 1, maxDepth))
                }
                child.recycle()
            }
        }

        val rect = Rect()
        if (node.isVisibleToUser) {
            node.getBoundsInScreen(rect)
        }

        val bounds = mapOf(
            "left" to rect.left,
            "top" to rect.top,
            "right" to rect.right,
            "bottom" to rect.bottom
        )

        return UiNode(
            type = node.className?.toString()?.substringAfterLast('.') ?: "Unknown",
            text = (node.text?.toString() ?: "").take(MAX_TEXT_LEN),
            resourceId = node.viewIdResourceName ?: "",
            contentDesc = (node.contentDescription?.toString() ?: "").take(MAX_TEXT_LEN),
            isClickable = node.isClickable,
            isScrollable = node.isScrollable,
            isEditable = node.isEditable,
            isFocused = node.isFocused,
            isEnabled = node.isEnabled,
            bounds = bounds,
            centerX = rect.centerX(),
            centerY = rect.centerY(),
            children = children
        )
    }

    private fun findByText(
        node: AccessibilityNodeInfo,
        text: String
    ): AccessibilityNodeInfo? {
        if (node.isClickable || node.isCheckable) {
            val nodeText = node.text?.toString() ?: ""
            val nodeDesc = node.contentDescription?.toString() ?: ""
            if (text in nodeText || text in nodeDesc) {
                return node
            }
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findByText(child, text)
            if (found != null) {
                if (child != found) child.recycle()
                return found
            }
            child.recycle()
        }
        return null
    }
}
