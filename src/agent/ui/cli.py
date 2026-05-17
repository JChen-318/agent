"""Rich terminal UI for the agent."""

import asyncio
import logging
import signal
import sys
from typing import Optional

from agent.config.config import AppConfig, load_config
from agent.core.loop import AgentLoop
from agent.core.state import InteractionMode, SafetyLevel
from agent.llm.client import LLMClient
from agent.device.bridge import DeviceBridge
from agent.speech.transcriber import Transcriber
from agent.speech.tts import TTSEngine

logger = logging.getLogger(__name__)


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
        logging.basicConfig(
            level=level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    def init(self) -> None:
        """Initialize all components."""
        # LLM client
        self.llm = LLMClient(
            base_url=self.config.llm.base_url,
            api_key=self.config.llm.api_key,
            model=self.config.llm.model,
            max_tokens=self.config.llm.max_tokens,
            temperature=self.config.llm.temperature,
        )

        # Device bridge
        self.bridge = DeviceBridge(
            host=self.config.device.host,
            port=self.config.device.port,
            reconnect_interval=self.config.device.reconnect_interval,
            max_reconnect_attempts=self.config.device.max_reconnect_attempts,
        )

        # Whisper transcriber (optional)
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
        if self.config.tts.enabled:
            try:
                self.tts = TTSEngine()
                logger.info("TTS loaded")
            except Exception as e:
                logger.warning(f"TTS not available: {e}")

        # Agent loop
        mode = InteractionMode(self.config.interaction.mode)
        safety = SafetyLevel(self.config.safety.confirmation_level)

        self.loop = AgentLoop(
            llm_client=self.llm,
            device_bridge=self.bridge,
            mode=mode,
            safety_level=safety,
        )

        # Register callbacks
        if self.tts:
            self.loop.on_speak = lambda text: asyncio.ensure_future(
                self.tts.speak(text)
            )

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
        print(f"Device: {self.config.device.host}:{self.config.device.port}")
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
            print(f"Expected device at: {self.config.device.host}:{self.config.device.port}")
        finally:
            await self.loop.device.disconnect()
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

    app.init()

    if args.command:
        # Non-interactive: execute single command
        async def _run_cmd():
            await app.loop.device.connect()
            await app.loop.process_text_input(args.command)
            await app.loop.device.disconnect()
        asyncio.run(_run_cmd())
    else:
        # Interactive
        asyncio.run(app.start())


if __name__ == "__main__":
    main()
