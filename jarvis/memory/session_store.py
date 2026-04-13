"""
memory/session_store.py
───────────────────────
Unified session registry that keeps conversation context consistent
regardless of whether the user contacts JARVIS via WhatsApp, Telegram,
or a phone call.

Key design:
  • Primary key is the caller's phone number (E.164) or Telegram user ID.
  • The session record stores the last-known channel, interaction count,
    and an ISO timestamp of the last activity.
  • get_or_create_session() returns a stable session_id string that the
    MemoryAgent uses as the database foreign key — so all channels share
    the same conversation history for a given user.

Storage: SQLite via MemoryAgent (add_interaction / get_recent_interactions).
State:   In-memory dict protected by a threading.Lock for thread safety.
"""

import logging
import threading
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── In-memory session map ─────────────────────────────────────────────────────
# Maps a normalised user identifier → session metadata dict
# This is intentionally lightweight and not persisted to disk because the
# actual conversation history is already stored in the SQLite Interaction table.
_sessions: Dict[str, dict] = {}
_lock = threading.Lock()


def _normalise_identifier(identifier: str) -> str:
    """
    Produce a canonical key from any user identifier.

    Strips Twilio prefixes (whatsapp:, voice:) and normalises whitespace.
    Result is the bare phone number or Telegram ID string.
    """
    return (
        identifier
        .strip()
        .replace("whatsapp:", "")
        .replace("voice:", "")
        .replace(" ", "")
    )


def get_or_create_session(
    identifier: str,
    channel: str = "default",
) -> str:
    """
    Return the stable session_id for a given user identifier.

    If no session exists, a new one is created automatically.  The channel
    hint is recorded so we know where the user last contacted us.

    Args:
        identifier: Phone number (E.164) or Telegram user ID string.
        channel:    "whatsapp" | "telegram" | "voice" | "default"

    Returns:
        A session_id string of the form "session_<normalised_id>".
    """
    key = _normalise_identifier(identifier)
    session_id = f"session_{key}"

    with _lock:
        if key not in _sessions:
            # First contact from this user — create a fresh session record
            _sessions[key] = {
                "session_id":        session_id,
                "first_seen":        datetime.utcnow().isoformat(),
                "last_seen":         datetime.utcnow().isoformat(),
                "last_channel":      channel,
                "interaction_count": 0,
            }
            logger.info(
                f"[SessionStore] New session created for identifier='{key}' "
                f"via channel='{channel}'"
            )
        else:
            # Returning user — update channel and timestamp
            _sessions[key]["last_seen"] = datetime.utcnow().isoformat()
            _sessions[key]["last_channel"] = channel
            _sessions[key]["interaction_count"] += 1
            logger.debug(
                f"[SessionStore] Resumed session '{session_id}' "
                f"(count={_sessions[key]['interaction_count']}, channel={channel})"
            )

    return session_id


def get_session_info(identifier: str) -> Optional[dict]:
    """Return the full session metadata for a user, or None if not seen before."""
    key = _normalise_identifier(identifier)
    with _lock:
        return _sessions.get(key)


def list_active_sessions() -> Dict[str, dict]:
    """Return a snapshot of all in-memory sessions — for observability."""
    with _lock:
        return dict(_sessions)
