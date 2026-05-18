package com.androidagent

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import com.androidagent.service.AgentAccessibilityService
import java.net.Inet4Address
import java.net.NetworkInterface

class MainActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private var refreshRunning = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        findViewById<Button>(R.id.btnAccessibility).setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }

        findViewById<Button>(R.id.btnOverlay).setOnClickListener {
            startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION))
        }
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
        startPeriodicRefresh()
    }

    override fun onPause() {
        super.onPause()
        refreshRunning = false
    }

    private fun startPeriodicRefresh() {
        if (refreshRunning) return
        refreshRunning = true
        Thread {
            while (refreshRunning) {
                try {
                    Thread.sleep(3000)
                } catch (_: InterruptedException) {
                    break
                }
                handler.post { refreshStatus() }
            }
        }.start()
    }

    private fun refreshStatus() {
        val status = findViewById<TextView>(R.id.statusText)
        val enabled = isAccessibilityServiceEnabled()
        status.text = if (enabled) {
            "Status: Accessibility Service ENABLED"
        } else {
            "Status: Accessibility Service NOT ENABLED"
        }

        // WiFi IP
        val ipText = findViewById<TextView>(R.id.ipAddressText)
        val ip = getWifiIpAddress()
        if (ip != null) {
            ipText.text = "IP: $ip"
        } else {
            ipText.text = "IP: Not connected to WiFi"
        }

        // NSD status
        val nsdText = findViewById<TextView>(R.id.nsdStatusText)
        val svc = AgentAccessibilityService.instance
        if (svc != null && AgentAccessibilityService.nsdRegistered) {
            nsdText.text = "mDNS: Advertising on network"
        } else if (svc != null) {
            nsdText.text = "mDNS: Not advertising"
        } else {
            nsdText.text = "mDNS: Service not started"
        }

        // Client count
        val clientsText = findViewById<TextView>(R.id.clientsText)
        val clientCount = AgentAccessibilityService.connectedClients
        clientsText.text = "Clients: $clientCount"
    }

    private fun getWifiIpAddress(): String? {
        try {
            val wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            if (wifiManager != null) {
                val ipInt = wifiManager.connectionInfo.ipAddress
                if (ipInt != 0) {
                    return String.format(
                        "%d.%d.%d.%d",
                        ipInt and 0xff,
                        (ipInt shr 8) and 0xff,
                        (ipInt shr 16) and 0xff,
                        (ipInt shr 24) and 0xff
                    )
                }
            }

            // Fallback: enumerate network interfaces (works for Ethernet too)
            return NetworkInterface.getNetworkInterfaces()?.toList()?.firstNotNullOfOrNull { iface ->
                iface.inetAddresses?.toList()?.firstOrNull { addr ->
                    !addr.isLoopbackAddress && addr is Inet4Address
                }?.hostAddress
            }
        } catch (_: Exception) {
            return null
        }
    }

    private fun isAccessibilityServiceEnabled(): Boolean {
        val service = "$packageName/.service.AgentAccessibilityService"
        val enabledServices = Settings.Secure.getString(
            contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        return enabledServices.contains(service) || enabledServices.contains("com.androidagent")
    }
}
