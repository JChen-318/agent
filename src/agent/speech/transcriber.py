"""faster-whisper integration for local speech-to-text."""

import asyncio
import logging
from typing import Optional

import numpy as np
from faster_whisper import WhisperModel

from agent.speech.vad import VoiceActivityDetector

logger = logging.getLogger(__name__)


class Transcriber:
    """Wraps faster-whisper with optional VAD for real-time speech-to-text."""

    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        use_vad: bool = True,
        sample_rate: int = 16000,
    ):
        self.sample_rate = sample_rate
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self.vad = VoiceActivityDetector(sample_rate=sample_rate) if use_vad else None
        self.tts = None

        # Supported languages for real-time transcription
        self.language = None  # None = auto-detect

    def set_language(self, lang: Optional[str]) -> None:
        """Set transcription language (e.g., 'zh', 'en'). None = auto-detect."""
        self.language = lang

    async def transcribe_file(self, audio_path: str) -> Optional[str]:
        """Transcribe an audio file."""
        loop = asyncio.get_event_loop()

        def _run():
            segments, _ = self.model.transcribe(
                audio_path, beam_size=5, language=self.language,
            )
            return " ".join(seg.text.strip() for seg in segments)

        try:
            text = await loop.run_in_executor(None, _run)
            return text if text else None
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None

    async def transcribe_array(self, audio: np.ndarray) -> Optional[str]:
        """Transcribe a numpy audio array (float32, 16kHz mono)."""
        loop = asyncio.get_event_loop()

        def _run():
            segments, _ = self.model.transcribe(
                audio, beam_size=5, language=self.language,
            )
            return " ".join(seg.text.strip() for seg in segments)

        try:
            text = await loop.run_in_executor(None, _run)
            return text if text else None
        except Exception as e:
            logger.error(f"Transcription error: {e}")
            return None

    async def listen(self, timeout: float = 10.0) -> Optional[str]:
        """Record from microphone, transcribe, return text.
        Requires sounddevice to be installed.
        """
        audio = await self._record_mic(timeout)
        if audio is None or len(audio) == 0:
            return None

        if self.vad:
            segments = self.vad.find_speech_segments(audio)
            if not segments:
                logger.debug("No speech detected")
                return None
            # Extract and transcribe speech segments
            texts = []
            for start_ms, end_ms in segments:
                start_idx = int(start_ms / 1000 * self.sample_rate)
                end_idx = int(end_ms / 1000 * self.sample_rate)
                segment_audio = audio[start_idx:end_idx]
                text = await self.transcribe_array(segment_audio)
                if text:
                    texts.append(text)
            return " ".join(texts) if texts else None

        return await self.transcribe_array(audio)

    async def _record_mic(self, timeout: float) -> Optional[np.ndarray]:
        """Record audio from microphone. Returns float32 array at sample_rate."""
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice not installed. Install with: pip install sounddevice")
            return None

        loop = asyncio.get_event_loop()

        def _record():
            duration = min(timeout, 30.0)  # cap at 30s
            audio = sd.rec(
                int(duration * self.sample_rate),
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
            )
            sd.wait()
            return audio.flatten()

        try:
            audio = await loop.run_in_executor(None, _record)
            return audio
        except Exception as e:
            logger.error(f"Recording error: {e}")
            return None
