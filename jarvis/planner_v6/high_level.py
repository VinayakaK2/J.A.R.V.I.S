import json
import logging
from typing import List, Dict, Any

from config.settings import settings

logger = logging.getLogger(__name__)

class HighLevelPlanner:
    def __init__(self):
        self.is_enabled = bool(settings.openai_api_key and not settings.openai_api_key.startswith("your_"))
        
    def generate_strategy(self, user_intent: str, active_context: str = "") -> Dict[str, Any]:
        """
        Translates raw human instruction into a logical chunked list of subgoals.
        Output format: {"goal": "...", "subgoals": ["...", "..."]}
        """
        if not self.is_enabled:
            return {"goal": user_intent, "subgoals": [user_intent]}

        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)

            prompt = f"""You are the High-Level Reasoning Node for JARVIS v6.
Your job is NOT to pick specific tools. Your job is to break a complex intent into generic logical SUB-GOALS.
Current Intent: "{user_intent}"
Context/Previous Output: "{active_context}"

Reply STRICTLY in JSON format:
{{
  "goal": "<cleaned up, professional phrasing of the intent>",
  "subgoals": [
     "Open Spotify",
     "Search for 'Lofi beats'",
     "Confirm playlist has started"
  ]
}}"""

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=800
            )

            raw = response.choices[0].message.content.strip()
            if raw.startswith("```json"): raw = raw.split("```json")[1].split("```")[0]
            elif raw.startswith("```"): raw = raw.split("```")[1]
            return json.loads(raw.strip())
        except Exception as e:
            logger.error(f"[HighLevelPlanner] Strategy generation failed: {e}")
            return {"goal": user_intent, "subgoals": [user_intent]}

hl_planner = HighLevelPlanner()
