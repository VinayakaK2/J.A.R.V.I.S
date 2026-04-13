"""
voice/pipeline.py
─────────────────
Full voice pipeline used by the Twilio phone-call webhooks.

Flow:
  1. Twilio records caller's voice and posts a RecordingUrl.
  2. transcribe_audio_from_url() fetches that WAV and sends it to
     OpenAI Whisper for Speech-to-Text.
  3. text_to_speech() sends JARVIS's text reply to ElevenLabs and
     returns MP3 bytes.
  4. save_audio_for_twilio() writes the MP3 to the workspace and returns
     the relative path so Twilio can fetch it as static content.

Dependencies:
  openai>=1.14.0        — Whisper STT
  elevenlabs>=1.0.0     — TTS
  httpx>=0.27.0         — Fetch Twilio recording URL
  twilio>=8.0.0         — Credentials used in wget
"""

import io
import logging
import uuid
from pathlib import Path

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

# ── Directory where synthesised audio files are stored for Twilio to fetch ───
# The workspace dir is mounted as /static by FastAPI StaticFiles middleware.
AUDIO_CACHE_DIR = Path(settings.workspace_dir).resolve() / "voice_cache"
AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─── Speech-to-Text (Whisper) ─────────────────────────────────────────────────

async def transcribe_audio_from_url(recording_url: str) -> str:
    """
    Download a Twilio recording URL and transcribe it via OpenAI Whisper.

    Twilio requires HTTP Basic Auth (account SID as user, auth token as
    password) to download recordings.  We stream the bytes directly into
    the Whisper API without writing to disk.

    Returns the transcribed text string, or a fallback error string.
    """
    if not settings.openai_api_key:
        logger.warning("[VoicePipeline] OpenAI API key not set — cannot transcribe.")
        return "I couldn't hear you clearly. Please try again."

    # Fetch the WAV file from Twilio with basic auth credentials
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                recording_url,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                follow_redirects=True,
            )
            response.raise_for_status()
            audio_bytes = response.content
    except Exception as e:
        logger.error(f"[VoicePipeline] Failed to download recording: {e}")
        return "I had trouble receiving your voice message. Please try again."

    # Send raw audio bytes to Whisper via a file-like object
    try:
        import openai

        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "recording.wav"   # Whisper needs a filename hint

        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="hi",  # Hint: supports mixed Hindi/English (Hinglish)
        )
        text = transcript.text.strip()
        logger.info(f"[VoicePipeline] Transcribed: '{text}'")
        return text or "I didn't catch that."

    except Exception as e:
        logger.error(f"[VoicePipeline] Whisper transcription failed: {e}")
        return "I had trouble understanding what you said. Please try again."


# ─── Text-to-Speech (ElevenLabs) ─────────────────────────────────────────────

async def text_to_speech(text: str) -> bytes:
    """
    Convert a text response to MP3 audio bytes via ElevenLabs.

    Uses the multilingual-v2 model with a neutral English-Hindi voice.
    Falls back to a silent placeholder if credentials are missing.
    """
    if not settings.elevenlabs_api_key:
        logger.warning("[VoicePipeline] ElevenLabs API key not set — returning silence.")
        return b""

    try:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=settings.elevenlabs_api_key)

        # Use the standard "Rachel" voice — calm, clear, works well for TTS
        # The voice_id can be overridden in .env as ELEVENLABS_VOICE_ID
        voice_id = getattr(settings, "elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM")

        # generate() returns an iterator of audio chunks — collect all bytes
        audio_chunks = client.generate(
            text=text,
            voice=voice_id,
            model="eleven_multilingual_v2",
        )
        audio_bytes = b"".join(audio_chunks)
        logger.info(f"[VoicePipeline] TTS generated {len(audio_bytes)} bytes")
        return audio_bytes

    except Exception as e:
        logger.error(f"[VoicePipeline] ElevenLabs TTS failed: {e}")
        return b""


async def save_audio_for_twilio(audio_bytes: bytes) -> str:
    """
    Write MP3 bytes to the voice_cache directory and return the relative URL
    path that Twilio can use to fetch the file via the FastAPI StaticFiles mount.

    Returns the relative path e.g. "/static/voice_cache/<uuid>.mp3"
    On failure (e.g. empty audio), returns an empty string.
    """
    if not audio_bytes:
        logger.warning("[VoicePipeline] No audio bytes — skipping file save.")
        return ""

    filename = f"{uuid.uuid4().hex}.mp3"
    file_path = AUDIO_CACHE_DIR / filename

    try:
        file_path.write_bytes(audio_bytes)
        logger.info(f"[VoicePipeline] Saved TTS audio: {file_path}")
        # The /static mount exposes the workspace directory
        return f"/static/voice_cache/{filename}"
    except Exception as e:
        logger.error(f"[VoicePipeline] Failed to save audio file: {e}")
        return ""
