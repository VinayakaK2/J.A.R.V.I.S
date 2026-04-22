"""
voice/tts_stream.py
────────────────────
Streaming TTS engine — splits JARVIS text into sentence chunks and synthesises
each chunk immediately so audio begins playing before the full response is ready.

Design goals:
  • First audio chunk delivered in < 500ms after text arrives
  • Sentence-boundary splitting for natural pauses between chunks
  • ElevenLabs streaming API used when available (faster than batch)
  • Graceful fallback to Twilio Polly TTS if ElevenLabs key is missing

Usage (async generator):
    async for audio_chunk in stream_text_to_speech("Hello, how can I help?"):
        websocket.send_bytes(audio_chunk)
"""

import asyncio
import logging
import re
from typing import AsyncGenerator, List

from config.settings import settings

logger = logging.getLogger(__name__)

# ── Sentence splitter regex ───────────────────────────────────────────────────
# Splits on sentence-ending punctuation followed by whitespace or end-of-string.
# Keeps each chunk ≥ 15 chars so we don't generate tiny 1-word audio clips.
_SENTENCE_RE = re.compile(r"(?<=[.!?।])\s+")

# Minimum characters before we flush a chunk to TTS — avoids tiny synthesis calls
MIN_CHUNK_LEN: int = 40

# Maximum characters per TTS chunk — keeps latency predictable
MAX_CHUNK_LEN: int = 200


def split_into_chunks(text: str) -> List[str]:
    """
    Split a response string into sentence-sized chunks for incremental TTS.

    Sentences shorter than MIN_CHUNK_LEN are merged with the next one until
    the combined length exceeds MIN_CHUNK_LEN.
    """
    # First pass: split on sentence boundaries
    raw_parts = _SENTENCE_RE.split(text.strip())

    chunks: List[str] = []
    buffer = ""

    for part in raw_parts:
        part = part.strip()
        if not part:
            continue

        # Force-split any single sentence longer than MAX_CHUNK_LEN
        while len(part) > MAX_CHUNK_LEN:
            # Split at nearest comma or space within the limit
            cutoff = part.rfind(",", 0, MAX_CHUNK_LEN)
            if cutoff == -1:
                cutoff = part.rfind(" ", 0, MAX_CHUNK_LEN)
            if cutoff == -1:
                cutoff = MAX_CHUNK_LEN
            chunks.append(part[:cutoff].strip())
            part = part[cutoff:].strip()

        buffer = (buffer + " " + part).strip() if buffer else part

        # Flush buffer when it's long enough
        if len(buffer) >= MIN_CHUNK_LEN:
            chunks.append(buffer)
            buffer = ""

    # Flush any remaining text
    if buffer:
        chunks.append(buffer)

    return chunks


async def synthesise_chunk(text: str, output_format: str = "mp3_44100_128") -> bytes:
    """
    Synthesise a single text chunk to audio bytes via ElevenLabs streaming API.

    Uses the websocket/streaming endpoint for lowest latency.
    Falls back to an empty bytes object on any failure so the caller can
    skip silently rather than crashing the call.
    """
    if not settings.elevenlabs_api_key or not text.strip():
        return b""

    try:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        voice_id = settings.elevenlabs_voice_id

        # generate() streams chunks synchronously — run in thread to avoid blocking
        def _sync_generate() -> bytes:
            audio_iter = client.generate(
                text=text,
                voice=voice_id,
                model="eleven_turbo_v2",   # Turbo model = lowest latency
                output_format=output_format,
                stream=True,
            )
            return b"".join(audio_iter)

        audio_bytes = await asyncio.get_event_loop().run_in_executor(None, _sync_generate)
        logger.debug(f"[TTS] Chunk synthesised: {len(audio_bytes)} bytes for '{text[:40]}…'")
        return audio_bytes

    except Exception as e:
        logger.error(f"[TTS] ElevenLabs chunk synthesis failed: {e}")
        return b""


async def stream_text_to_speech(text: str, output_format: str = "mp3_44100_128") -> AsyncGenerator[bytes, None]:
    """
    Async generator that streams audio chunks for a full response string.

    Splits the text into sentence chunks and yields each audio chunk as soon
    as it is synthesised — so playback can start on the first sentence while
    the rest is still being generated.

    Usage:
        async for chunk in stream_text_to_speech(reply):
            await ws.send_bytes(chunk)
    """
    chunks = split_into_chunks(text)
    if not chunks:
        logger.warning("[TTS] No chunks produced from text — nothing to stream.")
        return

    logger.info(f"[TTS] Streaming {len(chunks)} chunks for response ({len(text)} chars)")

    for i, chunk in enumerate(chunks):
        audio = await synthesise_chunk(chunk, output_format=output_format)
        if audio:
            logger.debug(f"[TTS] Yielding chunk {i + 1}/{len(chunks)}")
            yield audio
        # Small yield point so interrupt signals can be checked between chunks
        await asyncio.sleep(0)


async def full_text_to_speech(text: str, output_format: str = "mp3_44100_128") -> bytes:
    """
    Convenience wrapper that collects all streaming chunks into a single bytes object.
    Used by the batch (/voice/process) endpoint as a faster alternative to pipeline.py.
    """
    parts: List[bytes] = []
    async for chunk in stream_text_to_speech(text, output_format=output_format):
        parts.append(chunk)
    return b"".join(parts)
