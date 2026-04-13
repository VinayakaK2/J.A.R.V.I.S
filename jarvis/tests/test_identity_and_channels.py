"""
tests/test_identity_and_channels.py
─────────────────────────────────────
Unit tests for Part 9 — the four required test scenarios:

  1. OWNER (WhatsApp) → "Message Rahul I'll be late"   → message sent
  2. GUEST (Telegram) → "Open Chrome"                  → denied
  3. OWNER (Voice)    → "Kal ka weather bata"           → voice response (text path)
  4. OWNER (Voice)    → "Open YouTube"                  → system executes

Tests use unittest.mock to avoid calling real Twilio / Telegram APIs.
Run with: pytest tests/test_identity_and_channels.py -v
"""

import pytest
from unittest.mock import MagicMock, patch


# ─── Scenario 1: OWNER sends WhatsApp to Rahul ───────────────────────────────

def test_owner_whatsapp_send_message():
    """
    OWNER sends "Message Rahul I'll be late" via WhatsApp.
    The guard must allow send_whatsapp for owner with confirmed=True.
    """
    from safety.guard import SafetyGuard, PermissionLevel

    guard = SafetyGuard()
    level, reason = guard.evaluate_action(
        "send_whatsapp",
        {"number": "+919876543210", "message": "I'll be late", "confirmed": True},
        role="owner",
    )

    # Owner + confirmed=True → AUTO_ALLOW
    assert level == PermissionLevel.AUTO_ALLOW, (
        f"Expected AUTO_ALLOW for owner with confirmed=True, got {level}: {reason}"
    )


# ─── Scenario 2: GUEST tries to open Chrome via Telegram ─────────────────────

def test_guest_telegram_open_chrome_denied():
    """
    GUEST sends "Open Chrome" via Telegram.
    The guard must block open_application for a guest.
    """
    from safety.guard import SafetyGuard, PermissionLevel

    guard = SafetyGuard()
    level, reason = guard.evaluate_action(
        "open_application",
        {"app_name": "chrome"},
        role="guest",
    )

    # Guest trying a desktop tool → BLOCK with helpful message
    assert level == PermissionLevel.BLOCK, (
        f"Expected BLOCK for guest calling open_application, got {level}"
    )
    assert "permission" in reason.lower(), (
        f"Expected a permission-related denial message, got: {reason}"
    )


# ─── Scenario 3: OWNER voice call — "Kal ka weather bata" ─────────────────

def test_owner_voice_weather_query():
    """
    OWNER asks for tomorrow's weather via a voice call.
    search_web is AUTO_ALLOW for both owner and guest.
    """
    from safety.guard import SafetyGuard, PermissionLevel

    guard = SafetyGuard()
    level, reason = guard.evaluate_action(
        "search_web",
        {"query": "tomorrow weather forecast"},
        role="owner",
    )
    assert level == PermissionLevel.AUTO_ALLOW, (
        f"Expected AUTO_ALLOW for search_web on voice call, got {level}: {reason}"
    )


# ─── Scenario 4: OWNER voice call — "Open YouTube" ──────────────────────

def test_owner_voice_open_youtube():
    """
    OWNER says "Open YouTube" on a voice call.
    open_website is AUTO_ALLOW for the owner.
    """
    from safety.guard import SafetyGuard, PermissionLevel

    guard = SafetyGuard()
    level, reason = guard.evaluate_action(
        "open_website",
        {"url": "https://www.youtube.com"},
        role="owner",
    )

    assert level == PermissionLevel.AUTO_ALLOW, (
        f"Expected AUTO_ALLOW for owner opening YouTube, got {level}: {reason}"
    )


# ─── Identity resolution tests ────────────────────────────────────────────────

