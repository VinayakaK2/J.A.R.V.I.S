"""
voice/realtime.py
──────────────────
Real-time voice pipeline for JARVIS — built on Twilio Media Streams (WebSocket).

Architecture:
  ┌────────────┐  µ-law audio   ┌──────────────┐  text   ┌───────────┐
  │  Twilio    │ ─────────────► │ STT (Whisper)│ ───────►│ JARVIS   │
  │  Call      │               └──────────────┘         │Orchestrat.│
  │  (WS)      │  MP3  chunks   ┌──────────────┐  text   └───────────┘
  │            │ ◄───────────── │ TTS (ElevenL)│ ◄───────────────────
  └────────────┘               └──────────────┘

Key features implemented:
  1. Bi-directional Twilio Media Streams WebSocket handler
  2. VAD (Voice Activity Detection) via energy threshold — silence detection
  3. Interrupt handling — stops TTS immediately when new speech is detected
  4. Rolling 3-5 turn voice conversation memory (in-memory per call)
  5. Async parallel pipeline: STT runs → JARVIS runs → TTS streams concurrently
  6. Target first-response latency: < 1.5 seconds from end of speech

Twilio Media Streams protocol:
  • Twilio sends JSON "media" events with base64-encoded µ-law (MULAW) 8kHz audio
  • Twilio expects JSON "media" events back with base64 MP3 (or raw audio) chunks
  • Control events: "start", "stop", "mark", "dtmf"

Dependencies:
  openai>=1.14.0    — Whisper STT
  elevenlabs>=1.0.0 — TTS (via tts_stream.py)
  audioop           — stdlib µ-law → PCM conversion
"""

import asyncio
import audioop
import base64
import io
import json
import logging
import time
from collections import deque
from typing import AsyncGenerator, Deque, Dict, List, Optional

from config.settings import settings
from auth.identity import get_user_info
from memory.session_store import get_or_create_session
from voice.tts_stream import stream_text_to_speech

logger = logging.getLogger(__name__)

# ── Audio constants ────────────────────────────────────────────────────────────
# Twilio Media Streams delivers µ-law 8kHz mono audio
MULAW_RATE: int = 8000
MULAW_SAMPLE_WIDTH: int = 1   # 8-bit µ-law

# ── VAD (Voice Activity Detection) parameters ─────────────────────────────────
# Energy threshold above which audio is classified as speech (tune to taste)
VAD_ENERGY_THRESHOLD: int = 300
# Consecutive silent frames needed to trigger end-of-utterance (each frame = 20ms)
VAD_SILENCE_FRAMES: int = 40    # 40 × 20ms = 800ms silence → end of speech
# Minimum utterance length in frames before we bother sending to Whisper
VAD_MIN_SPEECH_FRAMES: int = 10  # 200ms minimum

# ── Conversation memory limit ──────────────────────────────────────────────────
# Keep last N voice turns in-memory for context; avoids N+1 DB reads per chunk
VOICE_MEMORY_TURNS: int = 5


# ─── Per-call session state ────────────────────────────────────────────────────

class CallSession:
    """
    Holds all mutable state for a single active Twilio voice call.

    Lifecycle: created when the WebSocket connects, destroyed on disconnect.
    """

    def __init__(self, stream_sid: str, caller: str):
        self.stream_sid      = stream_sid           # Twilio MediaStream SID
        self.caller          = caller               # Caller phone number (E.164)
        self.role            = get_user_info(caller)["role"]  # "owner"|"guest"
        self.session_id      = get_or_create_session(caller, channel="voice")

        # Sliding window of recent voice turns for context injection
        self.voice_history: Deque[Dict[str, str]] = deque(maxlen=VOICE_MEMORY_TURNS)

        # Raw PCM audio buffer accumulating the current utterance
        self.audio_buffer: bytes = b""

        # VAD state
        self.silent_frames: int = 0
        self.speaking: bool = False
        self.speech_frames: int = 0

        # Interrupt control — set to True when new speech detected during TTS
        self.interrupt_flag: asyncio.Event = asyncio.Event()

        # Lock so only one TTS stream runs at a time
        self.tts_lock: asyncio.Lock = asyncio.Lock()

        # Timestamp of last activity (for idle timeout detection)
        self.last_activity: float = time.monotonic()

        logger.info(
            f"[CallSession] Created sid={stream_sid} caller={caller} role={self.role}"
        )

    def add_voice_turn(self, role: str, content: str):
        """Append a turn to the rolling in-memory voice history."""
        self.voice_history.append({"role": role, "content": content})

    def get_context_string(self) -> str:
        """Format the last N voice turns as a compact context string for the planner."""
        return "\n".join(
            f"{t['role'].title()}: {t['content']}" for t in self.voice_history
        )


