"""Voice Activity Detection — determines when user starts/stops speaking."""

import logging
import time

import numpy as np

logger = logging.getLogger(__name__)


class VoiceActivityDetector:
    """Simple energy-based VAD with silence threshold."""

    def __init__(
        self,
        sample_rate: int = 16000,
        silence_threshold_ms: int = 1500,
        energy_threshold: float = 0.01,
        chunk_duration_ms: int = 30,
    ):
        self.sample_rate = sample_rate
        self.silence_threshold_ms = silence_threshold_ms
        self.energy_threshold = energy_threshold
        self.chunk_size = sample_rate * chunk_duration_ms // 1000

    def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if an audio chunk contains speech based on energy."""
        if len(audio_chunk) == 0:
            return False
        energy = np.sqrt(np.mean(audio_chunk.astype(np.float32) ** 2))
        return energy > self.energy_threshold

    def find_speech_segments(
        self, audio: np.ndarray, min_speech_ms: int = 300
    ) -> list[tuple[int, int]]:
        """Find speech segments in full audio. Returns list of (start_ms, end_ms)."""
        segments = []
        in_speech = False
        speech_start = 0
        silence_start = 0

        for i in range(0, len(audio) - self.chunk_size + 1, self.chunk_size):
            chunk = audio[i:i + self.chunk_size]
            has_speech = self.is_speech(chunk)
            time_ms = int(i / self.sample_rate * 1000)

            if has_speech and not in_speech:
                in_speech = True
                speech_start = time_ms
                silence_start = 0
            elif not has_speech and in_speech:
                if silence_start == 0:
                    silence_start = time_ms
                elif time_ms - silence_start >= self.silence_threshold_ms:
                    in_speech = False
                    if time_ms - speech_start >= min_speech_ms:
                        segments.append((speech_start, silence_start))
                    silence_start = 0
            elif has_speech:
                silence_start = 0

        if in_speech:
            end_ms = int(len(audio) / self.sample_rate * 1000)
            if end_ms - speech_start >= min_speech_ms:
                segments.append((speech_start, end_ms))

        return segments