class TestIdentityResolution:
    """Verify get_user_role correctly classifies owner vs. guest identifiers."""

    def test_owner_phone_recognised(self):
        """Owner's phone number returns 'owner' role."""
        from auth.identity import get_user_role, OWNER
        role = get_user_role(OWNER["phone"])
        assert role == "owner", f"Expected 'owner' for owner phone, got '{role}'"

    def test_whatsapp_prefix_stripped(self):
        """Twilio whatsapp: prefix is stripped before phone comparison."""
        from auth.identity import get_user_role, OWNER
        role = get_user_role(f"whatsapp:{OWNER['phone']}")
        assert role == "owner", "whatsapp: prefix should be stripped during comparison"

    def test_unknown_phone_is_guest(self):
        """An unregistered phone number returns 'guest' role."""
        from auth.identity import get_user_role
        role = get_user_role("+10000000000")
        assert role == "guest", f"Unknown caller should be 'guest', got '{role}'"

    def test_owner_telegram_id_recognised(self):
        """Owner's Telegram ID string returns 'owner' role."""
        from auth.identity import get_user_role, OWNER
        role = get_user_role(str(OWNER["telegram_id"]))
        assert role == "owner", f"Expected 'owner' for owner telegram_id, got '{role}'"

    def test_unknown_telegram_id_is_guest(self):
        """An unregistered Telegram ID returns 'guest' role."""
        from auth.identity import get_user_role
        role = get_user_role("9999999999")
        assert role == "guest", f"Unknown telegram id should be 'guest', got '{role}'"

    def test_empty_identifier_is_guest(self):
        """Empty string should always default to 'guest' — no crash."""
        from auth.identity import get_user_role
        role = get_user_role("")
        assert role == "guest"


# ─── Channel formatting tests ─────────────────────────────────────────────────

class TestChannelFormatting:
    """Verify CommunicationAgent formats text correctly per channel."""

    def _agent(self):
        from communication.responder import CommunicationAgent
        return CommunicationAgent()

    def test_voice_strips_markdown(self):
        """Voice channel output must be free of markdown symbols."""
        agent = self._agent()
        raw = "## Weather Update\n**Tomorrow**: 32°C, *partly cloudy*"
        result = agent.format_response(raw, tone="professional", channel="voice")
        assert "**" not in result and "##" not in result and "*" not in result, (
            f"Markdown not stripped for voice: {result}"
        )

    def test_whatsapp_short(self):
        """WhatsApp replies must be within the 600-char limit."""
        agent = self._agent()
        raw = "x" * 700
        result = agent.format_response(raw, tone="professional", channel="whatsapp")
        assert len(result) <= 600, f"WhatsApp reply exceeded 600 chars: {len(result)}"

    def test_telegram_retains_bold(self):
        """Telegram replies may keep *bold* markdown markers."""
        agent = self._agent()
        raw = "Here is the result: *key fact* about the query."
        result = agent.format_response(raw, tone="professional", channel="telegram")
        assert "*" in result, f"Telegram channel unexpectedly stripped bold: {result}"

    def test_hinglish_tone_voice(self):
        """Hinglish tone wraps output with Bhai prefix on voice channel."""
        agent = self._agent()
        raw = "32 degrees tomorrow"
        result = agent.format_response(raw, tone="hinglish", channel="voice")
        assert "Bhai" in result, f"Expected Hinglish prefix 'Bhai' in: {result}"


# ─── Unified session store tests ─────────────────────────────────────────────

class TestSessionStore:
    """Verify cross-channel session keys are stable and phone-keyed."""

    def test_same_phone_same_session(self):
        """Same phone number on different channels must return the same session_id."""
        from memory.session_store import get_or_create_session
        s1 = get_or_create_session("+919876543210", channel="whatsapp")
        s2 = get_or_create_session("+919876543210", channel="voice")
        assert s1 == s2, f"Expected same session for same phone, got {s1} vs {s2}"

    def test_whatsapp_prefix_normalised(self):
        """whatsapp: prefix must be stripped so session key is consistent."""
        from memory.session_store import get_or_create_session
        s1 = get_or_create_session("+919876543210", channel="whatsapp")
        s2 = get_or_create_session("whatsapp:+919876543210", channel="whatsapp")
        assert s1 == s2, "whatsapp: prefix not normalised in session store"

    def test_different_phones_different_sessions(self):
        """Two different callers must get independent session IDs."""
        from memory.session_store import get_or_create_session
        s1 = get_or_create_session("+911111111111", channel="whatsapp")
        s2 = get_or_create_session("+912222222222", channel="telegram")
        assert s1 != s2, "Different callers should have different sessions"
