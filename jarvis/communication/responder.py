"""
communication/responder.py
──────────────────────────
Formats JARVIS replies so they feel native on each channel.

Channel   | Behaviour
──────────┼─────────────────────────────────────────────────────────────
voice     | Natural spoken sentences — no markdown, no lists, concise
whatsapp  | Short, punchy — emojis and basic line-breaks only
telegram  | Richer — supports *bold*, bullet points, slightly longer
default   | Plain text (used for REST API / browser / CLI callers)

Tone modifiers (applied on top of channel formatting):
  professional — formal third-person
  friendly     — warm, first-person
  hinglish     — mixed Hindi/English casual (used in voice calls)
"""

import logging
import re

logger = logging.getLogger(__name__)

# Maximum character lengths per channel to keep messages digestible
_CHANNEL_MAX_LEN: dict = {
    "voice":     300,   # TTS reads long replies slowly — keep short
    "whatsapp":  600,   # WhatsApp chat bubble limit (practical)
    "telegram":  1024,  # Telegram message limit per bubble
    "default":   4096,  # REST/browser — generous limit
}


def _strip_markdown(text: str) -> str:
    """Remove markdown syntax so voice TTS reads clean sentences."""
    # Remove bold, italic, code ticks, headers, bullet markers
    text = re.sub(r"[*_`#>]", "", text)
    # Collapse multiple spaces / newlines into single space
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _truncate(text: str, max_len: int) -> str:
    """Truncate to max_len characters, appending ellipsis if cut."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class CommunicationAgent:
    """
    Transforms raw tool-execution output into a channel-appropriate reply.
    Instantiated once as a global singleton inside the orchestrator.
    """

    def format_response(
        self,
        raw_output: str,
        tone: str = "professional",
        channel: str = "default",
    ) -> str:
        """
        Convert raw_output to a polished, channel-appropriate string.

        Args:
            raw_output: The raw text produced by tool execution or LLM.
            tone:       Response persona — "professional" | "friendly" | "hinglish".
            channel:    Delivery channel — "voice" | "whatsapp" | "telegram" | "default".

        Returns:
            Formatted string ready to send on the specified channel.
        """
        channel = channel.lower().strip()
        max_len = _CHANNEL_MAX_LEN.get(channel, _CHANNEL_MAX_LEN["default"])

        # Apply channel-specific formatting first
        formatted = self._format_for_channel(raw_output, channel)

        # Then apply tone persona on top
        formatted = self._apply_tone(formatted, tone, channel)

        # Enforce length limit per channel
        return _truncate(formatted, max_len)

    # ── Channel formatters ────────────────────────────────────────────────────

    def _format_for_channel(self, text: str, channel: str) -> str:
        """Apply channel-specific text transformations."""

        if channel == "voice":
            # Strip all markdown — TTS engines read symbols aloud otherwise
            text = _strip_markdown(text)
            # Convert bullet lists to comma-separated sentences
            text = re.sub(r"\n\s*[-•]\s*", ", ", text)
            # Remove leftover newlines
            text = text.replace("\n", " ").strip()
            return text

        if channel == "whatsapp":
            # WhatsApp supports *bold* and _italic_ natively
            # Keep text short — strip heavy markdown like headers
            text = re.sub(r"#+\s", "", text)            # strip headers
            text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)  # strip code blocks
            text = text.strip()
            return text

        if channel == "telegram":
            # Telegram supports MarkdownV2 — keep *bold* and basic structure
            # but strip code blocks that may break the bubble
            text = re.sub(r"```.*?```", "`code`", text, flags=re.DOTALL)
            return text.strip()

        # default — return as-is
        return text.strip()

    # ── Tone personas ─────────────────────────────────────────────────────────

    def _apply_tone(self, text: str, tone: str, channel: str) -> str:
        """Wrap the formatted text in the requested persona style."""

        if tone == "hinglish":
            # Voice calls from the owner often use Hinglish — casual and warm
            return f"Bhai, ye lo: {text}. Done hai na? 😄"

        if tone == "friendly":
            if channel == "voice":
                return f"Here you go! {text}. Let me know if you need anything else!"
            return f"Hey! Here's what I found: {text} 😊 Anything else?"

        # Default: professional — clean, no fluff
        return text
