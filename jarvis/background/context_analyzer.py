"""
background/context_analyzer.py
──────────────────────────────
Maps raw OS events/titles into high-level semantic context.
Combines active window string matching with time and memory.
"""

import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class ContextAnalyzer:
    """Analyzes raw OS activity to determine user state and app context."""

    def analyze(self, activity: Dict[str, Any], memory) -> Dict[str, Any]:
        """
        Produce a context object from activity details.
        
        Outputs:
          state: idle | working | starting_day
          app_context: coding | browsing | unknown
          recent_goal: string or None
        """
        state = "working"
        app_context = "unknown"
        
        # ── 1. Infer App Context ──────────────────────────────────────────────
        app_title = activity.get("app", "").lower()
        if any(w in app_title for w in ["visual studio code", "cursor", "pycharm", "intellij", "sublime"]):
            app_context = "coding"
        elif any(w in app_title for w in ["chrome", "edge", "firefox", "safari"]):
            app_context = "browsing"
            
        # ── 2. Infer State ────────────────────────────────────────────────────
        if activity["event"] == "idle":
            state = "idle"
            
        hour = datetime.now().hour
        # starting_day: First unlock of the day or early morning start
        if activity["event"] == "unlock" and 5 <= hour <= 11:
            state = "starting_day"

        # ── 3. Query Memory for Goals ─────────────────────────────────────────
        recent_goal = None
        try:
            pending = memory.get_pending_tasks()
            if pending and len(pending) > 0:
                recent_goal = pending[0].description
        except Exception as e:
            logger.debug(f"[ContextAnalyzer] Could not query memory goals: {e}")

        # Construct final context snapshot
        return {
            "state": state,
            "app_context": app_context,
            "recent_goal": recent_goal,
            "confidence": 0.8 if app_context != "unknown" else 0.5,
            "raw_activity": activity
        }
