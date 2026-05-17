"""mDNS device discovery — auto-finds Android Agent devices on the local network."""

import logging
import threading
import time
from typing import Callable, Optional

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_android-agent._tcp.local."


class AgentDevice:
    """Discovered Android Agent device."""

    def __init__(self, name: str, address: str, port: int, model: str = ""):
        self.name = name
        self.address = address
        self.port = port
        self.model = model
        self.last_seen = time.time()

    @property
    def display_name(self) -> str:
        model_str = f" ({self.model})" if self.model else ""
        return f"{self.name}{model_str}"

    @property
    def ws_url(self) -> str:
        return f"ws://{self.address}:{self.port}"


class DeviceDiscovery(ServiceListener):
    """Browses mDNS for Android Agent devices on the local network."""

    def __init__(self, on_change: Optional[Callable] = None):
        self._zc: Optional[Zeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._devices: dict[str, AgentDevice] = {}
        self._lock = threading.Lock()
        self._on_change = on_change
        self._running = False

    def start(self) -> None:
        """Start browsing for devices."""
        if self._running:
            return
        self._zc = Zeroconf()
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, self)
        self._running = True
        logger.info("Device discovery started (mDNS)")

    def stop(self) -> None:
        """Stop browsing."""
        self._running = False
        if self._browser:
            self._browser.cancel()
        if self._zc:
            self._zc.close()
        logger.info("Device discovery stopped")

    @property
    def devices(self) -> list[AgentDevice]:
        with self._lock:
            return sorted(self._devices.values(), key=lambda d: d.last_seen, reverse=True)

    def add_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        info = zc.get_service_info(service_type, name)
        if not info or not info.addresses:
            return
        addr = _bytes_to_ip(info.addresses[0])
        port = info.port
        # Parse server name for model info
        server_name = name.removesuffix("." + SERVICE_TYPE)
        model = ""
        if "-" in server_name:
            parts = server_name.split("-", 1)
            if len(parts) == 2:
                server_name = parts[0]
                model = parts[1]

        with self._lock:
            self._devices[addr] = AgentDevice(
                name=server_name, address=addr, port=port, model=model
            )
        logger.info(f"Discovered: {server_name} at {addr}:{port}")
        if self._on_change:
            self._on_change()

    def remove_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        addr = None
        with self._lock:
            for ip, dev in self._devices.items():
                full = f"{dev.name}.{SERVICE_TYPE}"
                if full == name or dev.name == name.removesuffix("." + SERVICE_TYPE):
                    addr = ip
                    break
            if addr:
                del self._devices[addr]
                logger.info(f"Device removed: {addr}")
                if self._on_change:
                    self._on_change()

    def update_service(self, zc: Zeroconf, service_type: str, name: str) -> None:
        self.add_service(zc, service_type, name)


def _bytes_to_ip(addr_bytes: bytes) -> str:
    return ".".join(str(b) for b in addr_bytes)
