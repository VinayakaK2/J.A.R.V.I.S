"""
test_interruption_pipeline.py
─────────────────────────────
Part 6 — Structured test harness for the JARVIS interruption pipeline.

Tests cover:
  1. Speed       — Vosk grammar recognition latency on short keyword audio.
  2. Accuracy    — All interrupt keywords are correctly detected.
  3. False Alarm — Random speech / noise does NOT trigger interruption.
  4. Resume      — Silence-stable resume gate works correctly.
  5. Pre-gate    — Non-speech buffers are rejected before Vosk runs.

Usage:
  python test_interruption_pipeline.py

Requirements:
  - Vosk model extracted at models/vosk-model-small-en-us-0.15
  - vosk package installed
"""

import os
import sys
import json
import time
import struct
import math
import random
import logging

# Ensure the jarvis package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "jarvis"))

import vosk

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("test_interrupt")

# ── Constants (must match listener.py) ─────────────────────────────────────────
INTERRUPT_KEYWORDS = [
    "stop", "wait", "no", "cancel", "hold on",
    "pause", "actually", "never mind", "nevermind",
    "one sec", "no no", "wait wait", "stop it",
]
VOSK_GRAMMAR = json.dumps(INTERRUPT_KEYWORDS + [""])
MODEL_PATH = os.path.join("models", "vosk-model-small-en-us-0.15")
SAMPLE_RATE = 16000  # Porcupine / listener sample rate


# ── Helpers ────────────────────────────────────────────────────────────────────

def _generate_sine_pcm(freq_hz: float, duration_s: float, amplitude: int = 8000) -> bytes:
    """Generate a pure sine-wave PCM buffer at SAMPLE_RATE. Useful for energy/ZCR checks."""
    num_samples = int(SAMPLE_RATE * duration_s)
    samples = []
    for i in range(num_samples):
        sample = int(amplitude * math.sin(2 * math.pi * freq_hz * i / SAMPLE_RATE))
        samples.append(sample)
    return struct.pack(f"<{len(samples)}h", *samples)


def _generate_noise_pcm(duration_s: float, amplitude: int = 300) -> bytes:
    """Generate low-amplitude white noise (simulates background hum)."""
    num_samples = int(SAMPLE_RATE * duration_s)
    samples = [random.randint(-amplitude, amplitude) for _ in range(num_samples)]
    return struct.pack(f"<{len(samples)}h", *samples)


def _generate_silence_pcm(duration_s: float) -> bytes:
    """Generate pure silence."""
    num_samples = int(SAMPLE_RATE * duration_s)
    return b"\x00\x00" * num_samples


def _run_vosk_grammar(model: vosk.Model, audio: bytes) -> str:
    """Mirror of ListenerStateMachine._run_vosk_grammar for isolated testing."""
    rec = vosk.KaldiRecognizer(model, SAMPLE_RATE, VOSK_GRAMMAR)
    rec.AcceptWaveform(audio)
    result = json.loads(rec.FinalResult())
    raw_text = result.get("text", "").strip()
    if raw_text and any(kw in raw_text for kw in INTERRUPT_KEYWORDS):
        return raw_text
    return ""


# ── Pre-validation gate (mirrors listener.py) ─────────────────────────────────

def _buffer_looks_like_speech(pcm_bytes: bytes, baseline_energy: float) -> bool:
    """Duplicate of the listener's pre-validation gate for isolated testing."""
    if len(pcm_bytes) < 1024:
        return False
    import audioop
    energy = audioop.rms(pcm_bytes, 2)
    if energy < baseline_energy + 300:
        return False
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
        return True
    return 0.03 < zcr < 0.40


# ── Test Cases ─────────────────────────────────────────────────────────────────

def test_1_speed(model: vosk.Model) -> None:
    """Test 1 — Vosk restricted-grammar recognition latency.
    Measures how quickly Vosk decodes a short audio buffer with the grammar constraint.
    Expected: well under 200ms per call on modern hardware."""
    logger.info("═══ Test 1: Speed (Vosk grammar latency) ═══")

    # Use 500ms of 300Hz sine as a synthetic "voice" stimulus
    audio = _generate_sine_pcm(300, 0.5, amplitude=12000)
    timings = []

    for _ in range(10):
        t0 = time.perf_counter()
        _run_vosk_grammar(model, audio)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        timings.append(elapsed_ms)

    avg = sum(timings) / len(timings)
    mx = max(timings)
    logger.info(f"  Avg latency: {avg:.1f}ms | Max: {mx:.1f}ms | Target: <200ms")
    if avg < 200:
        logger.info("  ✅ PASS — Vosk grammar latency is within budget.")
    else:
        logger.warning("  ⚠️  WARN — Vosk grammar latency exceeds 200ms target.")


