"""
background/activity_monitor.py
───────────────────────────────
Monitors native OS usage (Windows) to track user context.
Watches: active window title and keyboard/mouse idle time.
Detects state shifts like "unlock" or "app_change".
"""

import ctypes
import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# ctypes structures for Windows API idle tracking
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_time_seconds() -> int:
    """Returns number of seconds since the last keyboard/mouse movement."""
    try:
        lastInputInfo = LASTINPUTINFO()
        lastInputInfo.cbSize = ctypes.sizeof(lastInputInfo)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lastInputInfo)):
            millis = ctypes.windll.kernel32.GetTickCount() - lastInputInfo.dwTime
            return int(millis / 1000.0)
    except Exception as e:
        logger.debug(f"[ActivityMonitor] Idle tracking error: {e}")
    return 0


def get_active_window_title() -> str:
    """Returns the title of the currently focused OS window."""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value if buf.value else "unknown"
    except Exception as e:
        logger.debug(f"[ActivityMonitor] Window tracking error: {e}")
        return "unknown"


class ActivityMonitor:
    """Stateful monitor that tracks deltas to fire context events."""
    
    def __init__(self):
        self.last_idle: int = 0
        self.last_app: str = ""

    def get_current_activity(self) -> Dict[str, Any]:
        """Polls current OS state and compares with previous to generate events."""
        app = get_active_window_title()
        idle_seq = get_idle_time_seconds()

        event = "active"
        if idle_seq > 300:  # 5 minutes
            event = "idle"

        if app != self.last_app and app != "unknown" and app != "":
            event = "app_change"

        # Detect resume/unlock: idle drops drastically from >10 mins (600s) to <5s
        if self.last_idle > 600 and idle_seq < 5:
            event = "unlock"

        self.last_idle = idle_seq
        self.last_app = app

        return {
            "event": event,
            "app": app,
            "idle_seconds": idle_seq,
            "timestamp": datetime.utcnow().isoformat()
        }
