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
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    override fun openWebSocket(handshake: IHTTPSession): WebSocket {
        return AgentWebSocket(handshake)
    }

    inner class AgentWebSocket(hs: IHTTPSession) : WebSocket(hs) {
        private val handshake = hs

        override fun onOpen() {
            Log.i(TAG, "Client connected: ${handshake.remoteIpAddress}")
        }

        override fun onClose(code: WebSocketFrame.CloseCode, reason: String, initiatedByRemote: Boolean) {
            Log.i(TAG, "Client disconnected: $reason")
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
                    val err = CommandResponse.error("unknown", "Parse error: ${e.message}")
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
