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

    def format_ui_tree_for_llm(self, max_depth: int = 5, max_text_len: int = 60,
                              prune: bool = True, max_lines: int = 120) -> str:
        """Serialize current UI tree to a compact text format for the LLM.

        Optimization: Only includes actionable nodes (clickable, editable, has text).
        Filters layout containers, invisible nodes, and duplicates.
        """
        if not self.ui_tree:
            return "[No UI tree available. Use get_ui_tree() to capture the screen.]"

        lines = []
        seen_texts = set()  # Dedup same text across nodes
        screen_height = 0
        screen_width = 0
        if self.ui_tree.bounds:
            screen_height = self.ui_tree.bounds.get("bottom", 0)
            screen_width = self.ui_tree.bounds.get("right", 0)

        def _is_actionable(node: UIElement) -> bool:
            """Only keep nodes that are clickable, editable, scrollable, or have text."""
            if node.is_clickable or node.is_editable or node.is_scrollable:
                return True
            if node.text or node.content_desc:
                return True
            return False

        def _is_status_bar(node: UIElement) -> bool:
            """Detect and skip status bar area (top ~80px)."""
            if node.bounds:
                b = node.bounds
                if b.get("top", 0) < 80 and b.get("bottom", 0) < 80 and not node.is_clickable:
                    return True
            return False

        def _format_node(node: UIElement, depth: int = 0) -> None:
            if depth > max_depth:
                return
            if len(lines) >= max_lines:
                return
            if prune:
                if not node.is_enabled and depth > 0:
                    return
                # Skip invisible areas (status bar, navigation bar)
                if _is_status_bar(node):
                    pass  # Still process children - some overlays appear here
                # Skip empty layout containers
                if not _is_actionable(node) and node.children:
                    for child in node.children:
                        _format_node(child, depth)
                    return

            indent = "  " * depth
            props = []
            if node.is_clickable:
                props.append("C")  # Short form
            if node.is_scrollable:
                props.append("S")
            if node.is_editable:
                props.append("E")
            if node.is_focused:
                props.append("F")

            text = node.text[:max_text_len] if node.text else ""
            desc = node.content_desc[:max_text_len] if node.content_desc else ""

            label = text or desc or ""
            if label:
                label = label[:max_text_len]
                # Dedup
                if label in seen_texts and not node.is_clickable:
                    label = ""
                else:
                    seen_texts.add(label)

            bounds_str = ""
            if node.bounds and node.is_clickable:
                center = node.get_center()
                if center:
                    bounds_str = f" ({center[0]},{center[1]})"

            prop_str = f"[{','.join(props)}]" if props else ""
            rid = ""
            if node.resource_id:
                # Keep only the last part of the resource ID
                short_id = node.resource_id.split("/")[-1] if "/" in node.resource_id else node.resource_id
                rid = f" #{short_id}"

            if not label and not props and not rid:
                for child in node.children:
                    _format_node(child, depth)
                return

            line = f"{indent}{node.type}{rid} {label}{prop_str}{bounds_str}"
            lines.append(line)

            for child in node.children:
                _format_node(child, depth + 1)

        _format_node(self.ui_tree)

        # Compute hash to detect UI changes
        tree_text = "\n".join(lines)
        self.last_ui_hash = hashlib.md5(tree_text.encode()).hexdigest()[:8]

        if len(lines) >= max_lines:
            tree_text += f"\n[Truncated at {max_lines} lines. {len(lines)} total actionable nodes.]"

        return tree_text

    def get_ui_summary(self) -> str:
        """Ultra-compact UI summary for minimal context. Use when UI is unchanged."""
        if not self.ui_tree:
            return "[No UI tree]"
        lines = []
        def _collect(n: UIElement, d: int = 0):
            if d > 3 or len(lines) > 60:
                return
            label = (n.text or n.content_desc or "")[:40]
            if n.is_clickable or label:
                center = n.get_center() if n.bounds else None
                c = f"({center[0]},{center[1]})" if center else ""
                lines.append(f"{n.type}#{n.resource_id and n.resource_id.split('/')[-1] or ''}|{label}|{c}")
            for ch in n.children:
                _collect(ch, d + 1)
        _collect(self.ui_tree)
        return "|".join(lines)

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
