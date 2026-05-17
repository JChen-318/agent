"""Optional text-to-speech for agent responses."""

import asyncio
import logging

logger = logging.getLogger(__name__)


class TTSEngine:
    """Simple TTS wrapper using pyttsx3 (offline) or system say command."""

    def __init__(self, engine: str = "pyttsx3"):
        self.engine = engine
        self._tts = None
        if engine == "pyttsx3":
            try:
                import pyttsx3
                self._tts = pyttsx3.init()
            except ImportError:
                logger.warning("pyttsx3 not installed. Falling back to system 'say'.")
                self.engine = "system"

    async def speak(self, text: str) -> None:
        """Speak text asynchronously."""
        if not text:
            return

        loop = asyncio.get_event_loop()

        if self.engine == "pyttsx3" and self._tts:
            def _speak():
                self._tts.say(text)
                self._tts.runAndWait()
            await loop.run_in_executor(None, _speak)
        else:
            # Fallback to macOS `say` or Linux `espeak`
            import shutil
            import subprocess
            import sys

            if sys.platform == "darwin":
                cmd = ["say", text]
            elif shutil.which("espeak"):
                cmd = ["espeak", text]
            else:
                logger.warning("No TTS engine available")
                return

            proc = await asyncio.create_subprocess_exec(*cmd)
            await proc.wait()
