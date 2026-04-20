import json
import uuid
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from config.settings import settings
from tools.registry import registry

logger = logging.getLogger(__name__)

# ─── Pydantic schemas for strict plan validation ─────────────────────────────

# A single executable step within a plan
class PlanStep(BaseModel):
    step_id: int
    tool: str
    action: str
    expected_outcome: str = Field(default="Execution succeeds without errors")
    params: Dict[str, Any]
    depends_on: List[int] = Field(default_factory=list)
    retryable: bool = True

# The full structured plan produced by the planner
class Plan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    goal: str
    steps: List[PlanStep]
    # Workflow learning meta-data tracking
    is_learned_workflow: bool = False
    workflow_confidence: float = 0.0
    workflow_sequence: str = ""


# ─── Planner Agent ─────────────────────────────────────────────────────────────

# Generates an ordered, validated execution plan from natural language intent
class TaskPlannerAgent:
    def __init__(self):
        # Only enable LLM when a real key is present
        is_real_key = bool(
            settings.openai_api_key and not settings.openai_api_key.startswith("your_")
        )
        self.llm_enabled = is_real_key
        self.available_tools = registry.get_available_tools()
        self.tool_descriptions = registry.get_tool_descriptions()

    # Build the system prompt dynamically injecting tool registry info
    def _build_system_prompt(self) -> str:
        tool_list = "\n".join(
            f'  - "{name}": {desc}' for name, desc in self.tool_descriptions.items()
        )
        return f"""You are JARVIS Task Planner — a precise AI orchestration engine.

Available tools and their purpose:
{tool_list}

Your job: Decompose the user's goal into a minimal set of executable steps.

OUTPUT RULES (STRICT):
1. Return ONLY a valid JSON object — no markdown fences, no explanation.
2. Every step MUST use one of the listed tools — never invent tools.
3. Every step MUST have all required params filled with real values.
4. Never produce vague steps like "process data" or "handle request".
5. For simple single-action tasks, return exactly 1 step.
6. Set retryable: false only for irreversible actions (e.g. sending messages).

Required JSON format:
{{
  "plan_id": "<uuid>",
  "goal": "<restate the user's goal concisely>",
  "steps": [
    {{
      "step_id": 1,
      "tool": "<tool_name>",
      "action": "<specific one-line description of what this step does>",
      "expected_outcome": "<concrete visual or programmatic state proving success>",
      "params": {{"<param>": "<value>"}},
      "depends_on": [],
      "retryable": true
    }}
  ]
}}"""

    # Parse and validate the raw JSON string from the LLM into a Plan object
    def _parse_plan(self, raw: str, goal: str) -> Optional[Plan]:
        # Strip markdown fences if the model wraps output anyway
        content = raw.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        try:
            data = json.loads(content)
            # Ensure plan_id and goal are present
            data.setdefault("plan_id", str(uuid.uuid4()))
            data.setdefault("goal", goal)
            return Plan(**data)
        except Exception as e:
            logger.error(f"[Planner] Failed to parse LLM plan: {e}\nRaw: {raw}")
            return None

    # Main entry point: produce a validated Plan from user intent
    def create_plan(self, user_intent: str, context: Optional[str] = None, session_id: str = "unknown", skills_context: str = "") -> Optional[Plan]:
        from learning.event_logger import workflow_logger
        from learning.workflows import match_workflow

        # Log Task Initialization Lifecycle phase
        workflow_logger.log_task_start(session_id, user_intent, user_intent)

        if not self.llm_enabled:
            return self._mock_plan(user_intent)

        # Import here to avoid errors when no key is set
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = ChatOpenAI(
            api_key=settings.openai_api_key,
            model="gpt-4o",
            temperature=0,            # Deterministic for structured output
            response_format={"type": "json_object"},   # Force JSON mode
        )

        messages = [SystemMessage(content=self._build_system_prompt())]
        if context:
            messages.append(SystemMessage(content=f"User context:\n{context}"))
            
        if skills_context:
            messages.append(SystemMessage(content=skills_context))

        # ── Workflow Injection (adaptive guidance, not rigid copy) ─────────────
        # match_workflow now returns a bundle: {workflow, confidence, semantic_similarity, mode}
        match_result = match_workflow(user_intent, context)
        is_learned = False
        conf = 0.0
        seq_str = ""

        if match_result and match_result.get("mode") in ("auto", "ask"):
            is_learned = True
            w = match_result["workflow"]
            conf = match_result["confidence"]
            sem_sim = match_result["semantic_similarity"]

            # Build a human-readable parameterized step summary for the LLM
            step_hints = []
            for s in w.get("steps", []):
                tool = s["tool"]
                template = s.get("params_template", {})
                hint = tool
                if template:
                    hint += f" (params: {', '.join(template.keys())})"
                step_hints.append(hint)
            seq_str = " -> ".join([s["tool"] for s in w.get("steps", [])])

            # Adaptive prompt: guide the planner without forcing exact copies
            # The LLM is told it CAN modify, add, or skip steps as needed.
            workflow_prompt = (
                f"LEARNED WORKFLOW SUGGESTION (confidence={conf:.2f}, semantic_similarity={sem_sim:.2f}):\n"
                f"Suggested tool sequence: {' -> '.join(step_hints)}\n"
                f"INSTRUCTIONS: Use this as a starting template. You MAY:\n"
                f"  - Adapt parameter values to match the current request context.\n"
                f"  - Add steps that are clearly missing for correctness.\n"
                f"  - Remove steps that are not relevant to this specific request.\n"
                f"  - Reorder steps if that produces a better outcome.\n"
                f"Do NOT copy blindly. This is a guide, not a script."
            )
            messages.append(SystemMessage(content=workflow_prompt))

        messages.append(HumanMessage(content=user_intent))

        try:
            response = llm.invoke(messages)
            plan = self._parse_plan(response.content, goal=user_intent)
            if plan:
                plan.is_learned_workflow = is_learned
                plan.workflow_confidence = conf
                plan.workflow_sequence = seq_str

                # Log plan generation Lifecycle phase
                workflow_logger.log_plan_generated(session_id, user_intent, [s.dict() for s in plan.steps])
            return plan
        except Exception as e:
            logger.error(f"[Planner] LLM call failed: {e}")
            return None

    # Fallback planner used when no OpenAI key is configured
    def _mock_plan(self, intent: str) -> Plan:
        lower = intent.lower()

        if "website" in lower or "portfolio" in lower:
            html = (
                "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
                "<title>My Portfolio</title></head><body><h1>My Portfolio</h1>"
                "<p>Welcome to my portfolio site.</p></body></html>"
            )
            steps = [PlanStep(
                step_id=1, tool="create_file", action="Generate a basic portfolio HTML file",
                params={"name": "index.html", "content": html}, retryable=True
            )]

        elif "message" in lower or "whatsapp" in lower:
            # Extract a target name from intent heuristically
            to = "Rahul" if "rahul" in lower else "contact"
            msg = "I'll be late." if "late" in lower else intent
            steps = [PlanStep(
                step_id=1, tool="send_whatsapp", action="Send a WhatsApp message",
                params={"number": to, "message": msg}, retryable=False
            )]

        elif "telegram" in lower:
            steps = [PlanStep(
                step_id=1, tool="send_telegram", action="Send a Telegram message",
                params={"chat_id": "your_chat_id", "message": intent}, retryable=False
            )]

        elif "weather" in lower or "kal" in lower or "aaj" in lower:
            query = "weather forecast tomorrow" if "kal" in lower else "weather today"
            steps = [PlanStep(
                step_id=1, tool="search_web", action="Search for weather information",
                params={"query": query}, retryable=True
            )]

        elif "search" in lower or "find" in lower or "lookup" in lower:
            steps = [PlanStep(
                step_id=1, tool="search_web", action="Search the web for information",
                params={"query": intent}, retryable=True
            )]

        elif "read" in lower or "open" in lower:
            # Best-effort extract filename
            parts = intent.split()
            name = parts[-1] if parts else "file.txt"
            steps = [PlanStep(
                step_id=1, tool="read_file", action="Read the requested file",
                params={"name": name}, retryable=True
            )]

        else:
            # Generic fallback: web search on the full intent
            steps = [PlanStep(
                step_id=1, tool="search_web", action="Search for information related to the request",
                params={"query": intent}, retryable=True
            )]

        return Plan(goal=intent, steps=steps)
