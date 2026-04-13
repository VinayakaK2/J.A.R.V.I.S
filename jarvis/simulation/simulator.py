import logging
import json
from typing import Dict, Any

from config.settings import settings
from planner import Plan
from learning.patterns import patterns_engine

logger = logging.getLogger(__name__)

class ExecutionSimulator:
    def __init__(self):
        self.is_enabled = bool(settings.openai_api_key and not settings.openai_api_key.startswith("your_"))
        
    def simulate_plan(self, plan: Plan, current_context: str) -> Dict[str, Any]:
        """
        Runs the generated plan logically tracking outputs WITHOUT executing structural changes natively.
        Determines probabilistic success, isolating missing boundaries before running commands.
        """
        if not self.is_enabled or not settings.use_simulation:
            # Fallback pass-through when disabled
            return {
                "success_probability": 1.0, 
                "risky_steps": [], 
                "estimated_cost": 0.01,
                "simulate_verdict": "pass"
            }

        try:
            logger.info(f"[Simulator] Running simulation against Plan: {plan.goal}")
            # Pull historical pattern data
            history_bias = patterns_engine.get_bias_for_goal(plan.goal)
            
            steps_repr = json.dumps([
                {"id": s.step_id, "tool": s.tool, "expected": getattr(s, 'expected_outcome', 'None')} 
                for s in plan.steps
            ])

            prompt = f"""You are JARVIS v6 Execution Simulator Node.
Evaluate the proposed Execution Plan logically WITHOUT running it.
Calculate risk by matching against contextual anomalies or historical bias.

Current Context: "{current_context}"
Historical DB Bias: "{history_bias}"

Proposed Steps Pipeline:
{steps_repr}

Analyze the sequence. Is a step practically guaranteeing a failure (e.g. clicking before opening)?
Does it rely on inputs that don't exist?

Return strictly as JSON format:
{{
  "success_probability": 0.0 to 1.0,
  "risky_steps": [ step_id1, step_id2 ],
  "simulated_outcome_reasoning": "1 sentence why it will pass/fail",
  "simulate_verdict": "pass" or "reject" (reject explicitly halts execution)
}}"""

            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",  # Mini is ideal for fast probabilistic simulation
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith("```json"): raw = raw.split("```json")[1].split("```")[0]
            elif raw.startswith("```"): raw = raw.split("```")[1]
            out = json.loads(raw.strip())
            
            logger.info(f"[Simulator] Verdict: {out.get('simulate_verdict')} (Prob: {out.get('success_probability')})")
            return out
        except Exception as e:
            logger.error(f"[Simulator] Simulation failed returning fallback permit: {e}")
            return {
                "success_probability": 0.8, 
                "risky_steps": [], 
                "simulate_verdict": "pass"
            }

simulator_node = ExecutionSimulator()
