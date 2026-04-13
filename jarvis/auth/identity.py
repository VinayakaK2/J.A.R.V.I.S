"""
auth/identity.py
────────────────
Defines the OWNER profile and provides role-resolution helpers used by the
guard, orchestrator, and all external channel webhooks.

Role model:
  "owner" → full system access (desktop, files, messaging, background tasks)
  "guest" → only safe, read-only tools (web search, general Q&A)

Credentials are read from config.settings (backed by .env) so they are
never hard-coded in source and are validated at startup.
"""

import logging
from typing import Optional

from config.settings import settings  # Single source of truth for all env vars

logger = logging.getLogger(__name__)

# ── Owner profile (populated lazily from validated pydantic Settings) ─────────
# Accessing settings here (module level) is safe — pydantic-settings loads .env
# before any request code runs.

OWNER: dict = {
    # E.164 phone number — used to match Twilio WhatsApp/Voice callers
    "phone":       settings.owner_phone,
    # Telegram numeric user ID — obtained via @userinfobot
    "telegram_id": settings.owner_telegram_id,
    # Human-readable name used in personalised responses
    "name":        settings.owner_name,
}

# ── Role definitions ──────────────────────────────────────────────────────────

# Full set of tools the owner is allowed to use (all tools not in GUEST_TOOLS)
OWNER_ROLE = "owner"

# Guests may only access these safe, non-destructive tools
GUEST_ROLE = "guest"

# Tools that a guest is explicitly allowed to invoke
GUEST_SAFE_TOOLS: set = {
    "search_web",
    "general_query",       # LLM Q&A without any side-effects
}

# ── Normalisation helpers ─────────────────────────────────────────────────────

def _normalise_phone(raw: str) -> str:
    """Strip whitespace and ensure E.164 format with leading +."""
    cleaned = raw.strip().replace(" ", "").replace("-", "")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


# ── Role resolution ───────────────────────────────────────────────────────────

def get_user_role(identifier: str) -> str:
    """
    Determine whether the caller is the owner or a guest.

    Accepts a phone number (E.164), a Telegram user ID string, or a
    composite session key produced by the webhook handlers.  Returns
    OWNER_ROLE or GUEST_ROLE.
    """
    if not identifier:
        logger.warning("[Identity] Empty identifier supplied — treating as guest.")
        return GUEST_ROLE

    identifier = identifier.strip()

    # Match numeric Telegram ID directly
    if identifier == str(OWNER["telegram_id"]):
        logger.info(f"[Identity] Telegram owner recognised: {identifier}")
        return OWNER_ROLE

    # Normalise phone numbers for WhatsApp / Voice matching
    # Twilio prefixes them with "whatsapp:" — strip it before comparing
    phone_candidate = identifier.replace("whatsapp:", "").replace("voice:", "")
    try:
        normalised = _normalise_phone(phone_candidate)
        owner_phone = _normalise_phone(OWNER["phone"])
        if normalised == owner_phone:
            logger.info(f"[Identity] Phone owner recognised: {normalised}")
            return OWNER_ROLE
    except Exception:
        pass  # Non-phone identifier — fall through to guest

    logger.info(f"[Identity] Unrecognised identifier '{identifier}' — treating as guest.")
    return GUEST_ROLE


def is_owner(identifier: str) -> bool:
    """Convenience wrapper — returns True when the caller is the owner."""
    return get_user_role(identifier) == OWNER_ROLE


def get_owner_name() -> str:
    """Return the owner's human-readable name for use in personalised greetings."""
    return OWNER["name"]


def get_user_info(identifier: str) -> dict:
    """
    Return a structured user context dict for use in the orchestrator.

    Fields:
      role        — "owner" or "guest"
      identifier  — original identifier string
      name        — display name if owner, "Guest" otherwise
    """
    role = get_user_role(identifier)
    return {
        "role":       role,
        "identifier": identifier,
        "name":       OWNER["name"] if role == OWNER_ROLE else "Guest",
    }
