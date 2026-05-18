package com.androidagent.server

import android.util.Log
import com.androidagent.App
import com.androidagent.model.Command
import com.androidagent.model.CommandResponse
import com.androidagent.service.AgentAccessibilityService
import com.androidagent.model.UiNode
import com.google.gson.Gson

class CommandHandler(private val service: AgentAccessibilityService) {
    companion object {
        private const val TAG = "${App.TAG}:Handler"
    }

    private val gson = Gson()

    fun handle(cmd: Command): CommandResponse {
        return when (cmd.type) {
            "click" -> handleClick(cmd)
            "click_by_text" -> handleClickByText(cmd)
            "long_press" -> handleLongPress(cmd)
            "swipe" -> handleSwipe(cmd)
            "scroll" -> handleScroll(cmd)
            "type" -> handleType(cmd)
            "back" -> handleBack()
            "home" -> handleHome()
            "recent_apps" -> handleRecentApps()
            "launch_app" -> handleLaunchApp(cmd)
            "get_ui_tree" -> handleGetUiTree(cmd)
            "screenshot" -> handleScreenshot()
            "wait" -> handleWait(cmd)
            "ping" -> CommandResponse.ok(cmd.id, message = "pong")
            else -> CommandResponse.error(cmd.id, "Unknown command: ${cmd.type}")
        }
    }

    private fun handleClick(cmd: Command): CommandResponse {
        val x = (cmd.args["x"] as? Number)?.toInt() ?: return badArg(cmd, "x")
        val y = (cmd.args["y"] as? Number)?.toInt() ?: return badArg(cmd, "y")
        val ok = service.gestureExecutor.click(x, y)
        return if (ok) CommandResponse.ok(cmd.id, mapOf("x" to x, "y" to y))
        else CommandResponse.error(cmd.id, "Click failed at ($x, $y)")
    }

    private fun handleClickByText(cmd: Command): CommandResponse {
        val text = cmd.args["text"] as? String ?: return badArg(cmd, "text")
        val instance = (cmd.args["instance"] as? Number)?.toInt() ?: 0
        val point = service.uiTreeCapturer.findNodeByText(text, instance)
        return if (point != null) {
            service.gestureExecutor.click(point.first, point.second)
            CommandResponse.ok(cmd.id, mapOf("text" to text, "instance" to instance, "x" to point.first, "y" to point.second))
        } else {
            CommandResponse.error(cmd.id, "No element found at instance $instance with text: $text")
        }
    }

    private fun handleLongPress(cmd: Command): CommandResponse {
        val x = (cmd.args["x"] as? Number)?.toInt() ?: return badArg(cmd, "x")
        val y = (cmd.args["y"] as? Number)?.toInt() ?: return badArg(cmd, "y")
        val dur = (cmd.args["duration_ms"] as? Number)?.toInt() ?: 1000
        val ok = service.gestureExecutor.longPress(x, y, dur)
        return if (ok) CommandResponse.ok(cmd.id)
        else CommandResponse.error(cmd.id, "Long press failed")
    }

    private fun handleSwipe(cmd: Command): CommandResponse {
        val x1 = (cmd.args["x1"] as? Number)?.toInt() ?: return badArg(cmd, "x1")
        val y1 = (cmd.args["y1"] as? Number)?.toInt() ?: return badArg(cmd, "y1")
        val x2 = (cmd.args["x2"] as? Number)?.toInt() ?: return badArg(cmd, "x2")
        val y2 = (cmd.args["y2"] as? Number)?.toInt() ?: return badArg(cmd, "y2")
        val dur = (cmd.args["duration_ms"] as? Number)?.toInt() ?: 300
        val ok = service.gestureExecutor.swipe(x1, y1, x2, y2, dur)
        return if (ok) CommandResponse.ok(cmd.id)
        else CommandResponse.error(cmd.id, "Swipe failed")
    }

    private fun handleScroll(cmd: Command): CommandResponse {
        val direction = cmd.args["direction"] as? String ?: "forward"
        val steps = (cmd.args["steps"] as? Number)?.toInt() ?: 1

        repeat(steps) {
            when (direction) {
                "forward" -> service.gestureExecutor.scrollForward()
                "backward" -> service.gestureExecutor.scrollBackward()
                else -> {}
            }
            if (steps > 1) {
                Thread.sleep(200)  // Allow UI to settle between scroll steps
            }
        }
        return CommandResponse.ok(cmd.id, mapOf("direction" to direction, "steps" to steps))
    }

    private fun handleType(cmd: Command): CommandResponse {
        val text = cmd.args["text"] as? String ?: return badArg(cmd, "text")
        val clearFirst = cmd.args["clear_first"] as? Boolean ?: true
        val ok = service.gestureExecutor.typeText(text, clearFirst)
        return if (ok) CommandResponse.ok(cmd.id, mapOf("text" to text))
        else CommandResponse.error(cmd.id, "Type failed: no focused editable field")
    }

    private fun handleBack(): CommandResponse {
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            service.gestureExecutor.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK)
        }, 50)
        return CommandResponse.ok("back", message = "back")
    }

    private fun handleHome(): CommandResponse {
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            service.gestureExecutor.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME)
        }, 50)
        return CommandResponse.ok("home", message = "home")
    }

    private fun handleRecentApps(): CommandResponse {
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            service.gestureExecutor.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_RECENTS)
        }, 50)
        return CommandResponse.ok("recent", message = "recent_apps")
    }

    private fun handleLaunchApp(cmd: Command): CommandResponse {
        val pkg = cmd.args["package_name"] as? String ?: return badArg(cmd, "package_name")
        return try {
            val intent = service.packageManager.getLaunchIntentForPackage(pkg)
            if (intent != null) {
                intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                service.startActivity(intent)
                CommandResponse.ok(cmd.id, mapOf("package_name" to pkg))
            } else {
                CommandResponse.error(cmd.id, "App not found: $pkg")
            }
        } catch (e: Exception) {
            CommandResponse.error(cmd.id, "Launch failed: ${e.message}")
        }
    }

    private fun handleGetUiTree(cmd: Command): CommandResponse {
        val maxDepth = (cmd.args["max_depth"] as? Number)?.toInt() ?: 20
        val tree = service.uiTreeCapturer.capture(maxDepth)
        val treeJson = gson.toJsonTree(tree).asJsonObject
        val data = gson.fromJson(treeJson, Map::class.java) as Map<String, Any?>
        return CommandResponse.ok(cmd.id, mapOf("ui_tree" to data))
    }

    private fun handleScreenshot(): CommandResponse {
        return try {
            val bitmap = service.captureScreenshot()
                ?: return CommandResponse.error("screenshot", "Screenshot requires Android 14+")
            val stream = java.io.ByteArrayOutputStream()
            bitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG, 70, stream)
            val base64 = android.util.Base64.encodeToString(stream.toByteArray(), android.util.Base64.NO_WRAP)
            bitmap.recycle()
            CommandResponse.ok("screenshot", mapOf("image_base64" to base64))
        } catch (e: Exception) {
            CommandResponse.error("screenshot", "Screenshot failed: ${e.message}")
        }
    }

    private fun handleWait(cmd: Command): CommandResponse {
        val dur = (cmd.args["duration_ms"] as? Number)?.toLong() ?: 1000L
        Thread.sleep(dur.coerceAtMost(30000L))
        return CommandResponse.ok(cmd.id, mapOf("duration_ms" to dur))
    }

    private fun badArg(cmd: Command, name: String) =
        CommandResponse.error(cmd.id, "Missing required argument: $name")
}
