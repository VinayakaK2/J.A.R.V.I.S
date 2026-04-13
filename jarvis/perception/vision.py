import os
import io
import json
import base64
import logging
from typing import Dict, Any, Optional

import mss
from PIL import Image

from config.settings import settings

logger = logging.getLogger(__name__)

class PerceptionEngine:
    def __init__(self):
        self.is_enabled = bool(
            settings.openai_api_key and not settings.openai_api_key.startswith("your_")
        )
        
    def capture_screenshot(self) -> bytes:
        """Captures the primary monitor using mss and returns it as JPEG bytes."""
        try:
            with mss.mss() as sct:
                # Grab the primary monitor
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                
                # Convert to PIL Image
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                
                # Resize if necessary to save tokens/bandwidth
                max_size = (1920, 1080)
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                
                # Save to bytes
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='JPEG', quality=70)
                return img_byte_arr.getvalue()
        except Exception as e:
            logger.error(f"[Perception] Failed to capture screenshot: {e}")
            return b""

    def analyze_screen(self, image_bytes: bytes, goal_context: str = "") -> Dict[str, Any]:
        """
        Sends the screenshot to GPT-4o for visual analysis, 
        returning structured JSON detailing visible elements.
        """
        if not self.is_enabled or not image_bytes:
            return {
                "visible_apps": [],
                "buttons": [],
                "input_fields": [],
                "state_summary": "Vision disabled or failed to capture."
            }

        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            prompt = f"""You are JARVIS Vision Perception Layer. Analyze this screenshot.
Context of recent action/goal: "{goal_context}"

Return ONLY a valid JSON object matching this exact schema:
{{
  "visible_apps": ["App1", "App2"],
  "buttons": ["ButtonText1", "ButtonText2"],
  "input_fields": ["Search", "Username"],
  "state_summary": "Brief 1-sentence summary of what is happening on screen",
  "expected_fulfilled": true/false (true if the screen implies the context goal was just accomplished)
}}
Do NOT wrap the json in codeblocks or markdown."""

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=300,
                temperature=0.0
            )
            
            raw_content = response.choices[0].message.content.strip()
            
            if raw_content.startswith("```"):
                raw_content = raw_content.split("```")[1]
                if raw_content.startswith("json"):
                    raw_content = raw_content[4:]
                    
            return json.loads(raw_content)
            
        except Exception as e:
            logger.error(f"[Perception] Vision analysis failed: {e}")
            return {
                "visible_apps": [],
                "buttons": [],
                "input_fields": [],
                "state_summary": f"Error parsing vision: {str(e)}"
            }

# Global singleton
perception = PerceptionEngine()
