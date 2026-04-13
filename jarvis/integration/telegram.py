"""
integration/telegram.py
────────────────────────
Telegram Bot webhook handler.

Flow:
  1. Telegram POSTs an Update JSON to /webhook/telegram.
  2. We extract chat_id and the incoming text.
  3. Resolve the caller's role via auth.identity (owner vs. guest).
  4. Route to the orchestrator with channel="telegram".
  5. Send the formatted reply back via the bot API (send_telegram tool).

Setup:
  Register the webhook with Telegram once your server is public:
  https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<host>/webhook/telegram

Dependencies:
  httpx — used by tools/actions.send_telegram
"""

import logging
from typing import Optional

from fastapi import APIRouter, Request, Response

from auth.identity import get_user_info
from tools.actions import send_telegram

logger = logging.getLogger(__name__)

# ── Router exported to main.py ────────────────────────────────────────────────
telegram_router = APIRouter(tags=["Telegram"])


# ─── Webhook endpoint ─────────────────────────────────────────────────────────

@telegram_router.post("/webhook/telegram")
async def telegram_webhook(request: Request) -> Response:
    """
    Receive an Update from Telegram's Bot API and respond via JARVIS.

    Telegram sends JSON payloads.  We acknowledge every update with HTTP 200
    immediately; actual processing is synchronous inside this handler.
    """
    # Parse the raw update payload from Telegram
    try:
        update = await request.json()
    except Exception as e:
        logger.error(f"[Telegram] Failed to parse update payload: {e}")
        return Response(status_code=200)  # Always 200 to Telegram — never 4xx

    # Extract message object — Telegram may also send edited_message, etc.
    message: Optional[dict] = update.get("message") or update.get("edited_message")
    if not message:
        # Not a text message update (e.g. channel post, callback_query) — ignore
        logger.debug("[Telegram] Non-message update received, skipping.")
        return Response(status_code=200)

    chat_id: str = str(message.get("chat", {}).get("id", ""))
    text: str = (message.get("text") or "").strip()

    # Ignore empty texts (e.g. stickers / media only messages)
    if not text or not chat_id:
        logger.debug("[Telegram] Empty text or missing chat_id — skipping.")
        return Response(status_code=200)

    logger.info(f"[Telegram] Incoming from chat_id={chat_id}: '{text}'")

    # ── Identity & role resolution ────────────────────────────────────────────
    # Resolve the caller's role using their Telegram user ID
    telegram_user_id: str = str(
        message.get("from", {}).get("id", chat_id)
    )
    user_info = get_user_info(telegram_user_id)
    role = user_info["role"]
    logger.info(f"[Telegram] Resolved role='{role}' for user_id={telegram_user_id}")

    # ── Route to JARVIS orchestrator ──────────────────────────────────────────
    # Import here to avoid circular imports at module load time
    from orchestrator import OrchestratorAgent

    orchestrator: OrchestratorAgent = request.app.state.orchestrator

    # Use a stable session key so conversation memory persists per Telegram user
    session_id = f"telegram_{chat_id}"

    try:
        reply = orchestrator.process_request(
            session_id=session_id,
            user_input=text,
            tone="professional",
            channel="telegram",
            role=role,
        )
    except Exception as e:
        logger.exception(f"[Telegram] Orchestrator error: {e}")
        reply = "Something went wrong on my end. Please try again shortly."

    # Handle None reply (scheduled task was queued)
    if reply is None:
        reply = "✅ Got it! Your task has been queued and will run at the scheduled time."

    # ── Send reply back via Telegram Bot API ──────────────────────────────────
    try:
        send_telegram(chat_id=chat_id, message=reply)
    except Exception as e:
        logger.error(f"[Telegram] Failed to send reply to chat_id={chat_id}: {e}")

    # Always return 200 so Telegram doesn't retry the update
    return Response(status_code=200)