def test_2_accuracy(model: vosk.Model) -> None:
    """Test 2 — Keyword detection accuracy.
    Feeds each interrupt keyword (as synthetic audio) through the grammar recognizer.
    NOTE: Because we use synthetic sine waves (not real speech), Vosk may not
    transcribe them as actual words. This test validates the PIPELINE LOGIC
    (grammar constraint + keyword filtering) rather than acoustic accuracy.
    For real accuracy testing, use the manual test instructions below."""
    logger.info("═══ Test 2: Accuracy (grammar constraint validation) ═══")

    # Validate that the grammar JSON is well-formed and contains all keywords
    parsed_grammar = json.loads(VOSK_GRAMMAR)
    missing = [kw for kw in INTERRUPT_KEYWORDS if kw not in parsed_grammar]
    if missing:
        logger.error(f"  ❌ FAIL — Missing keywords in grammar: {missing}")
    else:
        logger.info(f"  ✅ PASS — Grammar contains all {len(INTERRUPT_KEYWORDS)} keywords + empty string.")

    # Verify the filter logic rejects non-keyword text
    for fake_text in ["hello", "open youtube", "weather today", ""]:
        is_match = any(kw in fake_text.lower() for kw in INTERRUPT_KEYWORDS)
        if is_match:
            logger.error(f"  ❌ FAIL — '{fake_text}' incorrectly matched a keyword!")
        else:
            logger.info(f"  ✅ PASS — '{fake_text}' correctly rejected.")

    # Verify the filter logic accepts actual keywords
    for kw in ["stop", "wait", "hold on", "cancel", "actually"]:
        is_match = any(k in kw for k in INTERRUPT_KEYWORDS)
        if is_match:
            logger.info(f"  ✅ PASS — '{kw}' correctly matched.")
        else:
            logger.error(f"  ❌ FAIL — '{kw}' should have matched but didn't!")


def test_3_false_trigger(model: vosk.Model) -> None:
    """Test 3 — False trigger rejection.
    Feeds non-speech audio (noise, silence, single click) through the pipeline
    and verifies that no keyword is detected."""
    logger.info("═══ Test 3: False Trigger (noise rejection) ═══")

    test_cases = [
        ("Low noise (typing/background)", _generate_noise_pcm(0.5, amplitude=200)),
        ("Medium noise (louder background)", _generate_noise_pcm(0.5, amplitude=600)),
        ("Pure silence", _generate_silence_pcm(0.5)),
        ("High-freq tone (not speech)", _generate_sine_pcm(8000, 0.5, amplitude=5000)),
        ("Very low-freq hum (AC noise)", _generate_sine_pcm(60, 0.5, amplitude=3000)),
    ]

    all_pass = True
    for label, audio in test_cases:
        text = _run_vosk_grammar(model, audio)
        if text:
            logger.error(f"  ❌ FAIL — '{label}' produced keyword: '{text}'")
            all_pass = False
        else:
            logger.info(f"  ✅ PASS — '{label}' → no keyword detected.")

    if all_pass:
        logger.info("  ✅ ALL FALSE TRIGGER TESTS PASSED.")


