package com.androidagent.service

import android.accessibilityservice.AccessibilityService
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.util.DisplayMetrics
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import com.androidagent.App
import com.androidagent.R
import com.androidagent.server.AgentWebSocketServer
import com.androidagent.server.CommandHandler

class AgentAccessibilityService : AccessibilityService() {
    companion object {
        private const val TAG = "${App.TAG}:Service"
        const val PORT = 8765
        const val NOTIFICATION_CHANNEL_ID = "android_agent_service"
        const val NOTIFICATION_ID = 1001

        @Volatile
        var instance: AgentAccessibilityService? = null
            private set
    }

    lateinit var gestureExecutor: GestureExecutor
    lateinit var uiTreeCapturer: UiTreeCapturer
    private var webSocketServer: AgentWebSocketServer? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        gestureExecutor = GestureExecutor(this)
        uiTreeCapturer = UiTreeCapturer(this)
        Log.i(TAG, "Accessibility service created")
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        Log.i(TAG, "Accessibility service connected")

        startForeground()

        val handler = CommandHandler(this)
        webSocketServer = AgentWebSocketServer(PORT, handler)

        try {
            webSocketServer?.start()
            Log.i(TAG, "WebSocket server started on port $PORT")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start WebSocket server: ${e.message}", e)
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Events are captured on-demand via get_ui_tree command.
        // Could be extended for push-based event notification.
    }

    override fun onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted")
    }

    override fun onUnbind(intent: Intent?): Boolean {
        webSocketServer?.stop()
        instance = null
        Log.i(TAG, "Accessibility service unbound")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        webSocketServer?.stop()
        instance = null
        super.onDestroy()
    }

    fun takeScreenshot(): Bitmap {
        val displayMetrics = resources.displayMetrics
        val w = displayMetrics.widthPixels
        val h = displayMetrics.heightPixels

        return if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            takeScreenshot(w, h)
        } else {
            takeScreenshotAsync(w, h)
        }
    }

    @Suppress("DEPRECATION")
    private fun takeScreenshotAsync(width: Int, height: Int): Bitmap {
        val latch = java.util.concurrent.CountDownLatch(1)
        var result: Bitmap? = null
        takeScreenshot(
            android.os.Handler.createAsync(android.os.Looper.getMainLooper())
        ) { screenshot ->
            result = screenshot
            latch.countDown()
        }
        latch.await(5, java.util.concurrent.TimeUnit.SECONDS)
        return result ?: throw RuntimeException("Screenshot timed out")
    }

    private fun startForeground() {
        val channel = NotificationChannel(
            NOTIFICATION_CHANNEL_ID,
            "Android Agent",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Android Agent accessibility service"
        }

        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(channel)

        val notification = Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle(getString(R.string.foreground_notification_title))
            .setContentText(getString(R.string.foreground_notification_text))
            .setSmallIcon(android.R.drawable.ic_menu_manage)
            .setOngoing(true)
            .build()

        startForeground(NOTIFICATION_ID, notification)
    }
}

/** Minimal foreground service for the WebSocket server. */
class WebSocketForegroundService : android.app.Service() {
    override fun onBind(intent: Intent?) = null
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_NOT_STICKY
    }
}
