package com.androidagent.model

import com.google.gson.annotations.SerializedName

/** Incoming command from agent. */
data class Command(
    val id: String = "",
    val type: String = "",
    val args: Map<String, Any?> = emptyMap()
)

/** Response sent back to agent. */
data class CommandResponse(
    val id: String = "",
    val status: String = "ok",
    val data: Map<String, Any?>? = null,
    val error: String? = null,
    val message: String? = null
) {
    companion object {
        fun ok(id: String, data: Map<String, Any?>? = null, message: String? = null) =
            CommandResponse(id = id, status = "ok", data = data, message = message)

        fun error(id: String, error: String, message: String? = null) =
            CommandResponse(id = id, status = "error", error = error, message = message)
    }
}

/** Async event pushed from device to agent. */
data class DeviceEvent(
    val type: String = "event",
    val event: String,
    val data: Map<String, Any?>
)
