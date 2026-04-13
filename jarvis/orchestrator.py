import re
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from planner import TaskPlannerAgent, Plan
from executor import ToolExecutorAgent
from communication.responder import CommunicationAgent
from memory.manager import MemoryAgent
from auth.identity import get_user_role          # Role resolution for RBAC
from memory.session_store import get_or_create_session  # Unified cross-channel sessions

logger = logging.getLogger(__name__)

# ─── Scheduling intent keywords ───────────────────────────────────────────────
# Phrases that signal the user wants a deferred / background task
SCHEDULE_KEYWORDS = [
    "remind me", "schedule", "later", "at 8", "in 10 minutes", "in an hour",
    "tomorrow", "tonight", "set an alarm", "do it later", "ping me",
]


# ─── Orchestrator ─────────────────────────────────────────────────────────────

# Central brain of JARVIS — classifies intent, routes to planner or scheduler,
# drives the executor, and formats the reply via the communication agent
class OrchestratorAgent:
    def __init__(self):
        self.planner   = TaskPlannerAgent()
        self.executor  = ToolExecutorAgent()
        self.responder = CommunicationAgent()
        self.memory    = MemoryAgent()

    # ── Intent classification ─────────────────────────────────────────────────

    # Returns True when the user's message signals a scheduling request
    def _is_scheduling_intent(self, text: str) -> bool:
        lower = text.lower()
        return any(kw in lower for kw in SCHEDULE_KEYWORDS)

    # Returns True for simple, single-step tasks that don't need the planner
    def _is_simple_intent(self, text: str) -> bool:
        lower = text.lower()
        patterns = [
            r"^(hi|hello|hey|yo)[ ,!]",
            r"^what (is|are|time|day)",
            r"^(tell me about|explain)",
        ]
        return any(re.match(p, lower) for p in patterns)

    # ── Schedule extraction ───────────────────────────────────────────────────

    # Parse a rough time offset from the message (e.g. "in 10 minutes" → timedelta)
    # Returns the absolute datetime when the task should run, defaulting to now+1 min
    def _parse_run_at(self, text: str) -> datetime:
        lower = text.lower()

        # "every day at X" / "daily at X"
        m_daily = re.search(r"(every day|daily)\s*at (\d{1,2})", lower)
        
        m = re.search(r"in (\d+) minute", lower)
        if m:
            return datetime.utcnow() + timedelta(minutes=int(m.group(1))), None

        m = re.search(r"in (\d+) hour", lower)
        if m:
            return datetime.utcnow() + timedelta(hours=int(m.group(1))), None

        m = re.search(r"at (\d{1,2}):(\d{2})", lower)
        if m:
            now = datetime.utcnow()
            target = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)
            if target < now:
                target += timedelta(days=1)
            recurrence = "daily" if m_daily else None
            return target, recurrence

        m = re.search(r"at (\d{1,2})\s*(am|pm)", lower)
        if m:
            hour = int(m.group(1))
            if m.group(2) == "pm" and hour != 12:
                hour += 12
            elif m.group(2) == "am" and hour == 12:
                hour = 0
            now = datetime.utcnow()
            target = now.replace(hour=hour, minute=0, second=0)
            if target < now:
                target += timedelta(days=1)
            recurrence = "daily" if m_daily else None
            return target, recurrence

        # "every day" or "daily" without time
        if "every day" in lower or "daily" in lower:
            return datetime.utcnow() + timedelta(days=1), "daily"

        # "every hour" or "hourly"
        if "every hour" in lower or "hourly" in lower:
            return datetime.utcnow() + timedelta(hours=1), "hourly"

        # Fallback: 1 minute from now
        return datetime.utcnow() + timedelta(minutes=1), None

    # ── Node: Plan ────────────────────────────────────────────────────────────

    # Classify intent, build a Plan, or enqueue a scheduled task.
    # role is passed down to the executor so the guard can enforce RBAC.
    def _node_plan(self, session_id: str, user_input: str, role: str = "guest"):
        from planner_validator import validator
        from config.settings import settings

        # Persist user's message
        self.memory.add_interaction(session_id, "user", user_input)

        # Build context string from recent history for the planner
        history = self.memory.get_recent_interactions(session_id, limit=5)
        context = "\n".join(f"{h['role']}: {h['content']}" for h in history)

        plan = None
        # Route scheduling intents to the background queue
        if self._is_scheduling_intent(user_input):
            logger.info(f"[Orchestrator] Scheduling intent detected: '{user_input}'")
            run_at, recurrence = self._parse_run_at(user_input)
            plan = self.planner.create_plan(user_input, context=context)
            if plan and plan.steps:
                first_step = plan.steps[0]
                self.memory.enqueue_task(
                    session_id=session_id,
                    description=user_input,
                    tool=first_step.tool,
                    params=first_step.params,
                    run_at=run_at,
                    recurrence=recurrence,
                    priority=1
                )
            return None  # Signal task is queued

        # Generate Plan natively 
        if settings.use_hierarchical_planner:
            logger.info("[Orchestrator] Generating Plan via V6 Hierarchical Engines")
            from planner_v6.high_level import hl_planner
            from planner_v6.low_level import ll_planner
            from agents.critic import critic_node
            
            # 1. Strategy
            strategy = hl_planner.generate_strategy(user_input, context)
            
            # 2. Map to Execute
            plan = ll_planner.map_to_executable(strategy, context)
            
            # 3. Critic check valve
            if settings.use_critic_agent:
                review = critic_node.evaluate_plan(plan)
                if not review.get("approved"):
                    # Fast automatic 1-retry bounded context
                    logger.warning("[Orchestrator] Critic rejected initial hierarchical trace. Initiating re-map.")
                    context += f"\nCRITIC REJECTED PRIOR MAP: {review.get('feedback')}"
                    plan = ll_planner.map_to_executable(strategy, context)
        else:
            # Fallback legacy V5 Planner
            plan = self.planner.create_plan(user_input, context=context)

        is_valid, err_msg = validator.validate_plan(plan)
        
        # Super simple auto-fix via single replan if the format is fundamentally wrong
        if not is_valid:
            logger.warning(f"[Orchestrator] Plan Validation Failed: {err_msg}. Requesting immediate replan.")
            context += f"\nSystem: Previous plan failed validation -> {err_msg}. Fix tools and params."
            if settings.use_hierarchical_planner:
               from planner.low_level import ll_planner
               from planner.high_level import hl_planner
               plan = ll_planner.map_to_executable(hl_planner.generate_strategy(user_input, context), context)
            else:
               plan = self.planner.create_plan(user_input, context=context)
            
        return plan

    # ── Node: Execute ─────────────────────────────────────────────────────────

    # Run all steps in the plan and log results to memory.
    # role is forwarded to execute_plan so the safety guard can apply RBAC.
    def _node_execute(self, session_id: str, plan: Plan, role: str = "guest") -> List[Dict]:
        results = self.executor.execute_plan(plan, role=role)

        # Persist each step result for audit / future recall
        for r in results:
            self.memory.log_step_result(
                session_id=session_id,
                plan_id=plan.plan_id,
                step_id=r["step_id"],
                tool=next(
                    (s.tool for s in plan.steps if s.step_id == r["step_id"]), "unknown"
                ),
                status=r["status"],
                output=str(r.get("output")) if r.get("output") else None,
                error=r.get("error"),
            )
        return results

    # ── Node: Respond ─────────────────────────────────────────────────────────

    # Convert raw execution results into a polished, channel-aware reply and store it.
    def _node_respond(
        self,
        session_id: str,
        results: List[Dict],
        tone: str,
        channel: str = "default",
        scheduled: bool = False,
    ) -> str:
        if scheduled:
            raw = "Your task has been scheduled and will run automatically in the background."
        elif not results:
            raw = "I wasn't able to produce a result for that request."
        else:
            parts = []
            for r in results:
                if r["status"] == "success":
                    parts.append(str(r["output"]))
                elif r["status"] in ("blocked", "pending_approval"):
                    # Surface the human-readable denial message directly
                    parts.append(r.get("error", "Action blocked or pending approval."))
                else:
                    parts.append(f"[Step {r['step_id']} failed: {r.get('error', 'Unknown error')}]")
            raw = "\n".join(parts).strip() or "Task completed with no output."

        # Pass channel so the responder formats output appropriately
        reply = self.responder.format_response(raw, tone=tone, channel=channel)
        self.memory.add_interaction(session_id, "assistant", reply)
        return reply

    # ── Main entry point ──────────────────────────────────────────────────────

    # Orchestrate a full request lifecycle: classify → plan → execute → respond.
    # channel and role are new in v7 for multi-channel + RBAC support.
    def process_request(
        self,
        session_id: str,
        user_input: str,
        tone: str = "professional",
        channel: str = "default",
        role: str = "guest",
    ) -> str:
        logger.info(
            f"[Orchestrator] Processing request session={session_id} "
            f"channel={channel} role={role}"
        )

        plan = self._node_plan(session_id, user_input, role=role)

        # Scheduling path: task was queued, reply immediately
        if plan is None:
            return self._node_respond(session_id, [], tone, channel=channel, scheduled=True)

        # Execution path: run the plan with role forwarded to the guard
        results = self._node_execute(session_id, plan, role=role)
        return self._node_respond(session_id, results, tone, channel=channel)
