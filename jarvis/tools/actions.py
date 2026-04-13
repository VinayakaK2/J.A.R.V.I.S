import os
import re
import logging
import subprocess
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

# ── Workspace safety helper ───────────────────────────────────────────────────

# Resolve a user-supplied filename to a safe absolute path inside workspace.
# Raises ValueError if the resolved path escapes the sandbox.
def _safe_workspace_path(name: str) -> Path:
    workspace = Path(settings.workspace_dir).resolve()
    # Strip leading slashes / drive letters that could escape the sandbox
    safe_name = os.path.basename(name.replace("\\", "/"))
    resolved = (workspace / safe_name).resolve()
    # commonpath check prevents any path traversal
    if not str(resolved).startswith(str(workspace)):
        raise ValueError(f"Path '{name}' escapes the workspace sandbox.")
    return resolved


# ─── File Tools ───────────────────────────────────────────────────────────────

# Create a new file with the given content inside the sandboxed workspace
def create_file(name: str, content: str) -> str:
    path = _safe_workspace_path(name)
    path.write_text(content, encoding="utf-8")
    logger.info(f"[Tool:create_file] Created {path.name}")
    return f"File '{path.name}' created successfully in workspace."

# Read and return the contents of a file from the sandboxed workspace
def read_file(name: str) -> str:
    path = _safe_workspace_path(name)
    if not path.exists():
        return f"Error: File '{path.name}' not found in workspace."
    content = path.read_text(encoding="utf-8")
    logger.info(f"[Tool:read_file] Read {path.name} ({len(content)} chars)")
    return content


# ─── Web Search Tool ──────────────────────────────────────────────────────────

# Search the web via DuckDuckGo and return the top results as plain text
def search_web(query: str) -> str:
    try:
        from duckduckgo_search import DDGS

        results = []
        # Fetch up to 5 results with titles, urls, and snippets
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=5):
                results.append(f"• {r['title']}\n  {r['href']}\n  {r['body']}")

        if not results:
            return f"No results found for: {query}"

        formatted = f"Search results for '{query}':\n\n" + "\n\n".join(results)
        logger.info(f"[Tool:search_web] Got {len(results)} results for '{query}'")
        return formatted

    except ImportError:
        return "Error: duckduckgo-search package not installed. Run: pip install duckduckgo-search"
    except Exception as e:
        logger.error(f"[Tool:search_web] Failed: {e}")
        return f"Web search failed: {str(e)}"


# ─── WhatsApp Tool (Twilio) ───────────────────────────────────────────────────

# Send a WhatsApp message via Twilio's sandbox or approved sender number.
# Requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER in .env
def send_whatsapp(number: str, message: str) -> str:
    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    from_num = settings.twilio_whatsapp_number

    # Check credentials are configured
    if not sid or sid.startswith("your_"):
        logger.warning("[Tool:send_whatsapp] Twilio credentials not configured — using mock.")
        print(f"[MOCK WhatsApp] To: {number} | Message: {message}")
        return f"[MOCK] WhatsApp message queued for {number}."

    try:
        from twilio.rest import Client

        # Normalize destination number to whatsapp: prefix
        to_num = f"whatsapp:{number}" if not number.startswith("whatsapp:") else number

        client = Client(sid, token)
        msg = client.messages.create(body=message, from_=from_num, to=to_num)
        logger.info(f"[Tool:send_whatsapp] Sent SID={msg.sid} to {to_num}")
        return f"WhatsApp message sent to {number}. SID: {msg.sid}"

    except ImportError:
        return "Error: twilio package not installed. Run: pip install twilio"
    except Exception as e:
        logger.error(f"[Tool:send_whatsapp] Twilio error: {e}")
        return f"WhatsApp send failed: {str(e)}"


# ─── Telegram Tool ────────────────────────────────────────────────────────────

# Send a Telegram message via the Bot API using a chat_id.
# Requires TELEGRAM_BOT_TOKEN in .env. chat_id is the recipient's Telegram user/group ID.
def send_telegram(chat_id: str, message: str) -> str:
    token = settings.telegram_bot_token

    if not token or token.startswith("your_"):
        logger.warning("[Tool:send_telegram] Telegram token not configured — using mock.")
        print(f"[MOCK Telegram] chat_id={chat_id} | Message: {message}")
        return f"[MOCK] Telegram message queued for chat_id={chat_id}."

    try:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}

        with httpx.Client(timeout=10) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        msg_id = data.get("result", {}).get("message_id", "?")
        logger.info(f"[Tool:send_telegram] Sent message_id={msg_id} to chat_id={chat_id}")
        return f"Telegram message sent to chat_id={chat_id}. Message ID: {msg_id}"

    except ImportError:
        return "Error: httpx package not installed. Run: pip install httpx"
    except Exception as e:
        logger.error(f"[Tool:send_telegram] Error: {e}")
        return f"Telegram send failed: {str(e)}"


# ─── Code Sandbox Tool ────────────────────────────────────────────────────────

# Execute a Python code snippet in an isolated subprocess within the workspace.
# Execution is time-limited to 10 seconds to prevent runaway processes.
def run_code_sandbox(code: str) -> str:
    path = _safe_workspace_path("_sandbox_exec.py")
    path.write_text(code, encoding="utf-8")

    try:
        result = subprocess.run(
            ["python", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(Path(settings.workspace_dir).resolve()),
        )
        output = result.stdout.strip() or result.stderr.strip()
        logger.info(f"[Tool:run_code_sandbox] Completed (exit={result.returncode})")
        return output if output else "(No output produced)"

    except subprocess.TimeoutExpired:
        return "Error: Code execution timed out after 10 seconds."
    except Exception as e:
        logger.error(f"[Tool:run_code_sandbox] Error: {e}")
        return f"Code execution failed: {str(e)}"
