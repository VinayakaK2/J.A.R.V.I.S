import json
import logging
from typing import Dict, Any, List

from config.settings import settings
from tools.registry import ToolRegistry
from planner import Plan, PlanStep  # Direct fallback dependency mirroring

logger = logging.getLogger(__name__)

class LowLevelPlanner:
    def __init__(self):
        self.registry = ToolRegistry()
        self.is_enabled = bool(settings.openai_api_key and not settings.openai_api_key.startswith("your_"))
        
    def _build_llm_prompt(self, strategy: Dict[str, Any], context: str, skills_context: str = "") -> str:
        tools_list = self.registry.get_tools_info()
        tools_str = "\n".join(
            f"- {info['name']}: {info['description']}" for info in tools_list
        )

        return f"""You are the JARVIS Low-Level Execution Mapper (v6).
Your job is to translate High-Level Strategy Subgoals directly into physical tool executions matching the Registry.

Strategy/Goal Context: "{strategy.get('goal')}"
Subgoals to accomplish:
{json.dumps(strategy.get('subgoals', []))}

{context}

{skills_context}

Available Tools (DO NOT INVENT TOOLS):
{tools_str}

CRITICAL RULES:
1. ONLY use tools exactly as named in the Available Tools list above.
2. NEVER use arbitrary tools. Specifically, DO NOT USE `run_terminal_command` or shell commands. It is considered an unsafe tool.
3. If you need to open an app or URL, use `open_application` or `open_url`. Do not use bash/terminal/cmd for this.

Required JSON format:
{{
  "plan_id": "<uuid>",
  "goal": "{strategy.get('goal')}",
  "steps": [
    {{
      "step_id": 1,
      "tool": "<tool_name_from_list>",
      "action": "<specific one-line description>",
      "expected_outcome": "<concrete state proving success>",
      "params": {{"<param_name>": "<value>"}},
      "depends_on": [],
      "retryable": true
    }}
  ]
}}"""

    def map_to_executable(self, strategy: Dict[str, Any], bias_context: str = "", skills_context: str = "") -> Plan:
        if not self.is_enabled:
            # Fallback trivial stub
            return Plan(goal=strategy.get("goal", "Goal"), steps=[
                PlanStep(step_id=1, tool="search_web", action="Fallback mapping", params={"query": strategy.get("goal")})
            ])

        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)

            prompt = self._build_llm_prompt(strategy, bias_context, skills_context)
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```json"): raw = raw.split("```json")[1].split("```")[0]
            elif raw.startswith("```"): raw = raw.split("```")[1]
            
            data = json.loads(raw.strip())
            return Plan(**data)
            
        except Exception as e:
            logger.error(f"[LowLevelPlanner] Executable Mapping failed: {e}")
            return Plan(goal="Fallback Plan", steps=[])

ll_planner = LowLevelPlanner()
