import logging
import time
from typing import List, Dict, Any, Optional

from tools.registry import ToolRegistry
from safety.guard import SafetyGuard, PermissionLevel
from planner import Plan, PlanStep

logger = logging.getLogger(__name__)

# ─── Result schema ─────────────────────────────────────────────────────────────

# Builds a standardised execution result dict for a single step
def _make_result(step_id: int, status: str, output: Any = None, error: Optional[str] = None) -> Dict:
    return {"step_id": step_id, "status": status, "output": output, "error": error}


from observability.logger import structured_logger
from perception.vision import perception
from learning.metrics import metrics
from learning.event_logger import workflow_logger

# ─── Executor Agent ────────────────────────────────────────────────────────────

class LocalTaskQueued(Exception):
    """Signal raised when an execution step is deferred to a local polling agent."""
    pass

# Executes a validated Plan, enforcing safety checks and retry logic on each step
class ToolExecutorAgent:
    # Maximum number of automatic retries for a retryable failed step
    MAX_RETRIES = 2

    def __init__(self):
        self.registry = ToolRegistry()
        self.guard = SafetyGuard()

    # Dynamically attempts to replan when OCR validation specifically invalidates states
    def replan_remaining_steps(self, context_goal: str, failed_step: PlanStep, vision_state: Dict, plan: Plan):
        logger.warning(f"[Executor] Triggering REPLAN from step {failed_step.step_id}.")
        if getattr(self, "planner", None) is None:
            from planner import TaskPlannerAgent
            self.planner = TaskPlannerAgent()
            
        new_intent = f"Original Goal: {context_goal}\nFailed Step: `{failed_step.action}`.\nCurrent Vision Context/State: {vision_state.get('state_summary', 'Unknown block')}\nPlease create a new sub-plan to recover and finish the original goal respecting existing steps."
        
        recovered_plan = self.planner.create_plan(new_intent, context="REPLANNING")
        if recovered_plan and recovered_plan.steps:
            logger.info(f"[Executor] Successfully generated recovering sub-plan ({len(recovered_plan.steps)} steps).")
            # Recursively invoke the recovering cascade
            return self.execute_plan(recovered_plan)
        else:
            logger.error("[Executor] Replanning completely failed.")
            return [{"step_id": failed_step.step_id, "status": "failed", "error": "Unrecoverable replanning failure"}]

    # Execute a single step with safety gating, retry logic, and perception checks.
    # role is forwarded to the guard for role-based access control.
    def _execute_step(self, step: PlanStep, goal_context: str, role: str = "guest", user_id: int = 1) -> Dict:
        tool_name = step.tool
        params = step.params

        structured_logger.log_event("STEP_PENDING", {"step_id": step.step_id, "tool": tool_name})
        
        # ── Local Agent Splitting Logic (Non-blocking queueing) ────────────────
        if self.registry.get_tool_environment(tool_name) == "local":
            import json
            import uuid
            from memory.db import SessionLocal, LocalTask
            # Insert task into database and suspend current executor immediately
            
            idem_key = f"t-{getattr(self, '_current_request_id', 'req')}-{step.step_id}"
            with SessionLocal() as db:
                # Idempotent enqueue 
                if not db.query(LocalTask).filter_by(idempotency_key=idem_key).first():
                    lt = LocalTask(
                        user_id=user_id,
                        idempotency_key=idem_key,
                        request_id=getattr(self, "_current_request_id", "unknown"),
                        session_id=getattr(self, "_current_session_id", "unknown"),
                        plan_id=getattr(self, "_current_plan_id", "unknown"),
                        step_id=str(step.step_id),
                        plan_json=getattr(self, "_current_plan_json", None), # Fallback mapping
                        action=tool_name,
                        params=json.dumps(params),
                        status="pending",
                        priority=1
                    )
                    db.add(lt)
                    db.commit()
            
            logger.info(f"[Executor] Tool '{tool_name}' requires local execution. Queued LocalTask for polling.")
            structured_logger.log_event("STEP_QUEUED_LOCAL", {"step_id": step.step_id, "tool": tool_name})
            
            if settings.use_execution_state:
                return _make_result(step.step_id, "waiting_for_local")
            else:
                # Legacy explicit abort mechanism
                raise LocalTaskQueued(str(step.step_id))

        # ── Safety check (includes RBAC via role) ──────────────────────────────
        level, reason = self.guard.evaluate_action(tool_name, params, role=role)

        if level == PermissionLevel.BLOCK:
            logger.warning(f"[Executor] BLOCKED step {step.step_id}: {reason}")
            structured_logger.log_event("STEP_FAILED", {"step_id": step.step_id, "reason": reason, "status": "blocked"})
            return _make_result(step.step_id, "blocked", error=reason)

        if level == PermissionLevel.ASK_USER:
            logger.info(f"[Executor] PENDING APPROVAL step {step.step_id}: {reason}")
            structured_logger.log_event("STEP_PENDING_APPROVAL", {"step_id": step.step_id, "reason": reason})
            return _make_result(step.step_id, "pending_approval", error=reason)

        # ── Tool lookup ───────────────────────────────────────────────────────
        tool_fn = self.registry.get_tool(tool_name)
        if not tool_fn:
            error_msg = f"Tool '{tool_name}' is not registered."
            logger.error(f"[Executor] {error_msg}")
            structured_logger.log_event("STEP_FAILED", {"step_id": step.step_id, "reason": error_msg})
            return _make_result(step.step_id, "failed", error=error_msg)

        # ── Closed Loop Execution / Observation ────────────────────────────────
        attempts = self.MAX_RETRIES if step.retryable else 1
        last_error: Optional[str] = None
        vision_result_cache: Dict = {}

        for attempt in range(1, attempts + 1):
            structured_logger.log_event("STEP_RUNNING", {"step_id": step.step_id, "attempt": attempt, "tool": tool_name})
            try:
                logger.info(f"[Executor] Running step {step.step_id} '{tool_name}' (attempt {attempt}/{attempts})")
                
                # ACT
                output = tool_fn(**params)
                
                # OBSERVE
                if tool_name in ["open_application", "open_website", "click", "type_text", "press_keys", "open_url", "search_google", "click_selector", "fill_input"]:
                    time.sleep(1.5)  # Wait for UI to settle
                    # Hybrid Perception fallback
                    from perception.hybrid import perception_hybrid
                    img_bytes = perception_hybrid.capture_screenshot()
                    
                    if settings.use_semantic_perception:
                        from perception.semantic import perception_semantic
                        # Derive window hint from step params (e.g. name="chrome" → hints resolver)
                        _window_hint = (
                            step.params.get("name") or
                            step.params.get("app") or
                            step.params.get("title") or
                            ""
                        )
                        vision_result = perception_semantic.analyze_ui_semantics(
                            goal_context=step.action,
                            expected_outcome=getattr(step, "expected_outcome", step.action),
                            target_window_hint=_window_hint,
                        )
                    else:
                        expected = getattr(step, "expected_outcome", step.action)
                        vision_result = perception_hybrid.analyze_screen(img_bytes, goal_context=step.action, expected_outcome=expected)
                    
                    vision_result_cache = vision_result
                    
                    # THINK
                    is_fulfilled = vision_result.get("expected_fulfilled", True)
                    
                    structured_logger.log_event("OBSERVATION", {"step_id": step.step_id, "vision": vision_result})
                    
                    if not is_fulfilled:
                        raise Exception(f"Vision verification failed. Expected: {getattr(step, 'expected_outcome', step.action)}. Screen state: {vision_result.get('state_summary')}")
                
                logger.info(f"[Executor] Step {step.step_id} SUCCESS")
                structured_logger.log_event("STEP_SUCCESS", {"step_id": step.step_id, "output": str(output)})
                metrics.log_tool_success(tool_name)
                return _make_result(step.step_id, "success", output=output)
                
            except Exception as exc:
                last_error = str(exc)
                logger.warning(f"[Executor] Step {step.step_id} attempt {attempt} FAILED: {last_error}")
                if attempt < attempts:
                    structured_logger.log_event("STEP_RETRYING", {"step_id": step.step_id, "error": last_error, "attempt": attempt})
                    time.sleep(1)  # Brief back-off before retry

        # Determine Reason utilizing Failure Engine!
        from reasoning.failure_engine import failure_diagnostics
        diagnostic = failure_diagnostics.analyze_failure(step.action, last_error, vision_result_cache)
        logger.error(f"[Executor] Step {step.step_id} permanently FAILED. Diagnosis: {diagnostic.get('failure_type')} - {diagnostic.get('reason')}")

        structured_logger.log_event("STEP_FAILED", {"step_id": step.step_id, "diagnostic": diagnostic})
        metrics.log_tool_failure(tool_name)

        # Log failure into the behavioral modeling pipeline so future workflows
        # avoid reusing this tool in this context when it has a high failure rate.
        try:
            workflow_logger.log_task_failure(
                session_id=getattr(self, "_current_session_id", "unknown"),
                task_id=getattr(self, "_current_plan_id", "unknown"),
                tool=tool_name,
                failure_reason=last_error,
            )
        except Exception:
            pass  # Never let telemetry crash the main execution path

        # Trigger true replanning implicitly by injecting marker
        import json
        diagnostic_payload = json.dumps({"vision": vision_result_cache, "diagnostic": diagnostic})
        raise Exception(f"__REPLAN_REQUIRED__::{last_error}::{diagnostic_payload}")

    # Execute all steps in a Plan, respecting declared dependencies.
    # Accepts previously finished steps allowing asynchronous stateless resumptions.
    def execute_plan(self, plan: Plan, role: str = "guest", session_id: str = "unknown", 
                     user_id: int = 1, completed_ids: set = None, request_id: str = None) -> List[Dict]:
        from learning.event_logger import workflow_logger
        import uuid
        
        # Hydrate execution tracking states downstream
        self._current_session_id = session_id
        self._current_plan_id = plan.plan_id
        self._current_request_id = request_id or str(uuid.uuid4())
        self._current_plan_json = plan.json()

        # Database ExecutionState Tracking
        if settings.use_execution_state:
            from memory.db import SessionLocal, ExecutionState
            import json
            with SessionLocal() as db:
                es = db.query(ExecutionState).filter_by(plan_id=plan.plan_id).first()
                if not es:
                    es = ExecutionState(
                        session_id=session_id,
                        plan_id=plan.plan_id,
                        request_id=self._current_request_id,
                        steps=self._current_plan_json,
                        completed_steps=json.dumps(list(completed_ids or []))
                    )
                    db.add(es)
                else:
                    loaded = json.loads(es.completed_steps)
                    if completed_ids:
                        loaded.extend(list(completed_ids))
                    completed_ids = set(loaded)
                    es.completed_steps = json.dumps(list(completed_ids))
                    es.status = "running"
                db.commit()

        # Pre-simulation hook
        if settings.use_simulation and not completed_ids: # Only run on first fresh pass
            from simulation.simulator import simulator_node
            sim = simulator_node.simulate_plan(plan, "Execution Boot Sequence")
            if sim.get("simulate_verdict") == "reject":
                logger.error(f"[Executor] Simulator rejected the plan dynamically: {sim.get('simulated_outcome_reasoning')}")
                return [_make_result(0, "failed", error=sim.get('simulated_outcome_reasoning'))]
                
        results: Dict[int, Dict] = {}  # step_id → result

        # Topologically sort steps by depends_on to run in the right order
        completed_ids = set(completed_ids) if completed_ids else set()
        remaining = [s for s in plan.steps if s.step_id not in completed_ids]
        max_iterations = len(remaining) * 2  # Guard against circular deps
        iteration = 0

        while remaining:
            iteration += 1
            if iteration > max_iterations:
                logger.error("[Executor] Dependency resolution loop limit reached — aborting.")
                break

            made_progress = False
            still_pending = []

            for step in remaining:
                # Check all declared dependencies have completed successfully
                deps_met = all(
                    dep_id in completed_ids for dep_id in step.depends_on
                )
                if not deps_met:
                    still_pending.append(step)
                    continue

                # Execute this step (pass role for RBAC)
                try:
                    result = self._execute_step(step, goal_context=plan.goal, role=role)
                    results[step.step_id] = result
                    made_progress = True

                    if result["status"] == "waiting_for_local":
                        # Native non-exception state machine execution loop parked.
                        logger.info(f"[Executor] Execution parked natively (WAITING_FOR_LOCAL) at step {step.step_id}.")
                        if settings.use_execution_state:
                            from memory.db import SessionLocal, ExecutionState
                            import json
                            with SessionLocal() as db:
                                es = db.query(ExecutionState).filter_by(plan_id=plan.plan_id).first()
                                if es:
                                    es.status = "waiting_for_local"
                                    es.completed_steps = json.dumps(list(completed_ids))
                                    db.commit()
                        return list(results.values())
                    elif result["status"] == "success":
                        completed_ids.add(step.step_id)
                        # Log structured tool usage to modeling pipeline
                        workflow_logger.log_tool_usage(session_id, plan.plan_id, step.tool, step.action)
                    else:
                        # Dependent steps that rely on a failed step will be skipped
                        logger.warning(
                            f"[Executor] Step {step.step_id} did not succeed "
                            f"(status={result['status']}); downstream steps may be skipped."
                        )
                        completed_ids.add(step.step_id)  # Still mark done to avoid infinite loop
                except LocalTaskQueued as lq:
                    # Legacy Fallback mechanism if USE_EXECUTION_STATE=False
                    # Park execution gracefully — wait for agent result pingback.
                    # Inject the serialized plan into the DB row so resumption can reload the entire workflow.
                    from memory.db import SessionLocal, LocalTask
                    with SessionLocal() as db:
                        lt = db.query(LocalTask).filter_by(
                            plan_id=plan.plan_id, 
                            step_id=str(step.step_id), 
                            status="pending"
                        ).first()
                        if lt:
                            lt.plan_json = plan.json()
                            db.commit()

                    results[step.step_id] = _make_result(step.step_id, "local_queued")
                    logger.info(f"[Executor] Execution parked cleanly waiting on Agent for step {step.step_id}.")
                    return list(results.values())
                except Exception as exc:
                    err_str = str(exc)
                    if "__REPLAN_REQUIRED__" in err_str:
                        logger.warning(f"[Executor] Engaging Replanner for Plan {plan.plan_id}")
                        parts = err_str.split("::")
                        import json
                        vision_cache = json.loads(parts[2]) if len(parts) > 2 and parts[2] else {}
                        replan_res = self.replan_remaining_steps(plan.goal, step, vision_cache, plan)
                        # We absorb the replan dynamically and return instantly to prevent bleeding states
                        return list(results.values()) + replan_res
                    else:
                        results[step.step_id] = _make_result(step.step_id, "failed", error=err_str)
                        completed_ids.add(step.step_id)

            remaining = still_pending

            if not made_progress and remaining:
                # No forward progress — unsatisfiable dependencies
                for step in remaining:
                    results[step.step_id] = _make_result(
                        step.step_id, "skipped",
                        error="Dependency could not be satisfied."
                    )
                break

        overall_success = (len(completed_ids) == len(plan.steps))
        workflow_logger.log_task_end(session_id, plan.plan_id, overall_success)

        return list(results.values())

    # Legacy single-step entry point kept for backward compatibility
    def execute(self, tool_name: str, params: dict) -> dict:
        from planner import PlanStep
        step = PlanStep(step_id=0, tool=tool_name, action="direct call", params=params)
        result = self._execute_step(step)
        # Map to old format expected by orchestrator
        if result["status"] == "success":
            return {"status": "success", "result": result["output"]}
        return {"status": result["status"], "message": result.get("error", "Unknown error")}