# ─── µ-law → PCM conversion ───────────────────────────────────────────────────

def mulaw_to_pcm(mulaw_bytes: bytes) -> bytes:
    """Convert Twilio's µ-law 8kHz audio to 16-bit PCM — required by Whisper."""
    return audioop.ulaw2lin(mulaw_bytes, 2)   # 2 = output sample width (16-bit)


def compute_energy(pcm_bytes: bytes) -> int:
    """
    Compute RMS energy of a PCM frame for VAD.
    Returns 0 for empty input to avoid math errors.
    """
    if not pcm_bytes:
        return 0
    try:
        return audioop.rms(pcm_bytes, 2)   # 2 = 16-bit samples
    except Exception:
        return 0


# ─── Whisper STT (streaming accumulate → transcribe) ─────────────────────────

async def transcribe_pcm(pcm_bytes: bytes, sample_rate: int = MULAW_RATE) -> str:
    """
    Transcribe accumulated PCM audio bytes via OpenAI Whisper.

    Converts 16-bit PCM to WAV in-memory, then sends to Whisper.
    Returns empty string on failure so the caller can skip silently.
    """
    if not settings.openai_api_key or len(pcm_bytes) < (sample_rate // 5):
        # Need at least ~200ms of audio
        return ""

    try:
        import openai
        import wave

        # Wrap raw PCM in a WAV container — Whisper requires a valid audio format
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)      # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        wav_buffer.seek(0)
        wav_buffer.name = "utterance.wav"

        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=wav_buffer,
            language="hi",          # Hint: handles Hinglish naturally
            response_format="text",
        )
        text = str(transcript).strip()
        logger.info(f"[STT] Whisper transcribed: '{text}'")
        return text

    except Exception as e:
        logger.error(f"[STT] Whisper failed: {e}")
        return ""


# ─── JARVIS response generation ────────────────────────────────────────────────

async def get_jarvis_response(session: CallSession, user_text: str) -> str:
    """
    Route the transcribed user text through the JARVIS orchestrator.

    Uses a compact voice-context prompt so JARVIS stays conversational.
    Runs the (synchronous) orchestrator in a thread pool to avoid blocking
    the WebSocket event loop.
    """
    from orchestrator import OrchestratorAgent

    # Lazy singleton — share across calls to avoid re-loading LLM clients
    if not hasattr(get_jarvis_response, "_orchestrator"):
        get_jarvis_response._orchestrator = OrchestratorAgent()
    orchestrator: OrchestratorAgent = get_jarvis_response._orchestrator

    # Store user turn in rolling voice history
    session.add_voice_turn("user", user_text)

    # Inject voice conversation context into the session so the planner has
    # access to the last few turns without loading from SQLite every time
    ctx_hint = session.get_context_string()

    def _run_sync() -> str:
        return orchestrator.process_request(
            session_id=session.session_id,
            user_input=user_text,
            tone="professional",
            channel="voice",
            role=session.role,
            context_override=ctx_hint
        )

    # Run synchronous orchestrator in thread pool — keeps WS loop responsive
    loop = asyncio.get_event_loop()
    reply = await loop.run_in_executor(None, _run_sync)
    reply = reply or "Mujhe samajh nahi aaya. Please repeat."

    # Store assistant turn in rolling voice history
    session.add_voice_turn("assistant", reply)
    return reply


# ─── TTS → Twilio streaming ────────────────────────────────────────────────────

