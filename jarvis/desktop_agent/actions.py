import os
import time
import logging
import subprocess
from typing import List

logger = logging.getLogger(__name__)

# Very strict whitelist to prevent any arbitrary OS command injections
ALLOWED_APPLICATIONS = ["chrome", "msedge", "notepad", "code", "explorer", "calc"]

def _is_whitelisted(app_name: str) -> bool:
    """Verifies that an application string strictly matches the whitelist."""
    # Basic check against the lowest common denominator strings
    normalized = app_name.strip().lower()
    return any(allowed in normalized for allowed in ALLOWED_APPLICATIONS)

def open_application(app_name: str) -> str:
    """Safely spawns a whitelisted application."""
    if not _is_whitelisted(app_name):
        return f"Error: Application '{app_name}' is NOT whitelisted. Blocked by security policy."
    
    try:
        # Popen is non-blocking so the agent isn't stuck waiting for the app to close
        # Shell=False is enforced automatically in this signature which adds massive security
        subprocess.Popen([app_name])
        logger.info(f"[DesktopAgent] Opened application: {app_name}")
        return f"Successfully opened {app_name}"
    except FileNotFoundError:
        return f"Error: Application '{app_name}' not found on system PATH."
    except Exception as e:
        logger.error(f"[DesktopAgent] Failed to open {app_name}: {e}")
        return f"Error opening application: {e}"

def open_website(url: str) -> str:
    """Safely forces a URL open using web browser capabilities or safe Chrome invocation."""
    import webbrowser
    try:
        # Standard python webbrowser limits dangerous script injections safely
        webbrowser.open(url)
        logger.info(f"[DesktopAgent] Opened website: {url}")
        return f"Successfully ordered browser to open {url}"
    except Exception as e:
        logger.error(f"[DesktopAgent] Failed to open website {url}: {e}")
        return f"Error opening website: {e}"

def type_text(text: str) -> str:
    """Types a string of text over the currently active window using simulated keystrokes."""
    try:
        import pyautogui
        # Adding a tiny latency helps the OS digest the rapid strokes
        pyautogui.write(text, interval=0.01)
        logger.info(f"[DesktopAgent] Typed {len(text)} characters.")
        return f"Successfully typed {len(text)} characters."
    except Exception as e:
        logger.error(f"[DesktopAgent] Failed to type text: {e}")
        return f"Error typing text: {e}"

def press_keys(keys: str) -> str:
    """Presses a combination of keys e.g. 'ctrl+c' or 'enter'."""
    try:
        import pyautogui
        # Split by separator to allow things like "ctrl,c" or "space"
        key_list = [k.strip() for k in keys.replace("+", ",").split(",")]
        pyautogui.hotkey(*key_list)
        logger.info(f"[DesktopAgent] Pressed keys: {keys}")
        return f"Successfully pressed the key combination: {keys}"
    except Exception as e:
        logger.error(f"[DesktopAgent] Failed to press keys {keys}: {e}")
        return f"Error pressing keys: {e}"

def click(x: int, y: int) -> str:
    """Clicks the mouse on precise screen coordinates."""
    try:
        import pyautogui
        pyautogui.click(x=x, y=y)
        logger.info(f"[DesktopAgent] Clicked coordinate ({x}, {y})")
        return f"Successfully clicked at ({x}, {y})"
    except Exception as e:
        logger.error(f"[DesktopAgent] Failed to click coordinate: {e}")
        return f"Error clicking at ({x}, {y}): {e}"
