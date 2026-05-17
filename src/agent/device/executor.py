"""High-level action orchestration on top of the device bridge."""

import logging
from typing import Optional

from agent.device.bridge import DeviceBridge
from agent.device.protocol import UIElement

logger = logging.getLogger(__name__)


class ActionExecutor:
    """Orchestrates high-level actions using the device bridge."""

    def __init__(self, bridge: DeviceBridge):
        self.bridge = bridge

    async def tap(self, x: int, y: int) -> dict:
        return await self.bridge.execute("click", {"x": x, "y": y})

    async def tap_text(self, text: str, instance: int = 0) -> dict:
        return await self.bridge.execute("click_by_text", {"text": text, "instance": instance})

    async def long_press(self, x: int, y: int, duration_ms: int = 1000) -> dict:
        return await self.bridge.execute("long_press", {"x": x, "y": y, "duration_ms": duration_ms})

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 300,
    ) -> dict:
        return await self.bridge.execute("swipe", {
            "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration_ms,
        })

    async def scroll(self, direction: str = "forward", steps: int = 1) -> dict:
        return await self.bridge.execute("scroll", {"direction": direction, "steps": steps})

    async def type_text(self, text: str, clear_first: bool = True) -> dict:
        return await self.bridge.execute("type", {"text": text, "clear_first": clear_first})

    async def press_back(self) -> dict:
        return await self.bridge.execute("back", {})

    async def press_home(self) -> dict:
        return await self.bridge.execute("home", {})

    async def recent_apps(self) -> dict:
        return await self.bridge.execute("recent_apps", {})

    async def launch_app(self, package_name: str, activity: Optional[str] = None) -> dict:
        args = {"package_name": package_name}
        if activity:
            args["activity"] = activity
        return await self.bridge.execute("launch_app", args)

    async def wait(self, duration_ms: int) -> dict:
        return await self.bridge.execute("wait", {"duration_ms": duration_ms})

    async def get_ui_tree(self) -> Optional[UIElement]:
        data = await self.bridge.get_ui_tree()
        tree_data = data.get("ui_tree")
        if tree_data:
            return UIElement.from_dict(tree_data)
        return None

    async def screenshot(self) -> Optional[str]:
        return await self.bridge.screenshot()
