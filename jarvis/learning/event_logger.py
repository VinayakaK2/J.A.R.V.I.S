"""
learning/event_logger.py
────────────────────────
Filters and logs high-value execution events representing the full task lifecycle.

Events captured (high-value only — NO raw UI/mouse noise):
  - task_start: initial intent normalized to canonical form
  - plan_generated: the resolved tool sequence
  - tool_usage: per-step structured execution
  - task_failure: failure at tool level with optional reason + context
  - task_end: final success/failure outcome
"""

from typing import Dict, Any, List, Optional
from datetime import datetime

from learning.storage import event_store
from learning.intent_normalizer import normalize_intent


class EventLogger:
    """Singleton lifecycle logger for the Behavioral Modeling pipeline."""

    def log_task_start(self, session_id: str, task_id: str, intent: str):
        """Logs the beginning of a task and stores the canonical intent for grouping."""
        canonical = normalize_intent(intent)
        payload = {
            "event_type": "task_start",
            "session_id": session_id,
            "task_id": task_id,
            "intent": intent,               # Raw intent preserved for display
            "canonical_intent": canonical,  # Normalized form used for clustering
            "timestamp": datetime.utcnow().isoformat(),
        }
        event_store.save_event(payload)

    def log_plan_generated(
        self, session_id: str, task_id: str, steps: List[Dict[str, Any]]
    ):
        """Logs the resolved tool execution plan immediately after planning."""
        payload = {
            "event_type": "plan_generated",
            "session_id": session_id,
            "task_id": task_id,
            "steps": steps,
            "timestamp": datetime.utcnow().isoformat(),
        }
        event_store.save_event(payload)

    def log_tool_usage(
        self, session_id: str, task_id: str, tool: str, action: str,
        params_template: Optional[Dict[str, str]] = None,
    ):
        """
        Logs a successfully completed tool step.
        params_template contains placeholder names for parameterization,
        NOT actual param values (to preserve privacy).
        """
        payload = {
            "event_type": "tool_usage",
            "session_id": session_id,
            "task_id": task_id,
            "tool": tool,
            "action": action,
            "params_template": params_template or {},  # e.g. {"query": "{search_term}"}
            "timestamp": datetime.utcnow().isoformat(),
        }
        event_store.save_event(payload)

    def log_task_failure(
        self,
        session_id: str,
        task_id: str,
        tool: str,
        failure_reason: Optional[str] = None,
    ):
        """
        Logs a tool-level failure event including the failure reason.
        This feeds the failure pattern detection pipeline so JARVIS
        avoids repeating the same failing sequences in future workflows.
        """
        payload = {
            "event_type": "task_failure",
            "session_id": session_id,
            "task_id": task_id,
            "tool": tool,
            "failure_reason": failure_reason or "unknown",
            "timestamp": datetime.utcnow().isoformat(),
        }
        event_store.save_event(payload)

    def log_task_end(self, session_id: str, task_id: str, success: bool):
        """Logs the final outcome of the entire plan execution."""
        payload = {
            "event_type": "task_end",
            "session_id": session_id,
            "task_id": task_id,
            "success": success,
            "timestamp": datetime.utcnow().isoformat(),
        }
        event_store.save_event(payload)


workflow_logger = EventLogger()

