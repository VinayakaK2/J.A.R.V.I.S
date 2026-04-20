import json
import logging
from config.settings import settings

logger = logging.getLogger(__name__)

def classify_task(user_input: str) -> dict:
    """
    Classifies a task into a specific domain and determines complexity.
    """
    is_enabled = bool(settings.openai_api_key and not settings.openai_api_key.startswith("your_"))
    
    fallback = {
        "task_type": "general",
        "complexity": "low"
    }

    if not is_enabled:
        return fallback

    try:
        from openai import OpenAI
        client = OpenAI(api_key=settings.openai_api_key)
        
        prompt = f"""You are the JARVIS Context Classifier.
Analyze the following user input and categorize the task.

Input: "{user_input}"

Categories for task_type: debugging, automation, writing, architecture, devops, database, frontend, api, general.
Categories for complexity: low, medium, high.

Return ONLY a JSON object. Example:
{{"task_type": "debugging", "complexity": "medium"}}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```json"): raw = raw.split("```json")[1].split("```")[0]
        elif raw.startswith("```"): raw = raw.split("```")[1]
        
        data = json.loads(raw.strip())
        return {
            "task_type": data.get("task_type", "general"),
            "complexity": data.get("complexity", "low")
        }
    except Exception as e:
        logger.error(f"[Context Classifier] LLM classification failed: {e}")
        return fallback
