"""
background/trigger_engine.py
────────────────────────────
Evaluates context against decision rules to figure out if/when JARVIS should 
proactively interrupt the user. Applies strict silence (cooldown) logic.
"""

import time
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class TriggerEngine:
    """Decision logic layer for proactive interactions."""

    def __init__(self, cooldown_seconds: int = 600):
        self.last_trigger_time: float = 0.0
        self.cooldown_seconds: int = cooldown_seconds

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate context to decide whether a proactive prompt should fire.
        Returns a trigger dictionary containing 'should_trigger' and string hints.
        """
        trigger = {
            "should_trigger": False,
            "trigger_type": "",
            "message_hint": ""
        }
        
        now = time.time()
        activity = context.get("raw_activity", {})
        app_title = activity.get("app", "").lower()
        idle_secs = activity.get("idle_seconds", 0)

        # ── 🔇 Silence Logic ───────────────────────────────────────────────
        
        # 1. Do not interrupt if actively typing/working (idle < 2s) unless it's a new app or unlock.
        if idle_secs < 2 and activity.get("event") not in ["app_change", "unlock"]:
            return trigger

        # 2. Block during meetings/calls (highly disruptive to interrupt)
        call_keywords = ["zoom", "meet", "teams", "webex", "skype", "slack huddle", "discord"]
        if any(keyword in app_title for keyword in call_keywords):
            return trigger

        # 3. Cooldown logic (don't spam the user)
        if now - self.last_trigger_time < self.cooldown_seconds:
            return trigger

        # ── 🔔 Trigger Logic ──────────────────────────────────────────────
        
        # Rule 1: Laptop just opened / Start of day
        if context["state"] == "starting_day":
            trigger["should_trigger"] = True
            trigger["trigger_type"] = "greet"
            trigger["message_hint"] = "Good morning. What are you planning today?"

        # Rule 2: Idle Prompt
        # Activity monitor signals 'unlock' when idle drops massively gracefully.
        elif activity.get("event") == "unlock":
            trigger["should_trigger"] = True
            trigger["trigger_type"] = "idle_prompt"
            trigger["message_hint"] = "Welcome back. Want to resume where you left off?"

        # Rule 3: Work Context Trigger
        elif context["app_context"] == "coding" and activity.get("event") == "app_change":
            trigger["should_trigger"] = True
            trigger["trigger_type"] = "work_prompt"
            trigger["message_hint"] = "Want to continue your current project?"
            
            # Rule 4: Pending Task injection
            if context.get("recent_goal"):
                trigger["message_hint"] = f"You had planned '{context['recent_goal']}'. Want to start?"

        # ── Commit the trigger ─────────────────────────────────────────────
        if trigger["should_trigger"]:
            self.last_trigger_time = now
            logger.info(f"[TriggerEngine] Firing proactive trigger: {trigger['trigger_type']}")

        return trigger
