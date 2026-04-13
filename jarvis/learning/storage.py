"""
learning/storage.py
────────────────────
Storage abstractions for the Behavioral Modeling system.
Ensures we can swap JSON-backends for PostgreSQL later without refactoring
the core logic of the logger and pattern extractor.
"""

import json
import os
import logging

try:
    import fcntl
except ImportError:
    fcntl = None

from abc import ABC, abstractmethod
from typing import List, Dict, Any

from config.settings import settings

logger = logging.getLogger(__name__)

# ─── Interfaces ──────────────────────────────────────────────────────────────

class EventStore(ABC):
    """Abstract store for logging execution lifecycle events."""
    @abstractmethod
    def save_event(self, event: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def get_events(self) -> List[Dict[str, Any]]:
        pass


class WorkflowStore(ABC):
    """Abstract store for abstracted, normalized workflows."""
    @abstractmethod
    def save(self, workflow: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def load_all(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def find_by_intent(self, intent: str) -> List[Dict[str, Any]]:
        pass


# ─── JSON Implementations ────────────────────────────────────────────────────

# Core utility to safely append/read JSON lists with naive file locking
def _atomic_json_append(file_path: str, item: Dict[str, Any]):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if not os.path.exists(file_path):
        with open(file_path, "w") as f:
            json.dump([], f)
    
    with open(file_path, "r+") as f:
        # Note: fcntl is Unix only. On Windows, we gracefully fallback.
        try:
            if fcntl and os.name == "posix":
                fcntl.flock(f, fcntl.LOCK_EX)
            data = json.load(f)
            data.append(item)
            f.seek(0)
            f.truncate()
            json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[Storage] File lock/write error: {e}")
        finally:
            if fcntl and os.name == "posix":
                fcntl.flock(f, fcntl.LOCK_UN)

def _read_json_list(file_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r") as f:
        try:
            return json.load(f)
        except Exception:
            return []


class JSONEventStore(EventStore):
    def __init__(self, file_path: str = None):
        self.file_path = file_path or os.path.join(settings.workspace_dir, "learning_events.json")

    def save_event(self, event: Dict[str, Any]) -> None:
        _atomic_json_append(self.file_path, event)

    def get_events(self) -> List[Dict[str, Any]]:
        return _read_json_list(self.file_path)


class JSONWorkflowStore(WorkflowStore):
    def __init__(self, file_path: str = None):
        self.file_path = file_path or os.path.join(settings.workspace_dir, "learned_workflows.json")

    def save(self, workflow: Dict[str, Any]) -> None:
        # For simplicity, we append or update.
        # Let's read all, replace if workflow_name matches, else append.
        items = self.load_all()
        updated = False
        for i, item in enumerate(items):
            if item.get("workflow_name") == workflow.get("workflow_name"):
                items[i] = workflow
                updated = True
                break
        if not updated:
            items.append(workflow)

        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        with open(self.file_path, "w") as f:
            if fcntl and os.name == "posix":
                fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(items, f, indent=2)
            if fcntl and os.name == "posix":
                fcntl.flock(f, fcntl.LOCK_UN)

    def load_all(self) -> List[Dict[str, Any]]:
        return _read_json_list(self.file_path)

    def find_by_intent(self, intent: str) -> List[Dict[str, Any]]:
        items = self.load_all()
        # Basic keyword match on intent embedded within workflow context tracking
        matches = []
        intent_lower = intent.lower()
        for w in items:
            associated_intents = w.get("associated_intents", [])
            for w_intent in associated_intents:
                # Basic token overlap
                w_tokens = set(w_intent.lower().split())
                i_tokens = set(intent_lower.split())
                if len(w_tokens & i_tokens) >= 1:  # Simple 1-word overlap for demonstration
                    matches.append(w)
                    break 
        return matches

# Instantiated singletons
event_store = JSONEventStore()
workflow_store = JSONWorkflowStore()
