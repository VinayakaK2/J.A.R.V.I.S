import logging
from datetime import datetime
from typing import Dict, Any, List

from memory.db import SessionLocal
from config.settings import settings

logger = logging.getLogger(__name__)

class DistributedTracer:
    """
    Maintains a robust causal chain across distributed task execution:
    request_id -> plan_id -> step_id -> task_id
    
    Persists events to structured logs locally or stdout so SIEM monitors can parse them cleanly.
    """
    def log_transition(self, 
                       request_id: str, 
                       plan_id: str, 
                       status_from: str, 
                       status_to: str, 
                       step_id: str = None, 
                       task_id: str = None, 
                       metadata: Dict[str, Any] = None):
        
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "request_id": request_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "task_id": task_id,
            "transition": f"{status_from} -> {status_to}",
            "metadata": metadata or {}
        }
        
        # Output clean JSON structured line natively for observability backends (e.g. Datadog, ELK)
        import json
        logger.info(f"[TRACE] {json.dumps(event)}")
        
        # We also keep it in SQLite/PG structured_logger if we want API retrieval
        from observability.logger import structured_logger
        structured_logger.log_event("STATE_TRANSITION", event)

tracer = DistributedTracer()
