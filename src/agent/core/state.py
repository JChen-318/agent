"""Global state manager — conversation history, device state, interaction mode."""

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional, TYPE_CHECKING

from agent.device.protocol import UIElement

if TYPE_CHECKING:
    from agent.core.planner import TaskPlan, SubTask


class InteractionMode(StrEnum):
    SINGLE_COMMAND = "single_command"
    CONTINUOUS = "continuous"


class SafetyLevel(StrEnum):
    NONE = "none"       # Confirm nothing
    SENSITIVE = "sensitive"  # Confirm potentially risky actions
    ALL = "all"         # Confirm every action


SENSITIVE_ACTIONS = {"type", "launch_app"}

BLOCKED_PATTERNS = [
    "password", "密码", "passwd",
    "credit card", "信用卡",
    "cvv", "安全码",
    "验证码", "verification code",
    "ssn", "身份证",
]


@dataclass
class GlobalState:
    """Manages conversation context, device state, and safety."""

    mode: InteractionMode = InteractionMode.SINGLE_COMMAND
    safety_level: SafetyLevel = SafetyLevel.SENSITIVE
    blocked_patterns: list[str] = field(default_factory=lambda: BLOCKED_PATTERNS.copy())

    # Conversation history
    messages: list[dict] = field(default_factory=list)

    # Device state
    ui_tree: Optional[UIElement] = None
    ui_tree_raw: Optional[dict] = None
    last_screenshot: Optional[str] = None
    focused_window: Optional[str] = None
    last_ui_hash: Optional[str] = None
    _prev_ui_hash: Optional[str] = None

    # Task planning
    current_plan: Optional[object] = None  # TaskPlan | None
    current_task: Optional[object] = None  # SubTask | None
    task_results: list[dict] = field(default_factory=list)

    # Metrics
    iteration_count: int = 0
    max_iterations: int = 50

    def add_message(self, role: str, content, tool_call_id: Optional[str] = None,
                    tool_calls: Optional[list] = None) -> None:
        """Add a message to conversation history."""
        msg = {"role": role}

        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
            msg["content"] = json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else str(content)
        elif tool_calls:
            msg["tool_calls"] = tool_calls
            msg["content"] = content
        else:
            msg["content"] = content

        self.messages.append(msg)

    def get_recent_history(self, n: int = 40) -> list[dict]:
        """Get the last N messages from conversation history."""
        if len(self.messages) <= n:
            return self.messages.copy()
        return self.messages[-n:]

    def update_device_state(self, ui_tree_raw: dict) -> None:
        """Update the stored device state from raw UI tree dict."""
        self.ui_tree_raw = ui_tree_raw
        self._prev_ui_hash = self.last_ui_hash
        if ui_tree_raw:
            self.ui_tree = UIElement.from_dict(ui_tree_raw)
        self.iteration_count += 1

    def format_ui_tree_for_llm(self, max_depth: int = 8, max_text_len: int = 80) -> str:
        """Serialize current UI tree to a compact text format for the LLM."""
        if not self.ui_tree:
            return "[No UI tree available. Use get_ui_tree() to capture the screen.]"

        lines = []

        def _format_node(node: UIElement, depth: int = 0) -> None:
            if depth > max_depth:
                return
            if not node.is_enabled and depth > 0:
                return

            indent = "  " * depth

            props = []
            if node.is_clickable:
                props.append("clickable")
            if node.is_scrollable:
                props.append("scrollable")
            if node.is_editable:
                props.append("editable")
            if node.is_focused:
                props.append("focused")

            text = node.text[:max_text_len] if node.text else ""
            desc = node.content_desc[:max_text_len] if node.content_desc else ""

            label = text or desc or ""
            if label and len(label) > max_text_len:
                label = label[:max_text_len - 3] + "..."

            bounds_str = ""
            if node.bounds:
                b = node.bounds
                center = node.get_center()
                bounds_str = f" @({b.get('left',0)},{b.get('top',0)},{b.get('right',0)},{b.get('bottom',0)})"
                if center:
                    bounds_str += f" center=({center[0]},{center[1]})"

            prop_str = f"[{','.join(props)}]" if props else ""
            rid = f"#{node.resource_id}" if node.resource_id else ""
            line = f"{indent}{node.type}{rid} \"{label}\" {prop_str}{bounds_str}"
            lines.append(line)

            for child in node.children:
                _format_node(child, depth + 1)

        _format_node(self.ui_tree)

        # Compute hash to detect UI changes
        tree_text = "\n".join(lines)
        self.last_ui_hash = hashlib.md5(tree_text.encode()).hexdigest()[:8]

        return tree_text

    def needs_confirmation(self, action: str, args: dict) -> bool:
        """Check if this action needs user confirmation based on safety level."""
        if self.safety_level == SafetyLevel.NONE:
            return False
        if self.safety_level == SafetyLevel.ALL:
            return True
        return action in SENSITIVE_ACTIONS

    def check_safety(self, action: str, args: dict) -> Optional[str]:
        """Check if action is blocked by safety rules. Returns block reason or None."""
        if action == "type":
            text = str(args.get("text", "")).lower()
            for pattern in self.blocked_patterns:
                if pattern.lower() in text:
                    return f"Blocked: input text matches sensitive pattern '{pattern}'"
        return None

    def reset_iteration(self) -> None:
        """Reset only iteration counter (between sub-tasks, preserving history)."""
        self.iteration_count = 0
        self._prev_ui_hash = None

    def reset(self) -> None:
        """Reset state for a new command."""
        self.messages.clear()
        self.iteration_count = 0
        self.last_ui_hash = None
        self._prev_ui_hash = None
        self.current_plan = None
        self.current_task = None
        self.task_results.clear()
