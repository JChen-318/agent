"""WebSocket client for communicating with the Android device."""

import asyncio
import json
import logging
from typing import Optional

import websockets
from websockets.asyncio.client import ClientConnection

from agent.device.protocol import (
    Command,
    CommandType,
    CommandResponse,
    EventType,
    deserialize_response,
    serialize_command,
)

logger = logging.getLogger(__name__)


class DeviceBridge:
    """WebSocket client that connects to the Android APK's WS server."""

    def __init__(
        self,
        host: str = "192.168.1.100",
        port: int = 8765,
        reconnect_interval: int = 5,
        max_reconnect_attempts: int = 0,
    ):
        self.host = host
        self.port = port
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        self.ws: Optional[ClientConnection] = None
        self.connected = False
        self._request_id = 0
        self._event_handlers: dict[str, list] = {}

    async def connect(self) -> None:
        """Connect to phone WebSocket server with auto-reconnect."""
        url = f"ws://{self.host}:{self.port}"
        attempts = 0

        while True:
            try:
                self.ws = await websockets.connect(
                    url, ping_interval=30, ping_timeout=10, max_size=10 * 1024 * 1024
                )
                self.connected = True
                logger.info(f"Connected to device at {url}")
                return
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError) as e:
                attempts += 1
                if 0 < self.max_reconnect_attempts <= attempts:
                    raise ConnectionError(f"Failed after {attempts} attempts") from e
                logger.warning(f"Connection failed ({e}), retrying in {self.reconnect_interval}s...")
                await asyncio.sleep(self.reconnect_interval)

    async def disconnect(self) -> None:
        if self.ws:
            await self.ws.close()
            self.connected = False

    async def send_command(self, cmd: Command, timeout: float = 30.0) -> CommandResponse:
        """Send a command and wait for matching response."""
        if not self.ws or not self.connected:
            raise ConnectionError("Not connected to device")

        payload = serialize_command(cmd)
        logger.debug(f"Sending: {payload}")

        try:
            await asyncio.wait_for(self.ws.send(payload), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Timed out sending command {cmd.id}")

        # Wait for matching response
        try:
            async for raw in self.ws:
                data = json.loads(raw)
                resp = CommandResponse.from_dict(data)
                if resp.id == cmd.id:
                    return resp
                elif data.get("type") == "event":
                    self._dispatch_event(data)
        except websockets.ConnectionClosed as e:
            self.connected = False
            raise ConnectionError(f"Connection closed: {e}")

        raise ConnectionError("WebSocket closed while waiting for response")

    async def execute(self, action: str, args: dict, timeout: float = 30.0) -> dict:
        """Execute any action on device and return result dict."""
        cmd_type = CommandType(action)
        cmd = Command(type=cmd_type, args=args)
        resp = await self.send_command(cmd, timeout=timeout)
        return {
            "status": resp.status,
            "data": resp.data or {},
            "error": resp.error,
            "message": resp.message,
        }

    async def get_ui_tree(self, max_depth: int = 20) -> dict:
        """Request full UI tree from device."""
        result = await self.execute("get_ui_tree", {"max_depth": max_depth})
        return result.get("data", {})

    async def screenshot(self) -> Optional[str]:
        """Request screenshot from device, returns base64-encoded image."""
        result = await self.execute("screenshot", {})
        data = result.get("data", {})
        return data.get("image_base64")

    async def ping(self, timeout: float = 5.0) -> bool:
        """Check if device is responsive."""
        try:
            result = await self.execute("ping", {}, timeout=timeout)
            return result.get("status") == "ok"
        except Exception:
            return False

    def on_event(self, event_type: EventType, handler):
        """Register handler for async device events."""
        key = event_type.value
        if key not in self._event_handlers:
            self._event_handlers[key] = []
        self._event_handlers[key].append(handler)

    def _dispatch_event(self, data: dict) -> None:
        event_key = data.get("event", "")
        handlers = self._event_handlers.get(event_key, [])
        for handler in handlers:
            try:
                handler(data.get("data", {}))
            except Exception:
                logger.exception(f"Event handler error for {event_key}")

    def _next_id(self) -> str:
        self._request_id += 1
        return f"req_{self._request_id:04d}"
