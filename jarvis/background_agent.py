import asyncio
import logging
from datetime import datetime
from typing import Dict, Any

from memory.manager import MemoryAgent
from memory.db import SessionLocal, EventTrigger, ScheduledTask
from observability.logger import structured_logger
import json

from rq import Queue
from redis import Redis
import os

from background.activity_monitor import ActivityMonitor
from background.context_analyzer import ContextAnalyzer
from background.trigger_engine import TriggerEngine

logger = logging.getLogger(__name__)
redis_conn = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/"))
task_queue = Queue("jarvis_tasks", connection=redis_conn)

# Execution payload sent to Redis Worker
def execute_deferred_task(session_id: str, tool: str, params: Dict[str, Any], task_id: int):
    # This runs inside `worker.py` implicitly because RQ unpickles it
    from executor import ToolExecutorAgent
    from planner import PlanStep
    executor = ToolExecutorAgent()
    step = PlanStep(step_id=task_id, tool=tool, action="Background execution", params=params, expected_outcome="Background operation successful")
    
    # Track metrics
    from system.monitor import monitor_api
    @monitor_api.track_cost(tokens_est=50)
    def wrapped_execute():
        return executor._execute_step(step, "Background Autonomous Task")
    
    return wrapped_execute()

class BackgroundAgent:
    def __init__(self, poll_interval: int = 5):
        self.poll_interval = poll_interval
        self._running = False
        self.memory = MemoryAgent()
        
        # Proactive Assistant Components
        self.monitor = ActivityMonitor()
        self.analyzer = ContextAnalyzer()
        self.trigger_engine = TriggerEngine(cooldown_seconds=600)

    async def run(self) -> None:
        self._running = True
        logger.info(f"[BackgroundAgent] Started Goal-Autonomy loop. Polling every {self.poll_interval}s.")
        while self._running:
            try:
                # ── Proactive AI Interaction ──
                self._process_proactive_context()
                
                # ── Goal Based Autonomy ──
                self._process_active_goals()
                
                # ── Redis Task Enqueueing ──
                pending = self.memory.get_pending_tasks()
                if pending:
                    logger.info(f"[BackgroundAgent] Dispatching {len(pending)} tasks to Redis.")
                for task in pending:
                    self._dispatch_to_rq(task)
            except Exception as exc:
                logger.error(f"[BackgroundAgent] Unhandled error: {exc}")

            await asyncio.sleep(self.poll_interval)

    def _process_proactive_context(self):
        """Observe OS, analyze context, and decide whether to interrupt the user."""
        try:
            activity = self.monitor.get_current_activity()
            context = self.analyzer.analyze(activity, self.memory)
            trigger = self.trigger_engine.evaluate(context)

            if trigger.get("should_trigger"):
                self._deliver_proactive_message(trigger["message_hint"])
        except Exception as e:
            logger.debug(f"[BackgroundAgent] Error in proactive context loop: {e}")

    def _deliver_proactive_message(self, message: str):
        """Push notification to the OS desktop."""
        logger.info(f"[BackgroundAgent] Proactive Alert: {message}")
        try:
            from plyer import notification
            notification.notify(
                title="JARVIS",
                message=message,
                app_name="JARVIS Proactive Assistant",
                timeout=10
            )
        except Exception as e:
            logger.debug(f"[BackgroundAgent] Notification delivery failed (plyer missing or headless OS): {e}")

    def _process_active_goals(self):
        """Monitors continuous goals and creates plans periodically independent of users"""
        with SessionLocal() as db:
            triggers = db.query(EventTrigger).filter(EventTrigger.active == True).all()
            if not triggers:
                return
            
            for trigger in triggers:
                time_delta = (datetime.utcnow() - trigger.last_checked).total_seconds()
                if time_delta > 60: # 1 minute minimum polling rate for continuous goals
                    logger.info(f"[BackgroundAgent] Goal Triggered: '{trigger.condition}' -> '{trigger.action_desc}'")
                    # Enqueue an implicit task 
                    task_queue.enqueue(
                        execute_deferred_task,
                        trigger.session_id,
                        "search_web", # Generic default trigger fallback 
                        {"query": trigger.condition},
                        trigger.id
                    )
                    trigger.last_checked = datetime.utcnow()
                    db.commit()

    def _dispatch_to_rq(self, task: ScheduledTask):
        try:
            params_dict = json.loads(task.params)
            
            # Namespace isolation: Extract usr_X from session_id (format: usr_1_uuid)
            parts = task.session_id.split("_")
            user_id_str = "default"
            if len(parts) >= 2 and parts[0] == "usr":
                user_id_str = parts[1]
                
            dynamic_queue = Queue(f"jarvis_tasks_{user_id_str}", connection=redis_conn)

            # Push explicitly to Redis queue safely isolated
            job = dynamic_queue.enqueue(
                execute_deferred_task,
                task.session_id,
                task.tool,
                params_dict,
                task.id
            )
            structured_logger.log_event("TASK_ENQUEUED_TO_REDIS", {"task_id": task.id, "job_id": job.id, "namespace": f"jarvis_tasks_{user_id_str}"})
            
            with SessionLocal() as db:
                db_task = db.query(ScheduledTask).filter(ScheduledTask.id == task.id).first()
                db_task.status = "completed"  # Mark handled locally by dispatcher
                db.commit()
                
        except Exception as e:
            logger.error(f"[BackgroundAgent] Dispatch failed task {task.id}: {e}")

    def stop(self) -> None:
        self._running = False

from config.settings import settings
background_agent = BackgroundAgent(poll_interval=settings.background_poll_interval)
