import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from memory.db import SessionLocal, Interaction, Preference, StepResult, ScheduledTask

logger = logging.getLogger(__name__)


# ─── Memory Agent ─────────────────────────────────────────────────────────────

# Manages all persistent memory: conversations, preferences, results, and scheduled tasks
class MemoryAgent:

    # ── Conversation History ──────────────────────────────────────────────────

    # Store a single conversation turn in the DB
    def add_interaction(self, session_id: str, role: str, content: str):
        with SessionLocal() as db:
            db.add(Interaction(session_id=session_id, role=role, content=content))
            db.commit()

    # Retrieve the last N turns for a session, in chronological order
    def get_recent_interactions(self, session_id: str, limit: int = 10) -> List[Dict[str, str]]:
        with SessionLocal() as db:
            rows = (
                db.query(Interaction)
                .filter(Interaction.session_id == session_id)
                .order_by(Interaction.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [{"role": r.role, "content": r.content} for r in reversed(rows)]

    # ── User Preferences ─────────────────────────────────────────────────────

    # Upsert a single user preference key-value pair
    def set_preference(self, key: str, value: str):
        with SessionLocal() as db:
            pref = db.query(Preference).filter(Preference.key == key).first()
            if pref:
                pref.value = value
            else:
                db.add(Preference(key=key, value=value))
            db.commit()

    # Retrieve a single preference value by key, or None if not set
    def get_preference(self, key: str) -> Optional[str]:
        with SessionLocal() as db:
            pref = db.query(Preference).filter(Preference.key == key).first()
            return pref.value if pref else None

    # Return all stored preferences as a flat dict — used for context injection
    def get_all_preferences(self) -> Dict[str, str]:
        with SessionLocal() as db:
            return {p.key: p.value for p in db.query(Preference).all()}

    # ── Step Result Storage ───────────────────────────────────────────────────

    # Persist the outcome of a single tool execution step for audit/recall
    def log_step_result(
        self,
        session_id: str,
        plan_id: str,
        step_id: int,
        tool: str,
        status: str,
        output: Optional[str] = None,
        error: Optional[str] = None,
    ):
        with SessionLocal() as db:
            db.add(StepResult(
                session_id=session_id,
                plan_id=plan_id,
                step_id=step_id,
                tool=tool,
                status=status,
                output=str(output) if output is not None else None,
                error=error,
            ))
            db.commit()
        logger.debug(f"[Memory] Logged step {step_id} ({tool}) -> {status}")

    # ── Scheduled Task Queue ─────────────────────────────────────────────────

    # Schedule a tool call to be executed at a future datetime
    def enqueue_task(
        self,
        session_id: str,
        description: str,
        tool: str,
        params: Dict[str, Any],
        run_at: datetime,
        priority: int = 1,
        recurrence: Optional[str] = None
    ):
        with SessionLocal() as db:
            db.add(ScheduledTask(
                session_id=session_id,
                description=description,
                tool=tool,
                params=json.dumps(params),
                run_at=run_at,
                priority=priority,
                recurrence=recurrence
            ))
            db.commit()
        logger.info(f"[Memory] Scheduled task '{description}' at {run_at.isoformat()}")

    # Fetch all pending tasks that are due now or overdue, sorted by priority (higher first)
    def get_pending_tasks(self) -> List[ScheduledTask]:
        with SessionLocal() as db:
            now = datetime.utcnow()
            tasks = (
                db.query(ScheduledTask)
                .filter(ScheduledTask.status == "pending", ScheduledTask.run_at <= now)
                .order_by(ScheduledTask.priority.desc())
                .all()
            )
            # Expunge objects so they can be accessed outside the session
            for t in tasks:
                db.expunge(t)
            return tasks

    # Update task status or apply retry/recurrence
    def update_task_status(self, task_id: int, status: str):
        from datetime import timedelta
        with SessionLocal() as db:
            task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
            if not task:
                return
            
            if status == "success":
                if task.recurrence:
                    # Bump run_at if recurring
                    if task.recurrence == "daily":
                        task.run_at += timedelta(days=1)
                    elif task.recurrence == "hourly":
                        task.run_at += timedelta(hours=1)
                    # Stay 'pending'
                else:
                    task.status = "completed"
            elif status == "failed":
                task.retries += 1
                if task.retries >= task.max_retries:
                    task.status = "failed"
                else:
                    # Delay retry by 2 minutes
                    task.run_at += timedelta(minutes=2)
            db.commit()
