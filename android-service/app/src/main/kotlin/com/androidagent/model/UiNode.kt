package com.androidagent.model

/** Serializable UI tree node for sending to the agent. */
data class UiNode(
    val type: String = "",
    val text: String = "",
    val resourceId: String = "",
    val contentDesc: String = "",
    val isClickable: Boolean = false,
    val isScrollable: Boolean = false,
    val isEditable: Boolean = false,
    val isFocused: Boolean = false,
    val isEnabled: Boolean = true,
    val bounds: Map<String, Int>? = null,
    val centerX: Int = 0,
    val centerY: Int = 0,
    val children: List<UiNode> = emptyList()
)
