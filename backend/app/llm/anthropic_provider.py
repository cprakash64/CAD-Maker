"""Anthropic-backed provider. Returns structured JSON only (never code).

Selected when LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY is set. Falls back
to a clear error if the key is missing — we never hardcode keys.
"""
from __future__ import annotations

import json

from app.config import settings
from app.llm.base import CAD_PLAN_SYSTEM_PROMPT, SYSTEM_PROMPT, LLMProvider


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        import anthropic  # imported lazily so the dep is optional

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    def _json(self, system: str, user: str, max_tokens: int = 2048) -> dict:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        ).strip()
        return json.loads(_strip_fences(text))

    def parse_prompt(self, prompt: str) -> dict:
        return self._json(SYSTEM_PROMPT, prompt, max_tokens=1024)

    def plan_cad(self, prompt: str, feedback: str | None = None) -> dict | None:
        """CadPlan via the Anthropic adapter (provider-fallback path)."""
        user = prompt
        if feedback:
            user = (
                f"Original request: {prompt}\n\nYour previous CAD plan failed "
                f"validation:\n{feedback}\n\nReturn a corrected CadPlan JSON that "
                "fixes every failure. Output ONLY the JSON object."
            )
        try:
            return self._json(CAD_PLAN_SYSTEM_PROMPT, user)
        except Exception:  # noqa: BLE001 - fall back to the deterministic planner
            return None


def _strip_fences(text: str) -> str:
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()
