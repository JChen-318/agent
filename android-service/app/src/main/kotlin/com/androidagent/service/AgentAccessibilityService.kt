package com.androidagent.service

import android.accessibilityservice.AccessibilityService
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Build
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
        const val PORT = 18765
        const val NOTIFICATION_CHANNEL_ID = "android_agent_service"
        const val NOTIFICATION_ID = 1001

        @Volatile
        var instance: AgentAccessibilityService? = null
            private set

        @Volatile
        var nsdRegistered: Boolean = false
            private set

        @Volatile
        var connectedClients: Int = 0
    }

    lateinit var gestureExecutor: GestureExecutor
    lateinit var uiTreeCapturer: UiTreeCapturer
    private var webSocketServer: AgentWebSocketServer? = null
    private var nsdManager: NsdManager? = null

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
            registerNsd()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start WebSocket server: ${e.message}", e)
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event != null) {
            when (event.eventType) {
                AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED,
                AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                    uiTreeCapturer.contentChangedSinceLastCapture = true
                }
            }
        }
    }

    override fun onInterrupt() {
        Log.w(TAG, "Accessibility service interrupted")
    }

    override fun onUnbind(intent: Intent?): Boolean {
        unregisterNsd()
        webSocketServer?.stop()
        instance = null
        Log.i(TAG, "Accessibility service unbound")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        unregisterNsd()
        webSocketServer?.stop()
        instance = null
        super.onDestroy()
    }

    @Suppress("DEPRECATION")
    fun captureScreenshot(): Bitmap? {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val future = java.util.concurrent.CompletableFuture<Bitmap?>()
            takeScreenshot(
                android.view.Display.DEFAULT_DISPLAY,
                java.util.concurrent.Executors.newSingleThreadExecutor(),
                object : android.accessibilityservice.AccessibilityService.TakeScreenshotCallback {
                    override fun onSuccess(result: android.accessibilityservice.AccessibilityService.ScreenshotResult) {
                        try {
                            val bitmap = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                                val buffer = result.hardwareBuffer
                                if (buffer != null) {
                                    try {
                                        Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)
                                            ?.copy(Bitmap.Config.ARGB_8888, false)
                                    } finally {
                                        buffer.close()
                                    }
                                } else null
                            } else {
                                // API 30-33: getBitmap() removed from SDK 36 stubs, use reflection
                                try {
                                    val method = result.javaClass.getMethod("getBitmap")
                                    method.invoke(result) as? Bitmap
                                } catch (_: Exception) {
                                    null
                                }
                            }
                            future.complete(bitmap)
                        } catch (e: Exception) {
                            future.complete(null)
                        }
                    }
                    override fun onFailure(errorCode: Int) {
                        future.complete(null)
                    }
                }
            )
            try {
                return future.get(3, java.util.concurrent.TimeUnit.SECONDS)
            } catch (_: Exception) {
                return null
            }
        }
        return null
    }

    private fun registerNsd() {
        nsdManager = getSystemService(Context.NSD_SERVICE) as NsdManager
        val serviceInfo = NsdServiceInfo().apply {
            serviceName = "AndroidAgent-${Build.MODEL.replace(" ", "-")}"
            serviceType = "_android-agent._tcp"
            port = PORT
        }
        nsdManager?.registerService(serviceInfo, NsdManager.PROTOCOL_DNS_SD, object : NsdManager.RegistrationListener {
            override fun onRegistrationFailed(serviceInfo: NsdServiceInfo?, errorCode: Int) {
                Log.e(TAG, "NSD registration failed: $errorCode")
                nsdRegistered = false
            }
            override fun onUnregistrationFailed(serviceInfo: NsdServiceInfo?, errorCode: Int) {}
            override fun onServiceRegistered(serviceInfo: NsdServiceInfo?) {
                Log.i(TAG, "NSD registered: ${serviceInfo?.serviceName}")
                nsdRegistered = true
            }
            override fun onServiceUnregistered(serviceInfo: NsdServiceInfo?) {
                Log.i(TAG, "NSD unregistered")
                nsdRegistered = false
            }
        })
    }

    private fun unregisterNsd() {
        if (nsdRegistered && nsdManager != null) {
            nsdManager?.unregisterService(object : NsdManager.RegistrationListener {
                override fun onServiceUnregistered(serviceInfo: NsdServiceInfo?) {}
                override fun onRegistrationFailed(serviceInfo: NsdServiceInfo?, errorCode: Int) {}
                override fun onUnregistrationFailed(serviceInfo: NsdServiceInfo?, errorCode: Int) {}
                override fun onServiceRegistered(serviceInfo: NsdServiceInfo?) {}
            })
            nsdRegistered = false
        }
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
