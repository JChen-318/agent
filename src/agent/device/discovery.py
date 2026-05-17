"""Device discovery — mDNS + TCP scan for Android Agent devices on the local network."""

import logging
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_android-agent._tcp.local."
DEFAULT_PORT = 8765
TCP_SCAN_TIMEOUT = 0.5  # seconds per host
TCP_SCAN_WORKERS = 40


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


class DeviceDiscovery(ServiceListener):
    """Browses for Android Agent devices via mDNS and TCP scan."""

    def __init__(self, on_change: Optional[Callable] = None):
        self._zc: Optional[Zeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._devices: dict[str, AgentDevice] = {}
        self._lock = threading.Lock()
        self._on_change = on_change
        self._running = False
        self._scanning = False

    def start(self) -> None:
        """Start mDNS browsing."""
        if self._running:
            return
        self._zc = Zeroconf()
        self._browser = ServiceBrowser(self._zc, SERVICE_TYPE, self)
        self._running = True
        logger.info("Device discovery started (mDNS)")
        # Run an initial TCP scan after a short delay
        threading.Thread(target=self._delayed_scan, daemon=True).start()

    def stop(self) -> None:
        """Stop discovery."""
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

    @property
    def is_scanning(self) -> bool:
        return self._scanning

    def _delayed_scan(self) -> None:
        """Run TCP scan after mDNS has had a moment to respond."""
        time.sleep(3)
        self.scan_network()

    def scan_network(self) -> list[dict]:
        """
        TCP port scan the local subnet for devices on port 8765.
        Returns list of discovered device dicts.
        """
        local_ip = _get_local_ip()
        if not local_ip:
            logger.warning("Cannot determine local IP for TCP scan")
            return []

        # Scan the /24 subnet
        parts = local_ip.split(".")
        base = ".".join(parts[:3])
        targets = [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != local_ip]

        self._scanning = True
        found = []

        with ThreadPoolExecutor(max_workers=TCP_SCAN_WORKERS) as pool:
            futures = {pool.submit(_probe_host, ip, DEFAULT_PORT): ip for ip in targets}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    found.append(result)
                    with self._lock:
                        self._devices[result["address"]] = AgentDevice(
                            name=result.get("name", "Android Device"),
                            address=result["address"],
                            port=DEFAULT_PORT,
                            model=result.get("model", ""),
                        )
                    if self._on_change:
                        self._on_change()

        self._scanning = False
        logger.info(f"TCP scan found {len(found)} device(s)")
        return found

    # ── mDNS callbacks ──────────────────────
    def add_service(self, zc, service_type: str, name: str) -> None:
        info = zc.get_service_info(service_type, name)
        if not info or not info.addresses:
            return
        addr = _bytes_to_ip(info.addresses[0])
        port = info.port or DEFAULT_PORT
        server_name = name.removesuffix("." + SERVICE_TYPE)
        model = ""
        if "-" in server_name:
            parts = server_name.split("-", 1)
            if len(parts) == 2:
                server_name, model = parts[0], parts[1]

        with self._lock:
            self._devices[addr] = AgentDevice(name=server_name, address=addr, port=port, model=model)
        logger.info(f"mDNS found: {server_name} at {addr}:{port}")
        if self._on_change:
            self._on_change()

    def remove_service(self, zc, service_type: str, name: str) -> None:
        with self._lock:
            for addr, dev in list(self._devices.items()):
                full = f"{dev.name}.{SERVICE_TYPE}"
                if full == name or dev.name == name.removesuffix("." + SERVICE_TYPE):
                    del self._devices[addr]
                    if self._on_change:
                        self._on_change()
                    break

    def update_service(self, zc, service_type: str, name: str) -> None:
        self.add_service(zc, service_type, name)


def _get_local_ip() -> Optional[str]:
    """Get the primary local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _probe_host(ip: str, port: int) -> Optional[dict]:
    """Try to connect to an agent WebSocket on the given host:port."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TCP_SCAN_TIMEOUT)
        result = sock.connect_ex((ip, port))
        sock.close()
        if result == 0:
            return {"address": ip, "port": port, "name": f"Device-{ip.split('.')[-1]}"}
    except Exception:
        pass
    return None


def _bytes_to_ip(addr_bytes: bytes) -> str:
    return ".".join(str(b) for b in addr_bytes)
