import json
import logging
from typing import Dict, Any

from config.settings import settings

logger = logging.getLogger(__name__)

class FailureEngine:
    def __init__(self):
        self.is_enabled = bool(settings.openai_api_key and not settings.openai_api_key.startswith("your_"))
        
    def analyze_failure(self, step_action: str, raw_error: str, contextual_ui_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Translates raw OS or Vision exceptions into actionable categorized JSON failure nodes.
        Outputs: failure_type, reason, context, recommended_fix
        """
        fallback = {
            "failure_type": "unknown_error",
            "reason": str(raw_error),
            "context": "Context unavailable",
            "recommended_fix": "Retry or abort"
        }
        
        if not self.is_enabled:
            return fallback

        try:
            logger.info(f"[FailureEngine] Analyzing traceback for: {step_action}")
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)

            ui_state_str = json.dumps(contextual_ui_state).strip()[:1000] # Ensure we don't blow up token limits
            
            prompt = f"""You are the JARVIS Failure Diagnostics Engine (v6).
A systematic error occurred during an action step. Analyze it and categorise the failure strictly into a structured schema.

Attempted Action: "{step_action}"
Raw Exception Trace: "{raw_error}"
Last Known UI State: "{ui_state_str}"

Provide the output strictly as JSON format:
{{
  "failure_type": "element_not_found" | "timeout" | "authorization_blocked" | "api_error" | "logic_constraint",
  "reason": "<One sentence explaining what went wrong logically>",
  "context": "<Relevant bounds or OS states involved>",
  "recommended_fix": "<Actionable instruction for the Replanner to dynamically fix this>"
}}"""

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```json"): raw = raw.split("```json")[1].split("```")[0]
            elif raw.startswith("```"): raw = raw.split("```")[1]
            return json.loads(raw.strip())
        except Exception as e:
            logger.error(f"[FailureEngine] Diagnostics evaluation failed: {e}")
            return fallback

failure_diagnostics = FailureEngine()
