"""
voice/listener.py
──────────────────
Continuous Voice Listener State Machine for JARVIS.
Handles local microphone input, wake word detection, intent sensing,
and coordinates with the core JARVIS orchestrator.

Interruption pipeline:
  Stage 1 (<200ms): VAD confidence triggers instant TTS pause + hardware flush.
  Stage 2A (Vosk):  Restricted-grammar keyword spotter (~400-700ms adaptive window).
  Stage 2B (Whisper): Fallback for ambiguous / noisy inputs.
  Resume:           Silence-stable gate (250-300ms continuous silence before un-pause).
"""

import time
import logging
import struct
import pyaudio
import audioop
import asyncio
import collections
import re
import vosk
import json
import os
from enum import Enum, auto
from typing import Optional, Dict, Any

from .wake_word import WakeWordDetector
from .realtime import transcribe_pcm, compute_energy, VAD_ENERGY_THRESHOLD
from orchestrator import OrchestratorAgent
from config.settings import settings
from voice.tts_stream import stream_text_to_speech
from memory.session_store import get_or_create_session
from memory.manager import MemoryAgent

# ── Interrupt keyword allow-list (shared between grammar + validation) ─────────
# Only these exact words/phrases will be considered valid interruptions.
INTERRUPT_KEYWORDS = [
    "stop", "wait", "no", "cancel", "hold on",
    "pause", "actually", "never mind", "nevermind",
    "one sec", "no no", "wait wait", "stop it",
]

# Vosk restricted grammar JSON — forces the decoder to only emit these tokens,
# which makes recognition faster and far more accurate for short commands.
VOSK_GRAMMAR = json.dumps(INTERRUPT_KEYWORDS + [""])

# Minimum consecutive silent frames (at ~32ms each) before a false-alarm
# resume is allowed. 8 frames ≈ 256ms of confirmed silence.
RESUME_SILENCE_FRAMES = 8

# Adaptive validation window bounds (seconds)
VALIDATION_WINDOW_MIN = 0.35   # start checking after 350ms
VALIDATION_WINDOW_MAX = 0.70   # hard cap even if speech continues
VALIDATION_WINDOW_STEP = 0.05  # extend by 50ms per ongoing-speech check

def compute_vad_confidence(pcm_bytes: bytes, baseline_energy: float) -> float:
    """Computes a pseudo-probability of speech using energy and Zero-Crossing Rate."""
    if not pcm_bytes:
        return 0.0

    energy = compute_energy(pcm_bytes)
    margin = 200.0
    if energy <= baseline_energy + margin:
        return 0.0

    energy_score = min(1.0, (energy - (baseline_energy + margin)) / (margin * 2))

    # Compute ZCR — speech at 16kHz typically lands between 0.04 and 0.35
    fmt = f"<{len(pcm_bytes) // 2}h"
    try:
        samples = struct.unpack(fmt, pcm_bytes)
        crossings = sum(
            1 for i in range(1, len(samples))
            if (samples[i - 1] >= 0 > samples[i]) or (samples[i - 1] < 0 <= samples[i])
        )
        zcr = crossings / len(samples)
    except Exception:
        zcr = 0.0

    if 0.04 < zcr < 0.35:
        zcr_score = 1.0
    else:
        zcr_score = max(0.0, 1.0 - abs(zcr - 0.15) * 5)

    return energy_score * zcr_score


def _buffer_looks_like_speech(pcm_bytes: bytes, baseline_energy: float) -> bool:
    """Part 5 — Pre-validation gate: returns True only if the accumulated buffer
    has enough energy and speech-like spectral shape to justify a Vosk call.
    Saves CPU by skipping keyword recognition on clearly non-speech buffers."""
    if len(pcm_bytes) < 1024:
        return False
    energy = compute_energy(pcm_bytes)
    # Buffer must be meaningfully above baseline
    if energy < baseline_energy + 300:
        return False
    # Quick ZCR sanity on the last 2048 bytes (~64ms at 16kHz)
    tail = pcm_bytes[-2048:]
    fmt = f"<{len(tail) // 2}h"
    try:
        samples = struct.unpack(fmt, tail)
        crossings = sum(
            1 for i in range(1, len(samples))
            if (samples[i - 1] >= 0 > samples[i]) or (samples[i - 1] < 0 <= samples[i])
        )
        zcr = crossings / len(samples)
    except Exception:
        return True  # if we can't compute, let it pass
    return 0.03 < zcr < 0.40

