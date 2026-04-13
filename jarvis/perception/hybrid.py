import os
import io
import json
import base64
import logging
from typing import Dict, Any

from PIL import Image
import pytesseract
import mss

from config.settings import settings
from system.monitor import monitor_api

logger = logging.getLogger(__name__)

class HybridPerception:
    def __init__(self):
        # Allow fallback for windows users testing locally against a custom Tesseract install
        if os.name == "nt" and os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
            pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            
        self.is_enabled = bool(
            settings.openai_api_key and not settings.openai_api_key.startswith("your_")
        )

    def capture_screenshot(self) -> bytes:
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                sct_img = sct.grab(monitor)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                max_size = (1920, 1080)
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='JPEG', quality=85)
                return img_byte_arr.getvalue()
        except Exception as e:
            logger.error(f"[HybridPerception] Capture failed: {e}")
            return b""

    def extract_ocr(self, img_bytes: bytes) -> Dict[str, Any]:
        """Extracts text structures deterministically from an image using Tesseract."""
        try:
            image = Image.open(io.BytesIO(img_bytes))
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
            
            elements = []
            for i in range(len(data['text'])):
                word = data['text'][i].strip()
                if word:
                    elements.append({
                        "text": word,
                        "x": data['left'][i],
                        "y": data['top'][i],
                        "w": data['width'][i],
                        "h": data['height'][i]
                    })
            
            # Simple heuristic mapping for text blobs
            return {"texts": elements, "buttons": [], "inputs": [], "layout": elements}
        except Exception as e:
            logger.warning(f"[HybridPerception] OCR failed (Tesseract may not be installed): {e}")
            return {"texts": [], "buttons": [], "inputs": [], "layout": []}

    @monitor_api.track_cost(model_name="gpt-4o", tokens_est=400)
    def analyze_screen(self, image_bytes: bytes, goal_context: str = "", expected_outcome: str = "") -> Dict[str, Any]:
        """Runs the hybrid OCR + Vision interpretation loop."""
        if not self.is_enabled or not image_bytes:
            return {
                "visible_apps": [], "buttons": [], "input_fields": [],
                "state_summary": "Vision API disabled.", "expected_fulfilled": True
            }

        ocr_data = self.extract_ocr(image_bytes)
        
        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
            
            base64_image = base64.b64encode(image_bytes).decode('utf-8')
            
            prompt = f"""You are JARVIS Hybrid Perception. Analyze the screen based on the image AND provided OCR data to ground your findings without hallucinations.
Recent Target/Goal: "{goal_context}"
Expected Outcome constraint: "{expected_outcome}"

OCR Text Bounding Boxes found:
{json.dumps(ocr_data['texts'][:15])} ... (truncated)

Return valid STRICT JSON:
{{
  "state_summary": "1 sentence what screen is primarily showing",
  "expected_fulfilled": true/false (true ONLY if expected outcome is met literally on screen)
}}"""

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
                ],
                max_tokens=300,
                temperature=0.0
            )
            raw_content = response.choices[0].message.content.strip()
            if raw_content.startswith("```json"): raw_content = raw_content.split("```json")[1].split("```")[0]
            elif raw_content.startswith("```"): raw_content = raw_content.split("```")[1]
            return json.loads(raw_content.strip())
        except Exception as e:
            logger.error(f"[HybridPerception] LLM reasoning layer failed: {e}")
            return {"state_summary": str(e), "expected_fulfilled": False}

perception_hybrid = HybridPerception()
