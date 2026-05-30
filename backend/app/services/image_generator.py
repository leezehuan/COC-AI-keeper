from __future__ import annotations

from typing import Any

import httpx
from openai import OpenAI
from sqlalchemy.orm import Session

from app import models
from app.config import get_settings
from app.services.prompt_config import build_image_prompt_optimizer


class ImageGenerator:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._init_prompt_optimizer()

    def _init_prompt_optimizer(self) -> None:
        base_url = self.settings.drawing_llm_base_url or self.settings.llm_base_url or None
        api_key = self.settings.drawing_llm_api_key or self.settings.llm_api_key or "empty"
        self.prompt_model = self.settings.drawing_llm_model or self.settings.llm_model or ""
        self.prompt_client = OpenAI(api_key=api_key, base_url=base_url)

    def optimize_prompt(self, raw_prompt: str) -> str:
        if not self.prompt_model:
            return raw_prompt
        messages = build_image_prompt_optimizer(raw_prompt)
        try:
            response = self.prompt_client.chat.completions.create(
                model=self.prompt_model,
                messages=messages,
                temperature=0.7,
            )
            optimized = response.choices[0].message.content or ""
            return optimized.strip() or raw_prompt
        except Exception:
            return raw_prompt

    def generate_image(self, prompt: str, scene_type: str) -> dict[str, Any] | None:
        api_key = self.settings.drawing_api_key
        api_url = self.settings.drawing_api_url
        model = self.settings.drawing_model
        if not api_key or not api_url or not model:
            return None

        size = "16:9" if scene_type == "new_scene" else "1:1"
        payload = {
            "model": model,
            "mode": "generate",
            "size": size,
            "response_format": "url",
            "prompt": prompt,
            "n": 1,
        }
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    api_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()
                if data.get("data") and isinstance(data["data"], list) and len(data["data"]) > 0:
                    image_data = data["data"][0]
                    return {
                        "url": image_data.get("url", ""),
                        "size": image_data.get("size", ""),
                        "model": data.get("model", ""),
                    }
        except Exception:
            return None
        return None

    def generate_and_save(
        self,
        db: Session,
        turn_log_id: str,
        narration: str,
        scene_type: str,
    ) -> str | None:
        turn_log = db.get(models.TurnLog, turn_log_id)
        if turn_log is None:
            return None

        raw_prompt = narration[:500]
        optimized = self.optimize_prompt(raw_prompt)
        result = self.generate_image(optimized, scene_type)
        if result is None:
            return None

        turn_log.image_url = result["url"]
        turn_log.image_metadata = {
            "scene_type": scene_type,
            "prompt_raw": raw_prompt,
            "prompt_optimized": optimized,
            "size": result.get("size"),
            "model": result.get("model"),
        }
        db.commit()
        return result["url"]
