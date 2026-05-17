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
    }

    lateinit var gestureExecutor: GestureExecutor
    lateinit var uiTreeCapturer: UiTreeCapturer
    private var webSocketServer: AgentWebSocketServer? = null
    private var nsdManager: NsdManager? = null
    private var nsdRegistered = false

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
        // Events are captured on-demand via get_ui_tree command.
        // Could be extended for push-based event notification.
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

    fun captureScreenshot(): Bitmap? {
        // TODO: implement via MediaProjection for API < 34
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
