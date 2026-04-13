import time
import logging
from functools import wraps

logger = logging.getLogger(__name__)

class SystemMonitor:
    def __init__(self):
        self.metrics = {"tokens_used": 0, "api_calls": 0, "tasks_executed": 0}

    def track_cost(self, model_name: str = "gpt-4o", tokens_est: int = 150):
        # Decorator tracking LLM interactions globally shielding rogue autonomous loops
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                self.metrics["api_calls"] += 1
                self.metrics["tokens_used"] += tokens_est
                
                # Dynamic timeout & cost limits mapping:
                if self.metrics["tokens_used"] > 50000:
                    logger.warning(f"[Monitor] Token hard-limit reached globally! Halting API request.")
                    raise Exception("SYSTEM_LIMIT_REACHED: Token ceiling exceeded")
                    
                start_time = time.time()
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                
                logger.info(f"[Monitor] Execution {func.__name__} took {duration:.2f}s. Total tokens so far: {self.metrics['tokens_used']}")
                return result
            return wrapper
        return decorator

monitor_api = SystemMonitor()