async def stream_response_to_twilio(
    session: CallSession,
    reply_text: str,
    websocket,
    stream_sid: str,
) -> None:
    """
    Synthesise reply_text in streaming sentence chunks and send each MP3 chunk
    back to Twilio via the Media Streams WebSocket.

    Checks session.interrupt_flag between every chunk — if the user starts
    speaking, we abort mid-stream immediately.

    Twilio expects:
    {
        "event": "media",
        "streamSid": "<SID>",
        "media": { "payload": "<base64 MP3>" }
    }
    """
    async with session.tts_lock:
        # Clear any previous interrupt before we start streaming
        session.interrupt_flag.clear()

        logger.info(
            f"[TTS→WS] Streaming response for {stream_sid}: '{reply_text[:60]}…'"
        )

        chunk_count = 0
        async for audio_chunk in stream_text_to_speech(reply_text):
            # ── Interrupt check ───────────────────────────────────────────────
            if session.interrupt_flag.is_set():
                logger.info(f"[TTS→WS] Interrupted after {chunk_count} chunks.")
                break

            if not audio_chunk:
                continue

            # Encode MP3 bytes to base64 for the Twilio media event
            payload = base64.b64encode(audio_chunk).decode("utf-8")
            message = json.dumps({
                "event":     "media",
                "streamSid": stream_sid,
                "media":     {"payload": payload},
            })

            try:
                await websocket.send_text(message)
                chunk_count += 1
                logger.debug(f"[TTS→WS] Sent chunk {chunk_count} ({len(audio_chunk)} bytes)")
            except Exception as e:
                logger.error(f"[TTS→WS] WebSocket send failed: {e}")
                break

        # Send "mark" event so Twilio knows TTS is done
        try:
            mark_msg = json.dumps({
                "event":     "mark",
                "streamSid": stream_sid,
                "mark":      {"name": "tts_complete"},
            })
            await websocket.send_text(mark_msg)
        except Exception:
            pass

        logger.info(f"[TTS→WS] Finished streaming {chunk_count} chunks.")


# ─── VAD processor — accumulate audio and detect end-of-utterance ─────────────

class VADProcessor:
    """
    Processes incoming µ-law audio frames, detects speech/silence boundaries,
    and emits complete utterances for STT when the user stops speaking.

    Frame size: Twilio sends ~20ms frames (160 samples @ 8kHz).
    """

    def __init__(self, session: CallSession):
        self.session = session

    def process_frame(self, mulaw_frame: bytes) -> Optional[bytes]:
        """
        Feed a single µ-law audio frame into the VAD state machine.

        Returns:
            bytes — accumulated PCM utterance when end-of-speech is detected
            None  — still accumulating (no complete utterance yet)
        """
        pcm_frame = mulaw_to_pcm(mulaw_frame)
        energy = compute_energy(pcm_frame)

        if energy > VAD_ENERGY_THRESHOLD:
            # ── Speech detected ────────────────────────────────────────────
            if not self.session.speaking:
                logger.debug("[VAD] Speech started")
                self.session.speaking = True

            # Signal interrupt if TTS is currently running
            if self.session.tts_lock.locked():
                logger.info("[VAD] User speaking during TTS — triggering interrupt.")
                self.session.interrupt_flag.set()

            self.session.audio_buffer += pcm_frame
            self.session.speech_frames += 1
            self.session.silent_frames = 0

        else:
            # ── Silence detected ───────────────────────────────────────────
            if self.session.speaking:
                self.session.audio_buffer += pcm_frame
                self.session.silent_frames += 1

                if self.session.silent_frames >= VAD_SILENCE_FRAMES:
                    # End of utterance — flush buffer
                    if self.session.speech_frames >= VAD_MIN_SPEECH_FRAMES:
                        utterance_pcm = self.session.audio_buffer
                        logger.info(
                            f"[VAD] End of utterance: "
                            f"{self.session.speech_frames} speech frames, "
                            f"{len(utterance_pcm)} PCM bytes"
                        )
                    else:
                        logger.debug("[VAD] Utterance too short — discarding.")
                        utterance_pcm = b""

                    # Reset state for next utterance
                    self.session.audio_buffer = b""
                    self.session.speaking = False
                    self.session.speech_frames = 0
                    self.session.silent_frames = 0

                    return utterance_pcm if utterance_pcm else None

        return None


# ─── Main WebSocket handler ────────────────────────────────────────────────────

