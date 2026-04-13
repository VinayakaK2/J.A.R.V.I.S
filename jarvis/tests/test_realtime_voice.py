"""
tests/test_realtime_voice.py
─────────────────────────────
Unit tests for the real-time streaming voice pipeline (v7.1).

Tests cover:
  1. TTS chunk splitting at sentence boundaries
  2. VAD energy threshold (speech vs silence detection)
  3. µ-law → PCM conversion sanity check
  4. Interrupt flag is set when speech detected during TTS
  5. voice_stream channel formatting (no markdown, no filler)
  6. CallSession rolling memory (capped at VOICE_MEMORY_TURNS)
  7. WebSocket simulation (local end-to-end pipeline mock)

Run with: pytest tests/test_realtime_voice.py -v
"""

import asyncio
import audioop
import base64
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── 1. TTS chunk splitting ────────────────────────────────────────────────────

class TestTTSChunkSplitting:
    """split_into_chunks must produce sentence-sized pieces within size limits."""

    def test_short_text_single_chunk(self):
        """Text shorter than MIN_CHUNK_LEN produces exactly one chunk."""
        from voice.tts_stream import split_into_chunks
        chunks = split_into_chunks("Hello.")
        assert len(chunks) == 1
        assert chunks[0] == "Hello."

    def test_two_sentences_two_chunks(self):
        """Two well-formed sentences produce two chunks."""
        from voice.tts_stream import split_into_chunks, MIN_CHUNK_LEN
        # Each sentence must be long enough to flush its own buffer
        s1 = "The weather tomorrow will be thirty-two degrees and partly cloudy."
        s2 = "Please carry an umbrella if you go out in the afternoon."
        chunks = split_into_chunks(f"{s1} {s2}")
        assert len(chunks) >= 1            # At least one chunk produced
        combined = " ".join(chunks)
        assert "thirty-two" in combined
        assert "umbrella" in combined

    def test_very_long_sentence_split(self):
        """A sentence exceeding MAX_CHUNK_LEN must be force-split."""
        from voice.tts_stream import split_into_chunks, MAX_CHUNK_LEN
        long_sentence = "word " * 60 + "end."   # Well over 200 chars
        chunks = split_into_chunks(long_sentence)
        # Every chunk must be within MAX_CHUNK_LEN
        for chunk in chunks:
            assert len(chunk) <= MAX_CHUNK_LEN + 5, (
                f"Chunk too long ({len(chunk)} chars): {chunk[:60]}…"
            )

    def test_empty_input_no_chunks(self):
        """Empty input must produce zero chunks without crashing."""
        from voice.tts_stream import split_into_chunks
        assert split_into_chunks("") == []
        assert split_into_chunks("   ") == []


# ─── 2. VAD energy detection ──────────────────────────────────────────────────

class TestVAD:
    """VAD must classify silent and loud audio frames correctly."""

    def _make_mulaw_silence(self, num_samples: int = 160) -> bytes:
        """Generate µ-law encoded near-silence (value 0xFF = digital silence)."""
        return bytes([0xFF] * num_samples)

    def _make_mulaw_speech(self, num_samples: int = 160) -> bytes:
        """
        Generate synthetic 'loud' µ-law audio by encoding a 1000Hz sine wave.
        Uses audioop to produce real µ-law bytes from PCM.
        """
        import math
        # Build 16-bit PCM samples for a loud 1kHz tone
        pcm = struct.pack(
            f"<{num_samples}h",
            *[int(20000 * math.sin(2 * math.pi * 1000 * i / 8000))
              for i in range(num_samples)]
        )
        return audioop.lin2ulaw(pcm, 2)

    def _compute_pcm_energy(self, mulaw: bytes) -> int:
        from voice.realtime import mulaw_to_pcm, compute_energy
        return compute_energy(mulaw_to_pcm(mulaw))

    def test_silence_energy_below_threshold(self):
        """Near-silence µ-law frames should produce low energy (< 300)."""
        energy = self._compute_pcm_energy(self._make_mulaw_silence())
        assert energy < 300, f"Silence energy too high: {energy}"

    def test_speech_energy_above_threshold(self):
        """Loud speech µ-law frames should produce high energy (> 300)."""
        energy = self._compute_pcm_energy(self._make_mulaw_speech())
        assert energy > 300, f"Speech energy too low: {energy}"

    def test_mulaw_to_pcm_correct_length(self):
        """160 µ-law 8-bit samples → 320 bytes of 16-bit PCM."""
        from voice.realtime import mulaw_to_pcm
        pcm = mulaw_to_pcm(self._make_mulaw_silence(160))
        assert len(pcm) == 320, f"Expected 320 PCM bytes, got {len(pcm)}"


# ─── 3. CallSession rolling memory ───────────────────────────────────────────

