"""
tests/test_proactive_assistant.py
─────────────────────────────────
Unit tests to ensure the silence logic strongly protects the user from spam,
and triggers correctly evaluate contexts.
"""
import pytest
import time
from background.trigger_engine import TriggerEngine

def test_silence_logic_typing():
    """Ensure we do not interrupt if the user is actively working (idle < 2s)."""
    engine = TriggerEngine(cooldown_seconds=600)
    # Simulate a user actively typing in Chrome
    context = {
        "state": "working",
        "app_context": "browsing",
        "raw_activity": {"app": "Google Chrome", "idle_seconds": 1, "event": "active"}
    }
    trigger = engine.evaluate(context)
    assert trigger["should_trigger"] is False, "Failed to stay silent while active"

def test_silence_logic_in_calls():
    """Ensure we never interrupt when meeting software is focused."""
    engine = TriggerEngine(cooldown_seconds=600)
    # User is in a zoom meeting, maybe idle because they are just listening
    context = {
        "state": "idle",
        "app_context": "unknown",
        "raw_activity": {"app": "Zoom Meeting", "idle_seconds": 500, "event": "idle"}
    }
    trigger = engine.evaluate(context)
    assert trigger["should_trigger"] is False, "Failed to stay silent during a call"

def test_silence_logic_cooldown():
    """Ensure the system doesn't spam by honoring cooldown periods."""
    engine = TriggerEngine(cooldown_seconds=600)
    engine.last_trigger_time = time.time() # Just triggered!
    
    # Excellent trigger conditions (morning unlock)
    context = {
        "state": "starting_day",
        "app_context": "unknown",
        "raw_activity": {"app": "LockApp.exe", "idle_seconds": 3, "event": "unlock"}
    }
    trigger = engine.evaluate(context)
    assert trigger["should_trigger"] is False, "Failed to honor the cooldown"

def test_trigger_idle_resume():
    """Should ask to resume when returning from a long idle period."""
    engine = TriggerEngine(cooldown_seconds=600)
    
    context = {
        "state": "working",
        "app_context": "unknown",
        "raw_activity": {"app": "Explorer", "idle_seconds": 1, "event": "unlock"}
    }
    trigger = engine.evaluate(context)
    assert trigger["should_trigger"] is True
    assert trigger["trigger_type"] == "idle_prompt"

def test_trigger_coding_resume_with_goal():
    """Should prompt to continue the known goal when opening a coding app."""
    engine = TriggerEngine(cooldown_seconds=600)
    
    context = {
        "state": "working",
        "app_context": "coding",
        "recent_goal": "Finish the background assistant module",
        "raw_activity": {"app": "Visual Studio Code", "idle_seconds": 3, "event": "app_change"}
    }
    trigger = engine.evaluate(context)
    assert trigger["should_trigger"] is True
    assert trigger["trigger_type"] == "work_prompt"
    assert "Finish the background assistant module" in trigger["message_hint"]