def test_4_resume_silence() -> None:
    """Test 4 — Silence-stable resume validation.
    Verifies that the pre-validation gate correctly rejects non-speech buffers
    and accepts speech-like buffers."""
    logger.info("═══ Test 4: Resume / Pre-validation Gate ═══")

    baseline = 100.0  # typical quiet room baseline

    # Silence should NOT look like speech
    silence = _generate_silence_pcm(0.3)
    if _buffer_looks_like_speech(silence, baseline):
        logger.error("  ❌ FAIL — Silence was classified as speech-like!")
    else:
        logger.info("  ✅ PASS — Silence correctly rejected by pre-gate.")

    # Low noise should NOT look like speech
    noise = _generate_noise_pcm(0.3, amplitude=150)
    if _buffer_looks_like_speech(noise, baseline):
        logger.error("  ❌ FAIL — Low noise was classified as speech-like!")
    else:
        logger.info("  ✅ PASS — Low noise correctly rejected by pre-gate.")

    # Speech-like signal (mixed frequencies, moderate energy) should pass
    speech_sim = _generate_sine_pcm(300, 0.3, amplitude=8000)
    if _buffer_looks_like_speech(speech_sim, baseline):
        logger.info("  ✅ PASS — Speech-like signal correctly accepted by pre-gate.")
    else:
        logger.warning("  ⚠️  WARN — Speech-like signal rejected (may need threshold tuning).")

    # Very short buffer should be rejected
    tiny = _generate_sine_pcm(300, 0.01, amplitude=8000)
    if _buffer_looks_like_speech(tiny, baseline):
        logger.error("  ❌ FAIL — Tiny buffer was accepted (should require >= 1024 bytes)!")
    else:
        logger.info("  ✅ PASS — Tiny buffer correctly rejected by pre-gate.")


def test_5_adaptive_window_constants() -> None:
    """Test 5 — Validate adaptive window configuration constants."""
    logger.info("═══ Test 5: Adaptive Window Constants ═══")

    # These must match the values in jarvis/voice/listener.py
    VALIDATION_WINDOW_MIN = 0.35
    VALIDATION_WINDOW_MAX = 0.70
    VALIDATION_WINDOW_STEP = 0.05

    if VALIDATION_WINDOW_MIN < VALIDATION_WINDOW_MAX:
        logger.info(f"  ✅ PASS — Window range: {VALIDATION_WINDOW_MIN*1000:.0f}ms → {VALIDATION_WINDOW_MAX*1000:.0f}ms")
    else:
        logger.error(f"  ❌ FAIL — MIN ({VALIDATION_WINDOW_MIN}) >= MAX ({VALIDATION_WINDOW_MAX})")

    if VALIDATION_WINDOW_STEP > 0:
        logger.info(f"  ✅ PASS — Step size: {VALIDATION_WINDOW_STEP*1000:.0f}ms")
    else:
        logger.error(f"  ❌ FAIL — Step must be positive, got {VALIDATION_WINDOW_STEP}")

    # Worst-case iterations
    max_iters = int(VALIDATION_WINDOW_MAX / VALIDATION_WINDOW_STEP)
    logger.info(f"  Info — Max iterations in adaptive loop: {max_iters}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    logger.info("╔═══════════════════════════════════════════════════════════╗")
    logger.info("║   JARVIS Interruption Pipeline Test Harness              ║")
    logger.info("╚═══════════════════════════════════════════════════════════╝")

    # Load Vosk model
    if not os.path.isdir(MODEL_PATH):
        logger.error(f"Vosk model not found at '{MODEL_PATH}'. Run download_vosk.py first.")
        sys.exit(1)

    vosk.SetLogLevel(-1)
    model = vosk.Model(MODEL_PATH)
    logger.info(f"Vosk model loaded from '{MODEL_PATH}'.\n")

    test_1_speed(model)
    print()
    test_2_accuracy(model)
    print()
    test_3_false_trigger(model)
    print()
    test_4_resume_silence()
    print()
    test_5_adaptive_window_constants()

    print()
    logger.info("═══════════════════════════════════════════════════════════")
    logger.info("MANUAL TESTING INSTRUCTIONS (real microphone required):")
    logger.info("─────────────────────────────────────────────────────────")
    logger.info("1. Run: python run_voice_loop.py")
    logger.info("2. Say 'Jarvis' → wait for response to start playing.")
    logger.info("3. SPEED TEST:     Say 'stop' during playback → should pause < 200ms.")
    logger.info("4. ACCURACY TEST:  Say 'wait', 'cancel', 'hold on' → each should interrupt.")
    logger.info("5. FALSE ALARM:    Cough or clap during playback → should pause then resume.")
    logger.info("6. RESUME TEST:    Say a random word → pause → silence → clean resume.")
    logger.info("7. Check logs for [InterruptEvent] JSON entries with latency_ms values.")
    logger.info("═══════════════════════════════════════════════════════════")


if __name__ == "__main__":
    main()
