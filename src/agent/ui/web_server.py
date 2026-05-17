"""FastAPI web server for the Android Agent — serves the UI and WebSocket bridge."""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent.config.config import AppConfig, load_config
from agent.core.loop import AgentLoop
from agent.core.state import InteractionMode, SafetyLevel
from agent.llm.client import LLMClient
from agent.device.bridge import DeviceBridge
from agent.core.planner import Planner

logger = logging.getLogger(__name__)


def _static_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "agent" / "ui" / "static"
    return Path(__file__).parent / "static"


STATIC_DIR = _static_dir()


class AgentWebServer:
    """Wraps the agent in a FastAPI web server."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config(config_path)
        self._setup_logging()

        self.llm: Optional[LLMClient] = None
        self.bridge: Optional[DeviceBridge] = None
        self.loop: Optional[AgentLoop] = None
        self._ws_clients: list[WebSocket] = []
        self._device_connected = False

        self.app = FastAPI(title="Android Agent", version="0.1.0")
        self._setup_routes()

    def _setup_logging(self) -> None:
        level = getattr(logging, self.config.logging.level.upper(), logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    def init(self) -> None:
        self.llm = LLMClient(
            base_url=self.config.llm.base_url,
            api_key=self.config.llm.api_key,
            model=self.config.llm.model,
            max_tokens=self.config.llm.max_tokens,
            temperature=self.config.llm.temperature,
            max_retries=self.config.llm.max_retries,
            retry_delay_base=self.config.llm.retry_delay_base,
            retry_delay_max=self.config.llm.retry_delay_max,
            fallback_base_url=self.config.llm.fallback_base_url,
            fallback_api_key=self.config.llm.fallback_api_key,
            fallback_model=self.config.llm.fallback_model,
        )
        self.bridge = DeviceBridge(
            host=self.config.device.host,
            port=self.config.device.port,
            reconnect_interval=self.config.device.reconnect_interval,
            max_reconnect_attempts=self.config.device.max_reconnect_attempts,
        )
        mode = InteractionMode.CONTINUOUS
        safety = SafetyLevel(self.config.safety.confirmation_level)
        self.loop = AgentLoop(
            llm_client=self.llm,
            device_bridge=self.bridge,
            mode=mode,
            safety_level=safety,
            planner=Planner(self.llm),
        )
        # Route callbacks to WebSocket broadcast
        self.loop.on_action = lambda action, args: asyncio.ensure_future(
            self._broadcast({"type": "action", "action": action, "args": args})
        )
        self.loop.on_speak = lambda text: asyncio.ensure_future(
            self._broadcast({"type": "speak", "text": text})
        )

    async def _broadcast(self, data: dict) -> None:
        disconnected = []
        for ws in self._ws_clients:
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self._ws_clients.remove(ws)

    def _setup_routes(self) -> None:
        app = self.app

        @app.get("/", response_class=HTMLResponse)
        async def index():
            html_path = STATIC_DIR / "index.html"
            if html_path.exists():
                return html_path.read_text(encoding="utf-8")
            return "<h1>Android Agent</h1><p>Frontend not found. Run build first.</p>"

        @app.get("/api/status")
        async def status():
            return {
                "device_connected": self._device_connected,
                "agent_running": self.loop is not None and self.loop.running,
                "mode": self.config.interaction.mode,
                "model": self.config.llm.model,
            }

        @app.get("/api/config")
        async def get_config():
            return {
                "llm": {
                    "base_url": self.config.llm.base_url,
                    "model": self.config.llm.model,
                    "api_key_set": bool(self.config.llm.api_key),
                },
                "device": {
                    "host": self.config.device.host,
                    "port": self.config.device.port,
                },
                "whisper": {
                    "model_size": self.config.whisper.model_size,
                    "device": self.config.whisper.device,
                },
                "interaction": {"mode": self.config.interaction.mode},
                "safety": {"confirmation_level": self.config.safety.confirmation_level},
            }

        @app.post("/api/config")
        async def update_config(data: dict):
            if "llm" in data:
                llm = data["llm"]
                changed = False
                if "base_url" in llm and llm["base_url"] != self.config.llm.base_url:
                    self.config.llm.base_url = llm["base_url"]
                    changed = True
                if "api_key" in llm and llm["api_key"]:
                    self.config.llm.api_key = llm["api_key"]
                    changed = True
                if "model" in llm and llm["model"] != self.config.llm.model:
                    self.config.llm.model = llm["model"]
                    changed = True
                if changed and self.llm:
                    self.llm = LLMClient(
                        base_url=self.config.llm.base_url,
                        api_key=self.config.llm.api_key,
                        model=self.config.llm.model,
                        max_tokens=self.config.llm.max_tokens,
                        temperature=self.config.llm.temperature,
                        max_retries=self.config.llm.max_retries,
                        retry_delay_base=self.config.llm.retry_delay_base,
                        retry_delay_max=self.config.llm.retry_delay_max,
                        fallback_base_url=self.config.llm.fallback_base_url,
                        fallback_api_key=self.config.llm.fallback_api_key,
                        fallback_model=self.config.llm.fallback_model,
                    )
                    if self.loop:
                        self.loop.llm = self.llm
            if "device" in data:
                dev = data["device"]
                if "host" in dev:
                    self.config.device.host = dev["host"]
                if "port" in dev:
                    self.config.device.port = dev["port"]
            return {"status": "ok"}

        @app.post("/api/connect")
        async def connect_device():
            if not self.bridge:
                return JSONResponse({"error": "Agent not initialized"}, status_code=400)
            try:
                await self.bridge.connect()
                self._device_connected = True
                await self._broadcast({"type": "status", "device_connected": True})
                return {"status": "ok", "message": "Connected"}
            except Exception as e:
                self._device_connected = False
                return {"status": "error", "message": str(e)}

        @app.post("/api/disconnect")
        async def disconnect_device():
            if self.bridge and self.bridge.connected:
                await self.bridge.disconnect()
            self._device_connected = False
            await self._broadcast({"type": "status", "device_connected": False})
            return {"status": "ok"}

        @app.post("/api/command")
        async def send_command(data: dict):
            if not self.loop:
                return JSONResponse({"error": "Agent not initialized"}, status_code=400)
            text = data.get("text", "").strip()
            if not text:
                return JSONResponse({"error": "Empty command"}, status_code=400)
            if not self._device_connected:
                return JSONResponse({"error": "Device not connected"}, status_code=400)

            await self._broadcast({"type": "command_sent", "text": text})
            try:
                response = await self.loop.process_text_input(text)
                await self._broadcast({
                    "type": "response",
                    "text": response.content or "(action completed)",
                })
                return {"status": "ok", "response": response.content}
            except Exception as e:
                await self._broadcast({"type": "error", "text": str(e)})
                return {"status": "error", "message": str(e)}

        @app.websocket("/ws")
        async def websocket(ws: WebSocket):
            await ws.accept()
            self._ws_clients.append(ws)
            await ws.send_json({"type": "connected"})
            try:
                while True:
                    data = await ws.receive_json()
                    msg_type = data.get("type", "")
                    if msg_type == "ping":
                        await ws.send_json({"type": "pong"})
                    elif msg_type == "command":
                        text = data.get("text", "").strip()
                        if text and self.loop and self._device_connected:
                            await self._broadcast({"type": "command_sent", "text": text})
                            try:
                                response = await self.loop.process_text_input(text)
                                await self._broadcast({
                                    "type": "response",
                                    "text": response.content or "(done)",
                                })
                            except Exception as e:
                                await self._broadcast({"type": "error", "text": str(e)})
                    elif msg_type == "config_update":
                        if "data" in data:
                            await update_config(data["data"])
                            await ws.send_json({"type": "config_saved"})
            except WebSocketDisconnect:
                pass
            finally:
                if ws in self._ws_clients:
                    self._ws_clients.remove(ws)
