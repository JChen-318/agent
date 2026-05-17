"""
Wire protocol for agent ↔ Android device communication.

All messages are JSON over WebSocket.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional
import json
import uuid


class CommandType(StrEnum):
    # Gesture actions
    CLICK = "click"
    CLICK_BY_TEXT = "click_by_text"
    LONG_PRESS = "long_press"
    SWIPE = "swipe"
    SCROLL = "scroll"
    TYPE = "type"

    # System actions
    BACK = "back"
    HOME = "home"
    RECENT_APPS = "recent_apps"
    LAUNCH_APP = "launch_app"

    # State actions
    GET_UI_TREE = "get_ui_tree"
    SCREENSHOT = "screenshot"
    WAIT = "wait"

    # Meta
    PING = "ping"
    TASK_COMPLETE = "task_complete"
    ASK_USER = "ask_user"


class EventType(StrEnum):
    """Async events pushed from device to agent."""
    NOTIFICATION = "notification"
    APP_CHANGE = "app_change"
    SCREEN_CHANGE = "screen_change"
    ERROR = "error"


@dataclass
class Command:
    """Command sent from agent to device."""
    type: CommandType
    args: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: f"cmd_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type.value, "args": self.args}


@dataclass
class CommandResponse:
    """Response from device to agent for a specific command."""
    id: str
    status: str  # "ok" | "error"
    data: Optional[dict] = None
    error: Optional[str] = None
    message: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "CommandResponse":
        return cls(
            id=d.get("id", ""),
            status=d.get("status", "error"),
            data=d.get("data"),
            error=d.get("error"),
            message=d.get("message"),
        )

    @classmethod
    def ok(cls, cmd_id: str, data: Optional[dict] = None, message: Optional[str] = None) -> dict:
        return {"id": cmd_id, "status": "ok", "data": data or {}, "message": message}

    @classmethod
    def error(cls, cmd_id: str, error: str, message: Optional[str] = None) -> dict:
        return {"id": cmd_id, "status": "error", "error": error, "message": message}


@dataclass
class UIElement:
    """Deserialized UI tree node from device."""
    type: str = ""
    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    is_clickable: bool = False
    is_scrollable: bool = False
    is_editable: bool = False
    is_focused: bool = False
    is_enabled: bool = True
    bounds: Optional[dict] = None
    children: list["UIElement"] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "UIElement":
        children = [cls.from_dict(c) for c in d.get("children", [])]
        return cls(
            type=d.get("type", ""),
            text=d.get("text", ""),
            resource_id=d.get("resource_id", ""),
            content_desc=d.get("content_desc", ""),
            is_clickable=d.get("is_clickable", False),
            is_scrollable=d.get("is_scrollable", False),
            is_editable=d.get("is_editable", False),
            is_focused=d.get("is_focused", False),
            is_enabled=d.get("is_enabled", True),
            bounds=d.get("bounds"),
            children=children,
        )

    def find_by_text(self, text: str, exact: bool = False) -> Optional["UIElement"]:
        """Recursively find an element by its text."""
        if text in self.text if not exact else self.text == text:
            return self
        for child in self.children:
            found = child.find_by_text(text, exact)
            if found:
                return found
        return None

    def get_center(self) -> Optional[tuple[int, int]]:
        """Get the center point of this element's bounds."""
        if not self.bounds or not all(k in self.bounds for k in ("left", "top", "right", "bottom")):
            return None
        b = self.bounds
        return ((b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2)


def serialize_command(cmd: Command) -> str:
    return json.dumps(cmd.to_dict(), ensure_ascii=False)


def deserialize_response(raw: str) -> CommandResponse:
    return CommandResponse.from_dict(json.loads(raw))


def create_event(event_type: EventType, data: dict) -> dict:
    return {"type": "event", "event": event_type.value, "data": data}