logger = logging.getLogger(__name__)

class ListenerState(Enum):
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()
    PLAYING = auto()
    FOLLOWUP = auto()  # Active session mode

class ListenerStateMachine:
    def __init__(self, porcupine_key: str):
        if not porcupine_key:
            raise ValueError("[Listener] PORCUPINE_ACCESS_KEY is required.")

        self.wake_detector = WakeWordDetector(porcupine_key)
        self.pa = pyaudio.PyAudio()
        self.state = ListenerState.IDLE

        # Load Vosk model once — shared across all interrupt checks
        vosk.SetLogLevel(-1)
        model_path = os.path.join("models", "vosk-model-small-en-us-0.15")
        try:
            self.vosk_model = vosk.Model(model_path)
            logger.info("[Listener] Vosk model loaded (restricted grammar mode).")
        except Exception as e:
            logger.error(f"[Listener] Vosk model load failed: {e}")
            self.vosk_model = None
        
        self.audio_stream = self.pa.open(
            rate=self.wake_detector.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=self.wake_detector.frame_length,
            start=False
        )
        
        # Add output stream for TTS
        self.output_stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            output=True,
            start=False
        )
        self.playback_interrupted = asyncio.Event()
        self.voice_history = collections.deque(maxlen=3)
        self.memory = MemoryAgent()
        
        # Audio Buffer state
        self.audio_buffer = b""
        self.is_speaking = False
        self.silent_frames = 0
        self.speech_frames = 0
        self.interrupt_speech_frames = 0
        
        # 2. Wake Word Audio Buffer (1-2s ring buffer)
        self.pre_wake_buffer = collections.deque(maxlen=47)
        
        # 1. Dynamic Speech End Detection
        saved_silence = self.memory.get_preference("voice_silence_frames")
        if saved_silence:
            self.BASE_SILENCE_FRAMES = int(saved_silence)
        else:
            self.BASE_SILENCE_FRAMES = 25    # 25 * ~32ms = ~800ms of silence
            
        self.current_silence_threshold = self.BASE_SILENCE_FRAMES
        self.VAD_MIN_SPEECH_FRAMES = 5  # minimum speech to consider an utterance
        
        # 2. Noise Awareness
        self.baseline_noise = 0.0
        self.dynamic_energy_threshold = VAD_ENERGY_THRESHOLD
        self.BASE_ENERGY_MARGIN = 200
        
        self.listen_timeout = 4.0  # seconds fallback timeout
        self.listen_start_time = 0.0
        
        # 6. Active Session Mode timeout (15s)
        self.followup_timeout = 15.0
        self.followup_start_time = 0.0
        
        # 4. Cooldown Handling
        self.cooldown_until = 0.0
        
        self.orchestrator = OrchestratorAgent()
        self.session_id = get_or_create_session("+10000000000", channel="voice_local")
        
        self._running = False

        # Stage 2 state flags
        self._checking_interrupt = False
        self.playback_paused = False

        # Silence-stable resume counter (Part 4)
        self._resume_silence_counter = 0

        # Interrupt event log buffer for diagnostics (Part 7)
        self._interrupt_log: list = []

    def reset_vad(self):
        """Reset all VAD / interrupt counters for a fresh listening pass."""
        self.audio_buffer = b"".join(self.pre_wake_buffer)
        self.is_speaking = False
        self.silent_frames = 0
        self.speech_frames = 0
        self.interrupt_speech_frames = 0
        self.current_silence_threshold = self.BASE_SILENCE_FRAMES
        self._resume_silence_counter = 0

    # ── Part 7: Structured interrupt event logging ──────────────────────────
    def _log_interrupt_event(self, event: Dict[str, Any]) -> None:
        """Append one structured interrupt diagnostic record and emit to logger."""
        event["timestamp"] = time.time()
        self._interrupt_log.append(event)
        logger.info(f"[InterruptEvent] {json.dumps(event)}")

    def analyze_intent(self, text: str) -> str:
        """
        5. Intent Classification Upgrade
        Classifies input into distinct categories to drive interaction dynamics.
        """
        text_lower = text.lower().strip()
        words = set(re.findall(r'\b\w+\b', text_lower))
        first_word = text_lower.split()[0] if text_lower else ""
        
        # 1. Attention call
        if text_lower in ['jarvis', 'jarvis.', 'jarvis!', 'jarvis?']:
            return 'attention'
            
        # 2. Urgent
        urgent_keywords = {'help', 'quick', 'emergency', 'fast', 'hurry', 'urgent', 'now', 'abort', 'stop'}
        if words.intersection(urgent_keywords) or '!' in text:
            return 'urgent'
            
        # 3. Question
        question_words = {'what', 'how', 'why', 'who', 'where', 'when', 'is', 'are', 'can', 'do', 'does'}
        if '?' in text or first_word in question_words:
            return 'question'
            
        # 4. Command
        command_words = {'open', 'turn', 'play', 'stop', 'pause', 'search', 'find', 'tell', 'read', 'send', 'remind', 'create', 'set'}
        if first_word in command_words:
            return 'command'
            
        return 'casual'

    async def play_audio_response(self, text: str, tone: str = "casual"):
        """Generate and play audio response locally"""
        if not text.strip():
            if tone == "urgent":
                text = "I'm here, what happened?"
            elif tone == "attention":
                text = "Yes?"
            elif tone == "question":
                text = "Let me think..."
            elif tone == "command":
                text = "On it."
            else:
                text = "Yes?"
                
        logger.info(f"[JARVIS] >> {text}")
        if text.strip():
            self.voice_history.append({"role": "assistant", "content": text})
            
        self.state = ListenerState.PLAYING
        self.playback_interrupted.clear()
        self.playback_paused = False
        
        try:
            if self.output_stream.is_stopped():
                self.output_stream.start_stream()
                
            async for audio_chunk in stream_text_to_speech(text, output_format="pcm_16000"):
                if self.playback_interrupted.is_set():
                    logger.info("[Listener] Playback interrupted by user.")
                    break
                    
                if audio_chunk:
                    # Write in chunks to allow responsive interruption
                    CHUNK_SIZE = 4096
                    for i in range(0, len(audio_chunk), CHUNK_SIZE):
                        if self.playback_interrupted.is_set():
                            break
                            
                        if getattr(self, 'playback_paused', False):
                            # Immediate flush: stop stream to clear hardware buffers
                            if not self.output_stream.is_stopped():
                                self.output_stream.stop_stream()
                            
                            while getattr(self, 'playback_paused', False):
                                await asyncio.sleep(0.01)
                                if self.playback_interrupted.is_set():
                                    break
                            
                            # Resume: start stream
                            if not self.playback_interrupted.is_set() and self.output_stream.is_stopped():
                                self.output_stream.start_stream()
                                
                        chunk = audio_chunk[i:i+CHUNK_SIZE]
                        if not self.playback_interrupted.is_set() and not getattr(self, 'playback_paused', False):
                            await asyncio.get_event_loop().run_in_executor(None, self.output_stream.write, chunk)
                        
        except Exception as e:
            logger.error(f"[Listener] TTS playback error: {e}")
        finally:
            if not self.output_stream.is_stopped():
                self.output_stream.stop_stream()
            if self.state == ListenerState.PLAYING:
                self._transition_to_followup()

    async def _process_command(self, pcm_data: bytes):
        """Send PCM to STT, sense intent, get orchestrator response."""
        self.state = ListenerState.PROCESSING
        
        if not pcm_data:
            await self.play_audio_response("", tone="casual")
            self._transition_to_followup()
            return

        text = await transcribe_pcm(pcm_data, sample_rate=self.wake_detector.sample_rate)
        if not text:
            await self.play_audio_response("", tone="casual")
            self._transition_to_followup()
            return
            
        logger.info(f"[User] {text}")
        
        # WPM Profiling
        duration_sec = len(pcm_data) / (2 * self.wake_detector.sample_rate)
        if duration_sec > 1.0:
            words = len(text.split())
            wpm = (words / duration_sec) * 60
            logger.debug(f"[Listener] User WPM: {wpm:.1f}")
            new_silence_frames = max(20, min(50, int(6000 / max(50, wpm))))
            saved_silence = self.memory.get_preference("voice_silence_frames")
            if saved_silence:
                current_base = int(saved_silence)
                updated_base = int((current_base * 0.8) + (new_silence_frames * 0.2))
            else:
                updated_base = new_silence_frames
            self.memory.set_preference("voice_silence_frames", str(updated_base))
            self.BASE_SILENCE_FRAMES = updated_base
        
        intent = self.analyze_intent(text)
        logger.debug(f"[Intent] {intent}")
        
        if intent == 'attention':
            await self.play_audio_response("", tone=intent)
            return

        self.voice_history.append({"role": "user", "content": text})
        ctx_hint = "\n".join(f"{t['role'].title()}: {t['content']}" for t in self.voice_history)

        reply = self.orchestrator.process_request(
            session_id=self.session_id,
            user_input=text,
            tone="professional",
            channel="voice_local",
            role="owner",
            context_override=ctx_hint
        )
        
        await self.play_audio_response(reply)
        
    def _transition_to_followup(self):
        self.state = ListenerState.FOLLOWUP
        self.followup_start_time = time.monotonic()
        # 4. Cooldown Handling: ignore wake word / spurious VAD for 1.5s
        self.cooldown_until = time.monotonic() + 1.5
        logger.info("[Listener] Active Session mode. Ready for follow-up.")

    async def run(self):
        self._running = True
        self.audio_stream.start_stream()
        logger.info("[Listener] Loop started. Say 'Jarvis' to begin.")
        
        try:
            while self._running:
                pcm = await asyncio.get_event_loop().run_in_executor(
                    None, self.audio_stream.read, self.wake_detector.frame_length, False
                )
                
                # Maintain the ring buffer at all times
                self.pre_wake_buffer.append(pcm)
                
                if self.state == ListenerState.IDLE:
                    energy = compute_energy(pcm)
                    # 2. Noise Awareness - Baseline profiling
                    if energy < self.dynamic_energy_threshold:
                        self.baseline_noise = (0.95 * self.baseline_noise) + (0.05 * energy)
                        self.dynamic_energy_threshold = max(VAD_ENERGY_THRESHOLD, self.baseline_noise + self.BASE_ENERGY_MARGIN)
                        
                    if time.monotonic() > self.cooldown_until:
                        if self.wake_detector.process_frame(pcm):
                            logger.info("[Listener] Wake word detected -> LISTENING")
                            self.state = ListenerState.LISTENING
                            self.reset_vad()
                            self.listen_start_time = time.monotonic()
                        
                elif self.state == ListenerState.FOLLOWUP:
                    # 4. Active Session Optimization: Decay sensitivity
                    elapsed = time.monotonic() - self.followup_start_time
                    decay_factor = min(1.0, elapsed / self.followup_timeout)
                    followup_energy_threshold = self.dynamic_energy_threshold + (decay_factor * self.BASE_ENERGY_MARGIN)
                    
                    if time.monotonic() > self.cooldown_until:
                        energy = compute_energy(pcm)
                        if self.wake_detector.process_frame(pcm):
                            logger.info("[Listener] Wake word detected in FOLLOWUP -> LISTENING")
                            self.state = ListenerState.LISTENING
                            self.reset_vad()
                            self.listen_start_time = time.monotonic()
                            continue
                            
                        if energy > followup_energy_threshold:
                            logger.info("[Listener] Speech detected in FOLLOWUP -> LISTENING")
                            self.state = ListenerState.LISTENING
                            self.reset_vad()
                            self.listen_start_time = time.monotonic()
                            
                    if time.monotonic() - self.followup_start_time > self.followup_timeout:
                        logger.info("[Listener] Active Session timeout. Returning to IDLE.")
                        self.state = ListenerState.IDLE
                        
                elif self.state == ListenerState.LISTENING:
                    energy = compute_energy(pcm)
                    
                    if energy > self.dynamic_energy_threshold:
                        if not self.is_speaking:
                            self.is_speaking = True
                            logger.debug(f"[Listener] Speech started. Thresh: {self.dynamic_energy_threshold:.0f}")
                        self.audio_buffer += pcm
                        self.speech_frames += 1
                        self.silent_frames = 0
                        
                        # 1. Adaptive Silence Detection: Longer utterances get more pause leniency
                        if self.speech_frames > 40: # ~1.2s of continuous speaking
                            self.current_silence_threshold = int(self.BASE_SILENCE_FRAMES * 1.5) # ~1.2s silence
                    else:
                        if self.is_speaking:
                            self.audio_buffer += pcm
                            self.silent_frames += 1
                            
                            if self.silent_frames >= self.current_silence_threshold:
                                if self.speech_frames >= self.VAD_MIN_SPEECH_FRAMES:
                                    logger.info(f"[Listener] Command complete ({self.current_silence_threshold} silence frames).")
                                    asyncio.create_task(self._process_command(self.audio_buffer))
                                else:
                                    logger.debug("[Listener] Speech too short, ignoring.")
                                    asyncio.create_task(self._process_command(b""))
                                    
                                self.reset_vad()
                        else:
                            self.audio_buffer += pcm
                            if time.monotonic() - self.listen_start_time > self.listen_timeout:
                                logger.info("[Listener] Listen timeout reached.")
                                asyncio.create_task(self._process_command(self.audio_buffer))
                                self.reset_vad()
                                
                elif self.state == ListenerState.PROCESSING:
                    pass # Keep buffer mostly clean or handle interrupts if needed
                    
                elif self.state == ListenerState.PLAYING:
                    # ── Stage 1: Instant VAD-based pause (<200ms) ──────────────────
                    if time.monotonic() > self.cooldown_until:
                        energy = compute_energy(pcm)
                        vad_confidence = compute_vad_confidence(pcm, self.baseline_noise)

                        if vad_confidence > 0.5:
                            self.interrupt_speech_frames += 1
                            self.audio_buffer += pcm
                            # Reset the silence-resume counter when we hear speech
                            self._resume_silence_counter = 0

                            # Require 3 consecutive speech-like frames (~96ms) to trigger
                            if self.interrupt_speech_frames >= 3 and not self._checking_interrupt:
                                stage1_ts = time.monotonic()
                                logger.info(
                                    f"[Listener] Stage 1: Instant pause. "
                                    f"VAD={vad_confidence:.2f}, frames={self.interrupt_speech_frames}"
                                )
                                self.playback_paused = True

                                # ── Stage 2 check (runs as background task) ────────────
                                async def check_interrupt(trigger_time: float = stage1_ts):
                                    self._checking_interrupt = True
                                    vosk_used = False
                                    whisper_used = False
                                    text = ""

                                    # Part 3 — Adaptive validation window
                                    elapsed = 0.0
                                    window = VALIDATION_WINDOW_MIN
                                    while elapsed < window:
                                        await asyncio.sleep(VALIDATION_WINDOW_STEP)
                                        elapsed += VALIDATION_WINDOW_STEP
                                        # If speech is still arriving, extend window up to max
                                        if self.interrupt_speech_frames > 0 and window < VALIDATION_WINDOW_MAX:
                                            window = min(window + VALIDATION_WINDOW_STEP, VALIDATION_WINDOW_MAX)

                                    audio_to_check = self.audio_buffer

                                    # Part 5 — Pre-validation gate: skip recognition on non-speech buffers
                                    if not _buffer_looks_like_speech(audio_to_check, self.baseline_noise):
                                        logger.debug("[Listener] Pre-validation gate: buffer not speech-like.")
                                        text = ""
                                    else:
                                        # Part 1 — Vosk with restricted grammar (primary)
                                        if self.vosk_model is not None:
                                            try:
                                                loop = asyncio.get_event_loop()
                                                text = await loop.run_in_executor(
                                                    None, self._run_vosk_grammar, audio_to_check
                                                )
                                                vosk_used = True
                                                if text:
                                                    logger.debug(f"[Listener] Vosk (grammar) heard: '{text}'")
                                            except Exception as exc:
                                                logger.error(f"[Listener] Vosk error: {exc}")

                                        # Part 1 fallback — Whisper if Vosk returned nothing
                                        if not text:
                                            text = await transcribe_pcm(
                                                audio_to_check,
                                                sample_rate=self.wake_detector.sample_rate,
                                            )
                                            whisper_used = True
                                            if text:
                                                logger.debug(f"[Listener] Whisper fallback heard: '{text}'")

                                    # Part 2 — Strict confidence filtering: only allow-listed keywords
                                    is_interrupt = False
                                    if text:
                                        text_lower = text.lower().strip()
                                        if any(kw in text_lower for kw in INTERRUPT_KEYWORDS):
                                            is_interrupt = True

                                    latency_ms = (time.monotonic() - trigger_time) * 1000

                                    # Part 7 — Structured diagnostic log
                                    self._log_interrupt_event({
                                        "vad_trigger": True,
                                        "vosk_used": vosk_used,
                                        "whisper_used": whisper_used,
                                        "vosk_text": text if vosk_used else "",
                                        "whisper_text": text if whisper_used else "",
                                        "keyword_detected": is_interrupt,
                                        "latency_ms": round(latency_ms, 1),
                                        "false_alarm": not is_interrupt,
                                        "window_ms": round(window * 1000),
                                    })

                                    if is_interrupt:
                                        logger.info(
                                            f"[Listener] Stage 2: CONFIRMED interrupt "
                                            f"('{text}') in {latency_ms:.0f}ms"
                                        )
                                        buffer_copy = self.audio_buffer
                                        self.playback_interrupted.set()
                                        self.playback_paused = False
                                        self.state = ListenerState.LISTENING
                                        self.reset_vad()
                                        self.audio_buffer = buffer_copy
                                        self.listen_start_time = time.monotonic()
                                    else:
                                        # Part 4 — Silence-stable resume
                                        logger.info(
                                            f"[Listener] Stage 2: False alarm ('{text}'). "
                                            f"Waiting for silence before resume..."
                                        )
                                        await self._wait_for_stable_silence()
                                        self.playback_paused = False
                                        self.audio_buffer = b""
                                        self.interrupt_speech_frames = 0

                                    self._checking_interrupt = False

                                asyncio.create_task(check_interrupt())
                        else:
                            if not self._checking_interrupt:
                                self.interrupt_speech_frames = max(0, self.interrupt_speech_frames - 1)
                                # Track silence frames for resume stability
                                self._resume_silence_counter += 1
                            else:
                                # Still accumulating audio for Stage 2 validation
                                self.audio_buffer += pcm
                        
                await asyncio.sleep(0.001)

        except asyncio.CancelledError:
            pass
        finally:
            self.stop()

    # ── Part 1: Vosk with restricted grammar (fast, accurate) ──────────────
    def _run_vosk_grammar(self, audio: bytes) -> str:
        """Run Vosk recognition constrained to INTERRUPT_KEYWORDS only.
        The restricted grammar forces the decoder to pick from a tiny vocabulary,
        which is both faster and more accurate than open decoding."""
        rec = vosk.KaldiRecognizer(self.vosk_model, self.wake_detector.sample_rate, VOSK_GRAMMAR)
        rec.AcceptWaveform(audio)
        result = json.loads(rec.FinalResult())
        raw_text = result.get("text", "").strip()
        # Part 2 — Confidence filter: only return text that matches an allowed keyword
        if raw_text and any(kw in raw_text for kw in INTERRUPT_KEYWORDS):
            return raw_text
        return ""

    # ── Part 4: Silence-stable resume ──────────────────────────────────────
    async def _wait_for_stable_silence(self) -> None:
        """Block until we observe RESUME_SILENCE_FRAMES consecutive silent frames,
        guaranteeing the user has actually stopped talking before TTS resumes.
        Caps at 2 seconds to avoid hanging forever."""
        deadline = time.monotonic() + 2.0
        while self._resume_silence_counter < RESUME_SILENCE_FRAMES:
            if time.monotonic() > deadline:
                logger.debug("[Listener] Resume silence timeout — forcing resume.")
                break
            await asyncio.sleep(0.02)

    def stop(self):
        """Tear down all audio resources cleanly."""
        self._running = False
        if getattr(self, 'audio_stream', None):
            self.audio_stream.stop_stream()
            self.audio_stream.close()
        if getattr(self, 'output_stream', None):
            self.output_stream.stop_stream()
            self.output_stream.close()
        if getattr(self, 'pa', None):
            self.pa.terminate()
        if getattr(self, 'wake_detector', None):
            self.wake_detector.cleanup()
        logger.info("[Listener] Stopped.")
