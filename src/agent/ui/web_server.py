"""FastAPI web server for the Android Agent — serves the UI and WebSocket bridge."""

import asyncio
import json
import logging
import os
import threading
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
from agent.device.discovery import DeviceDiscovery
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
        self.discovery: Optional[DeviceDiscovery] = None
        self._ws_clients: list[WebSocket] = []
        self._device_connected = False
        self._pending_broadcasts: list[dict] = []

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

        # Start mDNS device discovery
        self._start_discovery()

    def _start_discovery(self) -> None:
        """Start device discovery and broadcast new devices to UI."""
        def _on_device_change() -> None:
            if not self.discovery:
                return
            usb_list = []
            for d in self.discovery.adb_devices:
                info = {}
                if self.discovery._adb:
                    info = self.discovery._adb.get_device_info(d.serial)
                usb_list.append({
                    "serial": d.serial,
                    "display_name": d.display_name,
                    "state": d.state,
                    "model": info.get("model", d.model),
                    "android_version": info.get("android_version", ""),
                    "type": "usb",
                })
            data = {
                "type": "devices",
                "devices": [
                    {"name": d.name, "display_name": d.display_name,
                     "address": d.address, "port": d.port, "model": d.model, "type": "wifi"}
                    for d in self.discovery.devices
                ],
                "usb_devices": usb_list,
                "adb_available": self.discovery.adb_available,
            }
            self._schedule_broadcast(data)

        self.discovery = DeviceDiscovery(on_change=_on_device_change)
        self.discovery.start()

    def _schedule_broadcast(self, data: dict) -> None:
        """Schedule a broadcast from a non-async context (thread-safe)."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(data))
        except RuntimeError:
            # Called from a thread without a running event loop — queue for later
            self._pending_broadcasts.append(data)

    def _flush_broadcasts(self) -> None:
        """Send any queued broadcasts (called from the main event loop)."""
        pending = self._pending_broadcasts[:]
        self._pending_broadcasts.clear()
        for data in pending:
            asyncio.ensure_future(self._broadcast(data))

    def shutdown(self) -> None:
        if self.discovery:
            self.discovery.stop()

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
        async def connect_device(data: dict = {}):
            if not self.bridge:
                return JSONResponse({"error": "Agent not initialized"}, status_code=400)
            host = data.get("host", self.config.device.host)
            port = data.get("port", self.config.device.port)
            if host != self.bridge.host or port != self.bridge.port:
                self.bridge = DeviceBridge(
                    host=host, port=port,
                    reconnect_interval=self.config.device.reconnect_interval,
                    max_reconnect_attempts=self.config.device.max_reconnect_attempts,
                )
                if self.loop:
                    self.loop.device = self.bridge
                    self.loop.executor._bridge = self.bridge
                self.config.device.host = host
                self.config.device.port = port
            try:
                await self.bridge.connect()
                self._device_connected = True
                await self._broadcast({"type": "status", "device_connected": True})
                return {"status": "ok", "message": f"Connected to {host}:{port}"}
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

        @app.get("/api/devices")
        async def list_devices():
            if not self.discovery:
                return {"devices": [], "usb_devices": [], "adb_available": False, "scanning": False}

            # Network devices
            net_devices = [
                {"name": d.name, "display_name": d.display_name,
                 "address": d.address, "port": d.port, "model": d.model, "type": "wifi"}
                for d in self.discovery.devices
            ]

            # ADB USB devices
            usb_devices = []
            for d in self.discovery.adb_devices:
                info = {}
                if self.discovery._adb:
                    info = self.discovery._adb.get_device_info(d.serial)
                usb_devices.append({
                    "serial": d.serial,
                    "display_name": d.display_name,
                    "state": d.state,
                    "model": info.get("model", d.model),
                    "android_version": info.get("android_version", ""),
                    "type": "usb",
                })

            return {
                "devices": net_devices,
                "usb_devices": usb_devices,
                "adb_available": self.discovery.adb_available,
                "scanning": self.discovery.is_scanning,
            }

        @app.post("/api/scan")
        async def scan_network():
            if not self.discovery:
                return {"status": "error", "message": "Discovery not running"}
            threading.Thread(target=self.discovery.scan_network, daemon=True).start()
            return {"status": "ok", "message": "Scan started"}

        @app.post("/api/adb-forward")
        async def adb_forward(data: dict):
            if not self.discovery or not self.discovery._adb:
                return {"status": "error", "message": "ADB not available"}
            serial = data.get("serial", "")
            local_port = data.get("local_port", 18765)
            if not serial:
                return {"status": "error", "message": "Serial required"}
            ok = self.discovery.adb_forward(serial, local_port)
            if ok:
                return {"status": "ok", "message": f"Forwarded to 127.0.0.1:{local_port}",
                        "host": "127.0.0.1", "port": local_port}
            return {"status": "error", "message": "Forward failed"}

        @app.post("/api/adb-connect")
        async def adb_connect_wireless(data: dict):
            """Connect to a device over WiFi via ADB TCP/IP."""
            if not self.discovery or not self.discovery._adb:
                return {"status": "error", "message": "ADB not available"}
            host = data.get("host", "")
            port = data.get("port", 5555)
            if not host:
                return {"status": "error", "message": "Host IP required"}
            ok = self.discovery._adb.connect_tcpip(host, port)
            if ok:
                return {"status": "ok", "message": f"ADB connected to {host}:{port}"}
            return {"status": "error", "message": f"Failed to connect to {host}:{port}"}

        @app.post("/api/adb-enable-tcpip")
        async def adb_enable_tcpip(data: dict):
            """Enable ADB over TCP/IP on a USB-connected device."""
            if not self.discovery or not self.discovery._adb:
                return {"status": "error", "message": "ADB not available"}
            serial = data.get("serial", "")
            port = data.get("port", 5555)
            if not serial:
                return {"status": "error", "message": "Serial required"}
            ok = self.discovery._adb.enable_tcpip(serial, port)
            if ok:
                # Try to get device IP
                ip = self.discovery._adb.get_device_ip(serial)
                if ip:
                    return {"status": "ok", "message": f"TCP/IP enabled. Device IP: {ip}", "ip": ip}
                return {"status": "ok", "message": "TCP/IP enabled. Unplug USB and connect via IP."}
            return {"status": "error", "message": "Failed to enable TCP/IP"}

        @app.get("/api/network-info")
        async def network_info():
            """Return local machine network info for wireless debugging."""
            import socket
            local_ip = "127.0.0.1"
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.1)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                pass
            subnet = ".".join(local_ip.split(".")[:3]) + ".0/24"
            return {
                "local_ip": local_ip,
                "subnet": subnet,
                "device_port": self.bridge.port if self.bridge else 18765,
            }

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
            # Flush any queued broadcasts
            self._flush_broadcasts()
            # Send current discovered devices
            if self.discovery:
                usb_list = []
                for d in self.discovery.adb_devices:
                    info = {}
                    if self.discovery._adb:
                        info = self.discovery._adb.get_device_info(d.serial)
                    usb_list.append({
                        "serial": d.serial,
                        "display_name": d.display_name,
                        "state": d.state,
                        "model": info.get("model", d.model),
                        "android_version": info.get("android_version", ""),
                        "type": "usb",
                    })
                await ws.send_json({
                    "type": "devices",
                    "devices": [
                        {"name": d.name, "display_name": d.display_name,
                         "address": d.address, "port": d.port, "model": d.model, "type": "wifi"}
                        for d in self.discovery.devices
                    ],
                    "usb_devices": usb_list,
                    "adb_available": self.discovery.adb_available,
                })
            try:
                while True:
                    data = await ws.receive_json()
                    msg_type = data.get("type", "")
                    if msg_type == "ping":
                        await ws.send_json({"type": "pong"})
                    elif msg_type == "scan":
                        if self.discovery:
                            threading.Thread(target=self.discovery.scan_network, daemon=True).start()
                            await ws.send_json({"type": "scanning", "active": True})
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
