import json
import logging
from typing import Dict, Any

from config.settings import settings
from planner import Plan

logger = logging.getLogger(__name__)

class CriticAgent:
    def __init__(self):
        self.is_enabled = bool(settings.openai_api_key and not settings.openai_api_key.startswith("your_"))
        
    def evaluate_plan(self, plan: Plan, skills_context: str = "", req_id: str = None) -> Dict[str, Any]:
        """
        Acts as a safety check-valve intercepting Low-Level plans. 
        Detects unhandled logic leaps or extreme security violations before Simulator runs.
        Returns {"approved": bool, "feedback": "Detailed string"}
        """
        if not self.is_enabled or not settings.use_critic_agent:
            return {"approved": True, "feedback": "Critic disabled or unsupported. Plan passed natively."}
            
        try:
            logger.info(f"[Critic] Reviewing proposed Execution Plan structure for '{plan.goal}'")
            steps_repr = json.dumps([
                {"id": s.step_id, "tool": s.tool, "expected": getattr(s, 'expected_outcome', 'None')} 
                for s in plan.steps
            ])
            
            prompt = f"""You are the JARVIS v6 Critic Agent.
Your job is to ruthlessly critique a proposed Low-Level Execution Plan.

Proposed Goal: "{plan.goal}"
Steps Sequence:
{steps_repr}

{skills_context}

Look explicitly for:
1. Missing prerequisite logical steps (e.g., trying to click a button before opening the application).
2. Overtly unsafe actions (e.g., attempting a system wipe).
3. Tool Discipline Violations: Reject arbitrary, unsafe, or generic tools like `run_terminal_command` or any OS shell executors. Ensure the planner ONLY uses whitelisted tools natively supplied by the registry.
4. Efficiency Checks: Detect unnecessary steps and suggest direct tool alternatives instead. For example, suggest using `open_url` directly instead of manually opening a browser and trying to click the address bar to type.

If the sequence is fundamentally sound, approve it. If there is a massive logic hole, invalid tool, or severe inefficiency, reject it.
Also evaluate the plan's overall quality considering the skills injected. Give a plan_quality_score between 0.0 and 1.0.
Return structured JSON:
{{
  "approved": true/false,
  "feedback": "1-2 sentences. If rejected, tell the low-level planner explicitly what tool it should use instead (e.g., 'Use open_url instead of run_terminal_command').",
  "plan_quality_score": 0.8
}}"""

            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```json"): raw = raw.split("```json")[1].split("```")[0]
            elif raw.startswith("```"): raw = raw.split("```")[1]
            out = json.loads(raw.strip())
            
            if out.get("approved"):
                logger.info(f"[Critic] Plan Approved: {out.get('feedback')}")
            else:
                logger.warning(f"[Critic] Plan REJECTED: {out.get('feedback')}")
                
            if out.get("plan_quality_score") is not None and req_id:
                from skills.metrics import skill_metrics
                skill_metrics.log_plan_quality(req_id, out.get("plan_quality_score"))
                
            return out
        except Exception as e:
            logger.error(f"[Critic] Evaluation failed inherently: {e}")
            return {"approved": True, "feedback": "Critic evaluation failed. Failing open."}

critic_node = CriticAgent()
