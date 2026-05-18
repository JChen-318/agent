"""Rich terminal UI for the agent."""

import asyncio
import logging
import os
import signal
import subprocess
import sys
from typing import Optional

from agent.config.config import AppConfig, load_config
from agent.core.loop import AgentLoop
from agent.core.state import InteractionMode, SafetyLevel
from agent.llm.client import LLMClient
from agent.device.bridge import DeviceBridge
from agent.device.adb import AdbManager
from agent.speech.transcriber import Transcriber
from agent.speech.tts import TTSEngine

logger = logging.getLogger(__name__)


def _free_port(port: int) -> None:
    """Kill any process holding the given port (Windows)."""
    if sys.platform != "win32":
        return
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1] if parts else ""
                if pid.isdigit() and pid != str(os.getpid()):
                    logger.warning(
                        f"Port {port} is held by PID {pid}, killing..."
                    )
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=5,
                    )
                    return
    except Exception as e:
        logger.debug(f"Port cleanup skipped: {e}")


class AgentApp:
    """Application entry point. Wires all components together."""

    def __init__(self, config_path: Optional[str] = None):
        self.config = load_config(config_path)
        self._setup_logging()

        # Components
        self.llm: Optional[LLMClient] = None
        self.bridge: Optional[DeviceBridge] = None
        self.transcriber: Optional[Transcriber] = None
        self.tts: Optional[TTSEngine] = None
        self.loop: Optional[AgentLoop] = None

    def _setup_logging(self) -> None:
        level = getattr(logging, self.config.logging.level.upper(), logging.INFO)
        handlers = []
        # Stream handler — skip if stderr is unavailable (console=False exe)
        if sys.stderr and hasattr(sys.stderr, "write"):
            try:
                handlers.append(logging.StreamHandler(sys.stderr))
            except Exception:
                pass
        # Always log to file when running as bundled exe (no console)
        if getattr(sys, "frozen", False):
            import os as _os
            _log_dir = _os.path.dirname(sys.executable)
            _log_path = _os.path.join(_log_dir, "android-agent.log")
            try:
                handlers.append(logging.FileHandler(_log_path, encoding="utf-8"))
            except Exception:
                pass
        if not handlers:
            handlers.append(logging.NullHandler())
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
            handlers=handlers,
        )

    def _auto_detect_usb(self) -> None:
        """Auto-detect USB-connected Android device via ADB and set up port forwarding."""
        self._adb = AdbManager()
        if not self._adb.available:
            logger.debug("ADB not found, skipping USB auto-detection")
            return

        devices = self._adb.list_devices()
        if not devices:
            logger.debug("No ADB devices found")
            return

        # Pick the first USB device in 'device' state
        for d in devices:
            if d.state == "device" and d.is_usb:
                info = self._adb.get_device_info(d.serial)
                logger.info(
                    f"USB device found: {info.get('model', d.serial)} "
                    f"(Android {info.get('android_version', '?')})"
                )
                local_port = self.config.device.port
                ok = self._adb.forward_port(d.serial, local_port=local_port)
                if ok:
                    self.bridge.host = "127.0.0.1"
                    self.bridge.port = local_port
                    self.config.device.host = "127.0.0.1"
                    self.config.device.port = local_port
                    logger.info(f"ADB forwarding: 127.0.0.1:{local_port} → device:{local_port}")
                else:
                    logger.warning("ADB port forwarding failed, falling back to configured host/port")
                return

        logger.debug("No usable USB device found (state must be 'device')")

    def init(self, skip_voice: bool = False, skip_auto_usb: bool = False) -> None:
        """Initialize all components."""
        # LLM client
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
        logger.info(
            "LLM configured: base_url=%s model=%s api_key=%s",
            self.config.llm.base_url,
            self.config.llm.model,
            ("set" if self.config.llm.api_key else "NOT SET"),
        )

        # Device bridge
        self.bridge = DeviceBridge(
            host=self.config.device.host,
            port=self.config.device.port,
            reconnect_interval=self.config.device.reconnect_interval,
            max_reconnect_attempts=self.config.device.max_reconnect_attempts,
            adb_path=self.config.device.adb_path or None,
        )

        self._adb: Optional[AdbManager] = None
        if not skip_auto_usb:
            self._auto_detect_usb()

        # Whisper transcriber (optional)
        if not skip_voice:
            try:
                self.transcriber = Transcriber(
                    model_size=self.config.whisper.model_size,
                    device=self.config.whisper.device,
                    compute_type=self.config.whisper.compute_type,
                    use_vad=self.config.whisper.use_vad,
                )
                logger.info(f"Whisper transcriber loaded (model: {self.config.whisper.model_size})")
            except Exception as e:
                logger.warning(f"Whisper not available: {e}. Voice input disabled.")

        # TTS
        if not skip_voice and self.config.tts.enabled:
            try:
                self.tts = TTSEngine()
                logger.info("TTS loaded")
            except Exception as e:
                logger.warning(f"TTS not available: {e}")

        # Planner (for complex task decomposition)
        from agent.core.planner import Planner

        # Agent loop
        mode = InteractionMode(self.config.interaction.mode)
        safety = SafetyLevel(self.config.safety.confirmation_level)

        self.loop = AgentLoop(
            llm_client=self.llm,
            device_bridge=self.bridge,
            mode=mode,
            safety_level=safety,
            transcriber=self.transcriber,
            planner=Planner(self.llm),
        )

        # Register callbacks
        if self.tts:
            def _speak(text: str) -> None:
                try:
                    asyncio.get_running_loop().create_task(self.tts.speak(text))
                except RuntimeError:
                    pass  # No running event loop
            self.loop.on_speak = _speak

        self.loop.on_action = lambda action, args: print(
            f"\n  Action: {action}({args})"
        )

    async def start(self) -> None:
        """Start the agent."""
        if not self.loop:
            raise RuntimeError("Call init() first")

        print("""
╔══════════════════════════════════════════╗
║        Android Phone Control Agent       ║
║         LLM + Whisper + A11y             ║
╚══════════════════════════════════════════╝
""")
        print(f"Mode: {self.config.interaction.mode}")
        print(f"Device: {self.bridge.host if self.bridge else self.config.device.host}:{self.bridge.port if self.bridge else self.config.device.port}")
        print(f"LLM: {self.config.llm.model}")
        if self.transcriber:
            print(f"Voice: Whisper {self.config.whisper.model_size}")
        print()

        if self.loop.state.mode == InteractionMode.CONTINUOUS:
            print("Continuous mode. Type 'exit' to stop.")
        else:
            print("Single-command mode. Enter your instruction:")

        try:
            await self.loop.run()
        except KeyboardInterrupt:
            print("\nInterrupted.")
        except ConnectionError as e:
            print(f"\nFailed to connect to device: {e}")
            print("Make sure the Android APK is installed and the accessibility service is enabled.")
            print(f"Expected device at: {self.bridge.host if self.bridge else self.config.device.host}:{self.bridge.port if self.bridge else self.config.device.port}")
        finally:
            if self.loop is not None:
                await self.loop.device.disconnect()
            if self._adb:
                self._adb.remove_all_forwards()
            print("Agent stopped.")


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Android Phone Control Agent")
    parser.add_argument("--config", "-c", help="Path to config YAML file")
    parser.add_argument("--setup", action="store_true", help="Run setup wizard")
    parser.add_argument("--host", help="Android device IP address")
    parser.add_argument("--port", type=int, help="WebSocket port")
    parser.add_argument("--mode", choices=["single", "continuous"], help="Interaction mode")
    parser.add_argument("--voice", action="store_true", help="Enable voice input")
    parser.add_argument("--model", help="LLM model name")
    parser.add_argument("--command", help="Execute a single command directly (non-interactive)")
    parser.add_argument("--web", action="store_true", help="Launch web UI instead of terminal mode")
    parser.add_argument("--desktop", action="store_true", help="Launch as standalone desktop app (native window, no terminal)")
    parser.add_argument("--web-host", default="127.0.0.1", help="Web UI bind address (default: 127.0.0.1)")
    parser.add_argument("--web-port", type=int, default=8080, help="Web UI port (default: 8080)")

    args = parser.parse_args()

    if args.setup:
        from agent.ui.config import run_wizard
        run_wizard()
        return

    app = AgentApp(config_path=args.config)

    # CLI overrides
    if args.host:
        app.config.device.host = args.host
    if args.port:
        app.config.device.port = args.port
    if args.mode:
        app.config.interaction.mode = args.mode
    if args.model:
        app.config.llm.model = args.model

    skip_voice = (args.web or args.desktop) and not args.voice
    skip_auto_usb = bool(args.host or args.port)
    app.init(skip_voice=skip_voice, skip_auto_usb=skip_auto_usb)

    # When running as bundled EXE with no args, default to web mode
    web_mode = args.web or args.desktop
    if getattr(sys, "frozen", False) and not args.command and not web_mode:
        web_mode = True

    if args.desktop:
        _run_desktop(app, host=args.web_host, port=args.web_port)
    elif args.web or web_mode:
        _run_web(app, host=args.web_host, port=args.web_port)
    elif args.command:
        # Non-interactive: execute single command
        async def _run_cmd():
            await app.loop.device.connect()
            await app.loop.process_text_input(args.command)
            await app.loop.device.disconnect()
        asyncio.run(_run_cmd())
    else:
        # Interactive
        asyncio.run(app.start())


