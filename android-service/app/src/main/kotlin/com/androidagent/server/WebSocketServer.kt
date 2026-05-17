package com.androidagent.server

import android.util.Log
import com.androidagent.App
import com.androidagent.model.Command
import com.androidagent.model.CommandResponse
import com.google.gson.Gson
import fi.iki.elonen.NanoHTTPD.IHTTPSession
import fi.iki.elonen.NanoWSD
import kotlinx.coroutines.*
import java.io.IOException

class AgentWebSocketServer(
    private val port: Int,
    private val handler: CommandHandler
) : NanoWSD(port) {

    companion object {
        private const val TAG = "${App.TAG}:WS"
    }

    private val gson = Gson()
    private val scope = CoroutineScope(Dispatchers.IO.limitedParallelism(1) + SupervisorJob())

    override fun openWebSocket(handshake: IHTTPSession): WebSocket {
        return AgentWebSocket(handshake)
    }

    @Volatile
    var connectedClients: Int = 0
        private set

    inner class AgentWebSocket(hs: IHTTPSession) : WebSocket(hs) {
        private val handshake = hs

        override fun onOpen() {
            connectedClients++
            com.androidagent.service.AgentAccessibilityService.instance?.connectedClients = connectedClients
            Log.i(TAG, "Client connected: ${handshake.remoteIpAddress} (total: $connectedClients)")
        }

        override fun onClose(code: WebSocketFrame.CloseCode, reason: String, initiatedByRemote: Boolean) {
            connectedClients--
            com.androidagent.service.AgentAccessibilityService.instance?.connectedClients = connectedClients
            Log.i(TAG, "Client disconnected: $reason (total: $connectedClients)")
        }

        override fun onMessage(message: WebSocketFrame) {
            val raw = message.textPayload
            Log.d(TAG, "Received: $raw")

            scope.launch {
                try {
                    val cmd = gson.fromJson(raw, Command::class.java)
                    val response = handler.handle(cmd)
                    val json = gson.toJson(response)
                    Log.d(TAG, "Sending: $json")
                    send(json)
                } catch (e: Exception) {
                    Log.e(TAG, "Error processing message", e)
                    // Try to extract the command id from the raw message for traceability
                    val cmdId = try {
                        gson.fromJson(raw, com.google.gson.JsonObject::class.java)
                            ?.get("id")?.asString ?: "unknown"
                    } catch (_: Exception) {
                        "unknown"
                    }
                    val err = CommandResponse.error(cmdId, "Parse error: ${e.message}")
                    send(gson.toJson(err))
                }
            }
        }

        override fun onPong(frame: WebSocketFrame?) {
        }

        override fun onException(exception: IOException?) {
            Log.e(TAG, "WebSocket exception", exception)
        }
    }

    override fun start() {
        Log.i(TAG, "Starting WebSocket server on port $port")
        super.start()
    }

    override fun stop() {
        scope.cancel()
        super.stop()
    }
}
