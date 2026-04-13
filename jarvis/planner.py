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
    def create_plan(self, user_intent: str, context: Optional[str] = None) -> Optional[Plan]:
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
        messages.append(HumanMessage(content=user_intent))

        try:
            response = llm.invoke(messages)
            return self._parse_plan(response.content, goal=user_intent)
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
