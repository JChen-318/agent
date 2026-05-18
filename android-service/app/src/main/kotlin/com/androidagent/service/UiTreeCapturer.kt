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

    // Incremental parsing: cache last tree, skip re-serialization if unchanged
    @Volatile
    var contentChangedSinceLastCapture: Boolean = true

    private var cachedTree: UiNode? = null
    private var cachedTreeJson: String? = null

    fun capture(maxDepth: Int = MAX_DEPTH): UiNode {
        // Return cached tree if content hasn't changed (incremental optimization)
        if (!contentChangedSinceLastCapture && cachedTree != null) {
            Log.d(TAG, "Using cached UI tree (no content change)")
            return cachedTree!!
        }

        val root = service.rootInActiveWindow
        val tree = if (root != null) {
            val t = serializeNode(root, depth = 0, maxDepth = maxDepth)
            root.recycle()
            t
        } else {
            UiNode(type = "Root", text = "[No active window]")
        }

        cachedTree = tree
        contentChangedSinceLastCapture = false
        return tree
    }

    /** Lazy loading: expand a specific node at given coordinates to get its full subtree. */
    fun expandNode(x: Int, y: Int, maxDepth: Int = 10): UiNode? {
        val root = service.rootInActiveWindow ?: return null
        val node = findNodeAt(root, x, y, 0)
        return if (node != null) {
            val tree = serializeNode(node, depth = 0, maxDepth = maxDepth)
            node.recycle()
            root.recycle()
            tree
        } else {
            root.recycle()
            null
        }
    }

    fun findNodeByText(text: String, instance: Int = 0): Pair<Int, Int>? {
        val root = service.rootInActiveWindow ?: return null
        val node = findByText(root, text, instance)

        if (node != null) {
            val rect = Rect()
            node.getBoundsInScreen(rect)
            val cx = rect.centerX()
            val cy = rect.centerY()
            node.recycle()
            root.recycle()
            return Pair(cx, cy)
        }
        root.recycle()
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
        text: String,
        instance: Int = 0
    ): AccessibilityNodeInfo? {
        val skip = IntArray(1) { instance }
        return findByTextWithSkip(node, text, skip)
    }

    private fun findByTextWithSkip(
        node: AccessibilityNodeInfo,
        text: String,
        skip: IntArray
    ): AccessibilityNodeInfo? {
        if (node.isClickable || node.isCheckable) {
            val nodeText = node.text?.toString() ?: ""
            val nodeDesc = node.contentDescription?.toString() ?: ""
            if (text in nodeText || text in nodeDesc) {
                if (skip[0] <= 0) {
                    return node
                }
                skip[0] -= 1
            }
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findByTextWithSkip(child, text, skip)
            if (found != null) {
                if (child != found) child.recycle()
                return found
            }
            child.recycle()
        }
        return null
    }

    /** Find the deepest node at given coordinates (for expand_node). */
    private fun findNodeAt(
        node: AccessibilityNodeInfo,
        x: Int,
        y: Int,
        depth: Int
    ): AccessibilityNodeInfo? {
        if (depth > 15) return null
        val rect = android.graphics.Rect()
        node.getBoundsInScreen(rect)
        if (!rect.contains(x, y)) return null

        // Recurse into children first to find the deepest match
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findNodeAt(child, x, y, depth + 1)
            if (found != null) {
                if (child != found) child.recycle()
                return found
            }
            child.recycle()
        }
        // No deeper match — return this node (clone since caller will recycle)
        return if (node.isVisibleToUser) {
            AccessibilityNodeInfo.obtain(node)
        } else null
    }
}