def _run_desktop(app: "AgentApp", host: str = "127.0.0.1", port: int = 8080) -> None:
    """Launch as a native desktop window (no terminal)."""
    from agent.ui.desktop import run_desktop
    run_desktop(app, host=host, port=port)


def _run_web(app: "AgentApp", host: str = "127.0.0.1", port: int = 8080) -> None:
    """Launch the web UI server."""
    import uvicorn
    from agent.ui.web_server import AgentWebServer

    _free_port(port)

    web = AgentWebServer()
    web.config = app.config
    web.llm = app.llm
    web.bridge = app.bridge
    web.loop = app.loop
    web._setup_logging()

    # Start device discovery
    web._start_discovery()

    # Wire WebSocket broadcast callbacks
    web.loop.on_action = lambda action, args: asyncio.ensure_future(
        web._broadcast({"type": "action", "action": action, "args": args})
    )
    web.loop.on_speak = lambda text: asyncio.ensure_future(
        web._broadcast({"type": "speak", "text": text})
    )

    print(f"\n  Web UI: http://{host}:{port}\n")

    # Auto-open browser when running as bundled EXE
    if getattr(sys, "frozen", False):
        import threading as _threading
        import webbrowser as _webbrowser
        def _open_browser():
            import time as _time
            _time.sleep(1.0)
            _webbrowser.open(f"http://{host}:{port}")
        _threading.Thread(target=_open_browser, daemon=True).start()

    # Use file-based logging when stderr is unavailable (console=False EXE)
    uvicorn_kwargs = {"host": host, "port": port, "log_level": "info"}
    if not sys.stderr or (hasattr(sys.stderr, "isatty") and not sys.stderr.isatty()):
        uvicorn_kwargs["log_config"] = {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    "datefmt": "%H:%M:%S",
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.FileHandler",
                    "filename": os.path.join(os.path.dirname(sys.executable), "android-agent.log"),
                    "encoding": "utf-8",
                    "formatter": "default",
                },
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO"},
                "uvicorn.error": {"handlers": ["default"], "level": "INFO"},
                "uvicorn.access": {"handlers": ["default"], "level": "INFO"},
            },
        }
    uvicorn.run(web.app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()
