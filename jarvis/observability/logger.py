import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List
from threading import Lock

from config.settings import settings

class ExecutionLogger:
    def __init__(self):
        self.log_file = os.path.join(settings.workspace_dir, "execution.log")
        self.lock = Lock()
        self._ensure_log_exists()

    def _ensure_log_exists(self):
        if not os.path.exists(self.log_file):
            with open(self.log_file, "w", encoding="utf-8") as f:
                pass

    def log_event(self, event_type: str, details: Dict[str, Any]):
        """Logs structured JSON events for observability."""
        try:
            entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": event_type,
                "details": details
            }
            log_line = json.dumps(entry) + "\n"
            
            with self.lock:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(log_line)
                    
            # Also emit over standard logging for fast console access
            logging.info(f"[ExecutionLogger] {event_type} | {details}")
        except Exception as e:
            logging.error(f"Failed to write execution log: {e}")

    def read_recent_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Reads the last N structured log entries directly from the JSON log."""
        try:
            with self.lock:
                with open(self.log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            
            # Grab bottom N lines
            recent = lines[-limit:]
            
            parsed = []
            for line in recent:
                line = line.strip()
                if line:
                    try:
                        parsed.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            
            return parsed
        except Exception as e:
            logging.error(f"Failed to read execution log: {e}")
            return []

# Singleton instance
structured_logger = ExecutionLogger()