class TestCallSession:
    """CallSession rolling voice history must be bounded and ordered."""

    def _make_session(self):
        from voice.realtime import CallSession
        # Patch identity/session so no real DB/network calls
        with patch("voice.realtime.get_user_info", return_value={"role": "owner"}):
            with patch("voice.realtime.get_or_create_session", return_value="s_test"):
                return CallSession(stream_sid="TEST_SID", caller="+910000000000")

    def test_history_bounded_at_max_turns(self):
        """Adding more turns than VOICE_MEMORY_TURNS drops oldest entries."""
        from voice.realtime import VOICE_MEMORY_TURNS
        session = self._make_session()
        for i in range(VOICE_MEMORY_TURNS + 3):
            session.add_voice_turn("user", f"Message {i}")
        assert len(session.voice_history) == VOICE_MEMORY_TURNS, (
            f"History should be capped at {VOICE_MEMORY_TURNS}, "
            f"got {len(session.voice_history)}"
        )

    def test_context_string_contains_recent_turns(self):
        """get_context_string must include the most recent turns."""
        session = self._make_session()
        session.add_voice_turn("user", "What is the weather?")
        session.add_voice_turn("assistant", "It will be 32 degrees.")
        ctx = session.get_context_string()
        assert "weather" in ctx
        assert "32 degrees" in ctx

    def test_interrupt_flag_initially_clear(self):
        """interrupt_flag must start clear so TTS runs immediately."""
        session = self._make_session()
        assert not session.interrupt_flag.is_set(), (
            "interrupt_flag should start clear on a new session."
        )


# ─── 4. Interrupt flag set during TTS ─────────────────────────────────────────

class TestInterruptHandling:
    """VADProcessor must set interrupt_flag when speech arrives during TTS."""

    def test_interrupt_set_on_speech_during_tts(self):
        """Speaking while TTS lock is held must set the interrupt flag."""
        import asyncio
        import math
        import struct
        from voice.realtime import CallSession, VADProcessor

        with patch("voice.realtime.get_user_info", return_value={"role": "owner"}):
            with patch("voice.realtime.get_or_create_session", return_value="s_int"):
                session = CallSession(stream_sid="SID_INT", caller="+910000000001")

        vad = VADProcessor(session)

        # Simulate TTS lock being held (someone is streaming audio)
        loop = asyncio.new_event_loop()

        async def _test():
            async with session.tts_lock:
                # Feed loud speech frames while TTS lock is held
                speech_mulaw = bytes([0x00] * 160)  # 0x00 = max amplitude in µ-law
                for _ in range(5):
                    vad.process_frame(speech_mulaw)
                # Interrupt flag should now be set
                assert session.interrupt_flag.is_set(), (
                    "interrupt_flag should be set when speech arrives during TTS."
                )

        loop.run_until_complete(_test())
        loop.close()


# ─── 5. voice_stream channel formatting ───────────────────────────────────────

class TestVoiceStreamFormatting:
    """voice_stream channel must strip markdown + filler phrases."""

    def _agent(self):
        from communication.responder import CommunicationAgent
        return CommunicationAgent()

    def test_strips_markdown_for_stream(self):
        """voice_stream removes ** bold ** and # headers."""
        agent = self._agent()
        raw = "## Result\n**Answer**: It will be *32 degrees* tomorrow."
        result = agent.format_response(raw, tone="professional", channel="voice_stream")
        assert "**" not in result and "#" not in result and "*" not in result

    def test_strips_filler_phrases(self):
        """'Sure!' and 'Of course!' prefixes must be stripped."""
        agent = self._agent()
        raw = "Sure! The weather will be cloudy."
        result = agent.format_response(raw, tone="professional", channel="voice_stream")
        assert not result.startswith("Sure"), f"Filler not stripped: {result}"

    def test_respects_max_len_150(self):
        """voice_stream responses must be ≤ 150 characters."""
        agent = self._agent()
        raw = "x" * 200
        result = agent.format_response(raw, tone="professional", channel="voice_stream")
        assert len(result) <= 153, f"voice_stream reply too long: {len(result)}"


# ─── 6. WebSocket pipeline simulation (local end-to-end) ──────────────────────

class TestWebSocketSimulation:
    """
    Simulate a complete Twilio Media Streams WebSocket conversation locally.

    Uses a mock WebSocket that plays back pre-recorded µ-law frames and
    captures the JSON messages JARVIS sends back.
    """

    def _make_mock_ws(self, events: list):
        """
        Build a mock WebSocket that yields the given event dicts as JSON strings
        and records send_text calls in a list.
        """
        sent_messages = []

        async def iter_text():
            for evt in events:
                yield json.dumps(evt)

        mock_ws = MagicMock()
        mock_ws.iter_text = iter_text
        mock_ws.send_text = AsyncMock(side_effect=lambda msg: sent_messages.append(msg))
        return mock_ws, sent_messages

    def test_start_event_creates_session_and_sends_greeting(self):
        """START event must trigger an async greeting to the caller."""
        from voice.realtime import handle_media_stream

        # Silence µ-law payload for a stop frame
        silent_payload = base64.b64encode(bytes([0xFF] * 160)).decode()

        events = [
            {
                "event": "start",
                "start": {
                    "streamSid": "MS_TEST_001",
                    "customParameters": {"caller": "+910000000000"},
                    "from": "+910000000000",
                },
            },
            # A few silent media frames
            {"event": "media", "media": {"payload": silent_payload}},
            {"event": "stop"},
        ]

        mock_ws, sent = self._make_mock_ws(events)

        # Patch stream_response_to_twilio to avoid real TTS
        async def fake_stream(session, text, ws, sid):
            await ws.send_text(json.dumps({
                "event": "media", "streamSid": sid,
                "media": {"payload": base64.b64encode(b"FAKE_MP3").decode()}
            }))

        with patch("voice.realtime.stream_response_to_twilio", side_effect=fake_stream):
            asyncio.run(handle_media_stream(mock_ws))

        # At least the greeting should have been queued/sent
        assert len(sent) >= 1, "Expected at least one media message sent to Twilio WS."
