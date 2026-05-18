"""WebSocket client for communicating with the Android device."""

import asyncio
import base64
import json
import logging
import random
import time
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

# Actions that are safe to retry on transient errors
RETRYABLE_ACTIONS = {"click", "click_by_text", "swipe", "scroll", "back", "home",
                     "recent_apps", "get_ui_tree", "wait", "long_press"}
MAX_ACTION_RETRIES = 2


class DeviceBridge:
    """WebSocket client that connects to the Android APK's WS server."""

    def __init__(
        self,
        host: str = "192.168.1.100",
        port: int = 18765,
        reconnect_interval: int = 5,
        max_reconnect_attempts: int = 0,
        adb_path: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.reconnect_interval = reconnect_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        self.adb_path = adb_path
        if self.adb_path:
            from pathlib import Path
            p = Path(self.adb_path)
            if not p.is_absolute():
                p = Path.cwd() / p
            if p.exists():
                self.adb_path = str(p.resolve())
            else:
                logger.warning(f"ADB path doesn't exist: {p}")
                self.adb_path = None
        self.ws: Optional[ClientConnection] = None
        self.connected = False
        self._request_id = 0
        self._event_handlers: dict[str, list] = {}

        # Action result cache for read-only operations
        self._action_cache: dict[tuple, dict] = {}
        self._cache_max_size = 50
        self._cache_ttl = 2.0  # seconds

    async def connect(self) -> None:
        """Connect to phone WebSocket server with auto-reconnect."""
        url = f"ws://{self.host}:{self.port}"
        attempts = 0

        while True:
            try:
                self.ws = await websockets.connect(
                    url, ping_interval=None, max_size=10 * 1024 * 1024
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
            await self.disconnect()
            await self.connect()

        payload = serialize_command(cmd)
        logger.debug(f"Sending: {payload}")

        try:
            await asyncio.wait_for(self.ws.send(payload), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Timed out sending command {cmd.id}")
        except websockets.ConnectionClosed as e:
            self.connected = False
            raise ConnectionError(f"Connection closed during send: {e}")

        # Wait for matching response
        try:
            async for raw in self.ws:
                data = json.loads(raw)
                resp = CommandResponse.from_dict(data)
                if resp.id == cmd.id:
                    self.connected = False  # Phone server closes after each response
                    return resp
                elif data.get("type") == "event":
                    self._dispatch_event(data)
        except websockets.ConnectionClosed as e:
            self.connected = False
            raise ConnectionError(f"Connection closed: {e}")

        raise ConnectionError("WebSocket closed while waiting for response")

    async def execute(self, action: str, args: dict, timeout: float = 30.0) -> dict:
        """Execute any action on device. Retries idempotent actions on transient errors."""
        try:
            cmd_type = CommandType(action)
        except ValueError:
            return {
                "status": "error",
                "data": {},
                "error": f"Unknown action: {action}",
                "message": f"No such command type: {action}",
            }

        cmd = Command(type=cmd_type, args=args)

        # Check result cache for read-only queries
        if action == "get_ui_tree":
            cache_key = (action, json.dumps(args, sort_keys=True))
            if cache_key in self._action_cache:
                entry = self._action_cache[cache_key]
                if time.time() - entry["_ts"] < self._cache_ttl:
                    return entry["result"]
                del self._action_cache[cache_key]

        max_retries = MAX_ACTION_RETRIES if action in RETRYABLE_ACTIONS else 0
        last_result: Optional[dict] = None
        for attempt in range(max_retries + 1):
            try:
                resp = await self.send_command(cmd, timeout=timeout)
                result = {
                    "status": resp.status,
                    "data": resp.data or {},
                    "error": resp.error,
                    "message": resp.message,
                }
                if result.get("status") == "error":
                    err_str = str(result.get("error", "")).lower()
                    if any(sub in err_str for sub in ("timed out", "connection", "reset", "broken pipe")):
                        if attempt < max_retries:
                            delay = 0.5 * (2 ** attempt) + random.random()
                            logger.warning(
                                f"Device action '{action}' transient error (attempt {attempt+1}), "
                                f"retrying in {delay:.1f}s: {err_str}"
                            )
                            await asyncio.sleep(delay)
                            last_result = result
                            continue
                # Cache successful read-only results
                if action == "get_ui_tree" and result.get("status") == "ok":
                    if len(self._action_cache) >= self._cache_max_size:
                        oldest = min(self._action_cache, key=lambda k: self._action_cache[k]["_ts"])
                        del self._action_cache[oldest]
                    self._action_cache[cache_key] = {"result": result, "_ts": time.time()}
                return result
            except (ConnectionError, TimeoutError, websockets.ConnectionClosed) as e:
                last_result = {
                    "status": "error",
                    "data": {},
                    "error": str(e),
                    "message": f"Failed to execute {action}",
                }
                if attempt < max_retries:
                    delay = 0.5 * (2 ** attempt) + random.random()
                    logger.warning(
                        f"Device action '{action}' failed (attempt {attempt+1}), "
                        f"retrying in {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                    # Reconnect before retry
                    try:
                        await self.disconnect()
                        await self.connect()
                    except Exception as ce:
                        logger.warning(f"Reconnect failed: {ce}")
                else:
                    return last_result

        return last_result or {"status": "error", "data": {}, "error": "Max retries exhausted"}

    async def get_ui_tree(self, max_depth: int = 20) -> dict:
        """Request full UI tree from device."""
        result = await self.execute("get_ui_tree", {"max_depth": max_depth})
        return result.get("data", {})

    async def screenshot(self) -> Optional[str]:
        """Request screenshot from device, returns base64-encoded JPEG.
        Falls back to ADB screencap if WebSocket method fails or device is old.
        """
        # Try WebSocket screenshot first
        result = await self.execute("screenshot", {})
        data = result.get("data", {})
        ws_image = data.get("image_base64")
        if ws_image:
            return ws_image

        # Fallback: ADB screencap (works on Android 8+)
        if self.adb_path:
            logger.info("WebSocket screenshot failed, trying ADB screencap fallback...")
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.adb_path, "exec-out", "screencap", "-p",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
                if proc.returncode == 0 and stdout:
                    # Convert PNG to base64
                    return base64.b64encode(stdout).decode()
                logger.warning(f"ADB screencap failed: {stderr.decode() if stderr else 'no output'}")
            except asyncio.TimeoutError:
                logger.warning("ADB screencap timed out")
            except FileNotFoundError:
                logger.warning(f"ADB binary not found: {self.adb_path}")
            except Exception:
                logger.exception("ADB screencap unexpected error")

        return None

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