async def handle_media_stream(websocket) -> None:
    """
    Main coroutine that handles a single Twilio Media Streams WebSocket connection.

    Registered in main.py as the /ws/voice WebSocket route.

    Protocol:
      1. Twilio sends {"event": "start", "start": {"streamSid": …, "from": …}}
      2. Twilio sends {"event": "media", "media": {"payload": <base64 mulaw>}}
      3. We send {"event": "media", "streamSid": …, "media": {"payload": <base64 mp3>}}
      4. Twilio sends {"event": "stop"} when call ends
    """
    session: Optional[CallSession] = None
    vad: Optional[VADProcessor] = None
    stream_sid: str = ""

    logger.info("[MediaStream] WebSocket connection opened.")

    try:
        async for raw_message in websocket.iter_text():
            msg: dict = json.loads(raw_message)
            event: str = msg.get("event", "")

            # ── START: initialise session ──────────────────────────────────
            if event == "start":
                start_data = msg.get("start", {})
                stream_sid = start_data.get("streamSid", "unknown")
                # Caller phone from Twilio custom params or call metadata
                caller = (
                    start_data.get("customParameters", {}).get("caller")
                    or start_data.get("from", "unknown")
                )
                session = CallSession(stream_sid=stream_sid, caller=caller)
                vad = VADProcessor(session)
                logger.info(f"[MediaStream] Stream started: sid={stream_sid} caller={caller}")

                # Send a short greeting immediately so the caller isn't in silence
                greeting = "Namaste! Main JARVIS hoon. Boliye."
                asyncio.create_task(
                    stream_response_to_twilio(session, greeting, websocket, stream_sid)
                )

            # ── MEDIA: process incoming audio frame ────────────────────────
            elif event == "media" and session and vad:
                session.last_activity = time.monotonic()
                payload_b64: str = msg.get("media", {}).get("payload", "")
                if not payload_b64:
                    continue

                mulaw_bytes = base64.b64decode(payload_b64)
                utterance_pcm = vad.process_frame(mulaw_bytes)

                if utterance_pcm:
                    # Complete utterance detected — process asynchronously
                    asyncio.create_task(
                        _handle_utterance(session, utterance_pcm, websocket, stream_sid)
                    )

            # ── MARK: TTS playback confirmed ───────────────────────────────
            elif event == "mark":
                mark_name = msg.get("mark", {}).get("name", "")
                logger.debug(f"[MediaStream] Mark received: {mark_name}")

            # ── STOP: call ended ───────────────────────────────────────────
            elif event == "stop":
                logger.info(f"[MediaStream] Stream stopped: sid={stream_sid}")
                break

    except Exception as e:
        logger.exception(f"[MediaStream] WebSocket error: {e}")
    finally:
        logger.info(f"[MediaStream] WebSocket connection closed for sid={stream_sid}")


async def _handle_utterance(
    session: CallSession,
    utterance_pcm: bytes,
    websocket,
    stream_sid: str,
) -> None:
    """
    Full pipeline for a single detected utterance:
    PCM → Whisper STT → JARVIS → TTS stream → WebSocket

    Runs in an asyncio Task so multiple overlapping utterances are queued
    without blocking the main receive loop.
    """
    t0 = time.monotonic()

    # ── Step 1: STT ────────────────────────────────────────────────────────
    user_text = await transcribe_pcm(utterance_pcm)
    if not user_text:
        logger.info("[Pipeline] Empty transcription — skipping.")
        return

    t_stt = time.monotonic()
    logger.info(f"[Pipeline] STT done in {t_stt - t0:.2f}s: '{user_text}'")

    # ── Step 2: JARVIS ─────────────────────────────────────────────────────
    reply_text = await get_jarvis_response(session, user_text)
    t_llm = time.monotonic()
    logger.info(f"[Pipeline] JARVIS done in {t_llm - t_stt:.2f}s: '{reply_text[:60]}…'")

    # ── Step 3: TTS → WebSocket stream ─────────────────────────────────────
    await stream_response_to_twilio(session, reply_text, websocket, stream_sid)
    t_end = time.monotonic()

    logger.info(
        f"[Pipeline] Total latency: {t_end - t0:.2f}s "
        f"(STT={t_stt-t0:.2f}s, LLM={t_llm-t_stt:.2f}s, "
        f"TTS_first_chunk=streaming)"
    )
