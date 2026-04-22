"""
voice/wake_word.py
──────────────────
Wake word detector using pvporcupine.
Processes audio frames provided by the main listener loop.
"""

import struct
import logging
import pvporcupine

logger = logging.getLogger(__name__)

class WakeWordDetector:
    def __init__(self, access_key: str):
        self.access_key = access_key
        try:
            self.porcupine = pvporcupine.create(
                access_key=self.access_key,
                keywords=['jarvis']
            )
        except pvporcupine.PorcupineError as e:
            logger.error(f"[WakeWord] Failed to initialize Porcupine: {e}")
            raise

    @property
    def frame_length(self) -> int:
        return self.porcupine.frame_length

    @property
    def sample_rate(self) -> int:
        return self.porcupine.sample_rate

    def process_frame(self, pcm_data: bytes) -> bool:
        """
        Processes a PCM frame (16-bit, 1 channel).
        Returns True if the wake word is detected.
        """
        audio_frame = struct.unpack_from("h" * self.porcupine.frame_length, pcm_data)
        keyword_index = self.porcupine.process(audio_frame)
        return keyword_index >= 0

    def cleanup(self):
        if self.porcupine is not None:
            self.porcupine.delete()
            self.porcupine = None
