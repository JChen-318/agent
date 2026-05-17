"""ADB (Android Debug Bridge) wrapper for USB device connection."""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Common ADB locations
_ADB_PATHS = [
    "adb",
    "adb.exe",
]

# SDK default paths
if sys.platform == "win32":
    _ADB_PATHS.extend([
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"),
        str(Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools" / "adb.exe"),
        str(Path(os.environ.get("ANDROID_SDK_ROOT", "")) / "platform-tools" / "adb.exe"),
        "C:\\platform-tools\\adb.exe",
        "C:\\adb\\adb.exe",
        # Common download/extract locations
        str(Path.home() / "Downloads" / "platform-tools-latest-windows" / "platform-tools" / "adb.exe"),
        str(Path.home() / "Downloads" / "platform-tools" / "adb.exe"),
    ])
else:
    _ADB_PATHS.extend([
        str(Path(os.environ.get("HOME", "")) / "Android" / "Sdk" / "platform-tools" / "adb"),
        str(Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools" / "adb"),
        "/usr/bin/adb",
        "/usr/local/bin/adb",
    ])

DEFAULT_REMOTE_PORT = 18765
DEFAULT_LOCAL_PORT = 18765


class AdbDevice:
    """A device connected via ADB (USB or TCP)."""

    def __init__(self, serial: str, state: str = "device", model: str = "", android_version: str = ""):
        self.serial = serial
        self.state = state
        self.model = model
        self.android_version = android_version

    @property
    def is_usb(self) -> bool:
        return not (":" in self.serial and "." in self.serial)

    @property
    def display_name(self) -> str:
        parts = []
        if self.model:
            parts.append(self.model)
        else:
            parts.append("Android Device")
        if self.android_version:
            parts.append(f"(Android {self.android_version})")
        if not self.is_usb:
            parts.append("[TCP]")
        return " ".join(parts)


class AdbManager:
    """Manages ADB device listing and port forwarding."""

    def __init__(self):
        self._adb_path: Optional[str] = None
        self._forwarded: dict[str, int] = {}  # serial -> local_port

    @property
    def available(self) -> bool:
        """Check if ADB is available."""
        return self._get_adb_path() is not None

    def _get_adb_path(self) -> Optional[str]:
        """Find the adb executable."""
        if self._adb_path:
            return self._adb_path

        for path in _ADB_PATHS:
            p = Path(path)
            if not p.is_absolute():
                # Check PATH
                import shutil
                found = shutil.which(path)
                if found:
                    self._adb_path = found
                    return found
            elif p.exists() and p.is_file():
                self._adb_path = str(p)
                return str(p)

        return None

    def _run(self, *args: str, timeout: float = 10.0) -> tuple[int, str, str]:
        """Run an ADB command, return (returncode, stdout, stderr)."""
        adb = self._get_adb_path()
        if not adb:
            return -1, "", "ADB not found"
        try:
            p = subprocess.run([adb] + list(args), capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Timeout"
        except Exception as e:
            return -1, "", str(e)

    def list_devices(self) -> list[AdbDevice]:
        """List connected devices via `adb devices`."""
        code, stdout, stderr = self._run("devices", "-l")
        if code != 0:
            logger.warning(f"ADB devices failed: {stderr}")
            return []

        devices = []
        for line in stdout.strip().split("\n")[1:]:  # Skip header
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            if state not in ("device", "unauthorized", "offline"):
                continue

            model = ""
            for part in parts[2:]:
                if part.startswith("model:"):
                    model = part.split(":", 1)[1]

            devices.append(AdbDevice(serial=serial, state=state, model=model))

        return devices

    def get_device_info(self, serial: str) -> dict:
        """Get device model and Android version."""
        info = {}
        code, stdout, _ = self._run("-s", serial, "shell", "getprop", "ro.product.model")
        if code == 0:
            info["model"] = stdout.strip()
        code, stdout, _ = self._run("-s", serial, "shell", "getprop", "ro.build.version.release")
        if code == 0:
            info["android_version"] = stdout.strip()
        return info

    def forward_port(self, serial: str, local_port: int = DEFAULT_LOCAL_PORT,
                     remote_port: int = DEFAULT_REMOTE_PORT) -> bool:
        """
        Forward local port to device port via ADB.
        After this, connecting to 127.0.0.1:<local_port> reaches the device's WebSocket server.
        """
        code, stdout, stderr = self._run("-s", serial, "forward",
                                          f"tcp:{local_port}", f"tcp:{remote_port}")
        if code == 0:
            self._forwarded[serial] = local_port
            logger.info(f"ADB forward: 127.0.0.1:{local_port} -> {serial}:{remote_port}")
            return True
        logger.error(f"ADB forward failed: {stderr}")
        return False

    def remove_forward(self, serial: str, local_port: Optional[int] = None) -> None:
        """Remove a port forward."""
        port = local_port or self._forwarded.get(serial)
        if port:
            if serial:
                self._run("-s", serial, "forward", "--remove", f"tcp:{port}")
            else:
                self._run("forward", "--remove", f"tcp:{port}")
            self._forwarded.pop(serial, None)
            logger.info(f"Removed ADB forward for port {port}")

    def remove_all_forwards(self) -> None:
        """Remove all port forwards."""
        self._run("forward", "--remove-all")
        self._forwarded.clear()

    def connect_tcpip(self, host: str, port: int = 5555) -> bool:
        """
        Connect to a device over WiFi via ADB TCP/IP.
        The device must have ADB over TCP/IP enabled first (adb tcpip 5555 via USB).
        """
        code, stdout, stderr = self._run("connect", f"{host}:{port}", timeout=10.0)
        if code == 0:
            logger.info(f"ADB connected to {host}:{port}")
            return True
        logger.warning(f"ADB connect to {host}:{port} failed: {stderr.strip()}")
        return False

    def enable_tcpip(self, serial: str, port: int = 5555) -> bool:
        """
        Enable ADB over TCP/IP on a USB-connected device.
        After this, you can unplug USB and use `adb connect <ip>:<port>`.
        """
        code, stdout, stderr = self._run("-s", serial, "tcpip", str(port), timeout=10.0)
        if code == 0:
            logger.info(f"ADB TCP/IP enabled on {serial} (port {port})")
            return True
        logger.warning(f"ADB tcpip failed: {stderr.strip()}")
        return False

    def disconnect_tcpip(self, host: str, port: int = 5555) -> bool:
        """Disconnect an ADB TCP/IP connection."""
        code, stdout, stderr = self._run("disconnect", f"{host}:{port}", timeout=10.0)
        return code == 0

    def get_device_ip(self, serial: str) -> str:
        """Get the WiFi IP address of a device."""
        code, stdout, _ = self._run(
            "-s", serial, "shell", "ip", "route", "get", "8.8.8.8",
            timeout=5.0,
        )
        if code == 0 and stdout:
            for word in stdout.split():
                if "src" in word or "." in word:
                    parts = word.split("src")[-1]
                    if "." in parts and not parts.startswith("8.8"):
                        return parts.strip()
        # Fallback: try wlan0
        code, stdout, _ = self._run(
            "-s", serial, "shell", "ip", "addr", "show", "wlan0",
            timeout=5.0,
        )
        if code == 0 and stdout:
            import re
            match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", stdout)
            if match:
                return match.group(1)
        return ""
