"""OpenAI provider using the Responses API with Structured Outputs.

Selected when LLM_PROVIDER=openai and OPENAI_API_KEY is set. Emits only strict
JSON (DesignSpec / DesignModification) — never code. All output is re-validated
by Pydantic downstream, which is the real safety boundary.

A client may be injected (for tests); otherwise one is built lazily from
settings so importing this module never requires network/credentials.
"""
from __future__ import annotations

import json

from app.config import settings
from app.llm.base import (
    CAD_PLAN_SYSTEM_PROMPT,
    DRAWING_SYSTEM_PROMPT,
    FEATURE_GRAPH_SYSTEM_PROMPT,
    GENERAL_CAD_PLAN_SYSTEM_PROMPT,
    MODIFICATION_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    LLMProvider,
    LLMUnavailableError,
)
from app.observability import log_event
from app.llm.schemas import (
    CAD_FEATURE_GRAPH_SCHEMA,
    CAD_PLAN_SCHEMA,
    DESIGN_MODIFICATION_SCHEMA,
    DESIGN_SPEC_SCHEMA,
    DRAWING_INTERPRETATION_SCHEMA,
    GENERAL_CAD_PLAN_SCHEMA,
)

_CLARIFY_SYSTEM = (
    "You are a CAD intake assistant. The user's request could not be turned into "
    "a valid part. In one short, friendly sentence, ask for the single most "
    "important missing or invalid detail. Output plain text only."
)
_EXPLAIN_SYSTEM = (
    "You explain a generated mechanical part to a maker in 2-3 plain sentences: "
    "what it is, its key dimensions and holes, and how to make it. No markdown."
)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, client=None) -> None:
        if client is not None:
            self._client = client
        else:
            if not settings.openai_api_key:
                raise RuntimeError(
                    "OpenAI API key missing — set OPENAI_API_KEY (in .env, not "
                    ".env.example) to use the OpenAI provider."
                )
            try:
                from openai import OpenAI  # lazy: optional dependency
            except ModuleNotFoundError as exc:  # pragma: no cover - env-specific
                raise RuntimeError(
                    "The 'openai' package is not installed. Run "
                    "`pip install -r requirements.txt` to enable the OpenAI provider."
                ) from exc

            kwargs = {
                "api_key": settings.openai_api_key,
                # Per-request timeout + automatic retries on transient
                # 429/5xx/connection errors (SDK applies exponential backoff).
                "timeout": settings.openai_timeout_seconds,
                "max_retries": settings.openai_max_retries,
            }
            if settings.openai_base_url:
                kwargs["base_url"] = settings.openai_base_url
            self._client = OpenAI(**kwargs)
        self._model = settings.openai_model
        self._reasoning_effort = settings.openai_reasoning_effort
        # Model fallback chains. We never assume the configured model id is valid:
        # if a request fails outright (e.g. an unknown OPENAI_MODEL / CAD_LLM_MODEL)
        # we transparently re-issue the SAME request against the next known-good
        # model before surfacing a clean error. Known-good general models come last.
        self._text_models = _dedupe([self._model, "gpt-4.1", "gpt-4o", "gpt-4o-mini"])
        # The CAD planner prefers the (possibly newer) CAD model, then degrades.
        self._cad_models = _dedupe(
            [settings.cad_llm_model, "gpt-5.1", "gpt-4.1", "gpt-4o", self._model]
        )

    # --- low-level calls --------------------------------------------------
    def _request_kwargs(self, system: str, user: str, model: str | None = None) -> dict:
        kwargs: dict = {
            "model": model or self._model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self._reasoning_effort:
            kwargs["reasoning"] = {"effort": self._reasoning_effort}
        return kwargs

    def _run(self, factory, models: list[str], label: str, parse=None):
        """Issue a Responses call across a model fallback chain.

        ``factory(model)`` builds the request kwargs for a given model. On any
        failure (timeout, unknown model, transport error, unparseable output) we
        try the next model; once the chain is exhausted we raise a user-safe
        ``LLMUnavailableError`` (never a raw provider stack trace)."""
        last_exc: Exception | None = None
        for idx, model in enumerate(models):
            try:
                resp = self._client.responses.create(**factory(model))
                text = _output_text(resp)
                result = parse(text) if parse else text
                if idx > 0:
                    log_event("openai_model_fallback", label=label, used=model)
                return result
            except Exception as exc:  # noqa: BLE001 - try the next model in the chain
                last_exc = exc
                log_event(
                    "openai_call_failed", label=label, model=model,
                    error_type=type(exc).__name__, detail=str(exc)[:200],
                )
        raise LLMUnavailableError(
            "The AI service is temporarily unavailable — please try again in a "
            "moment. If this keeps happening, the configured model may be invalid "
            "(check OPENAI_MODEL / CAD_LLM_MODEL)."
        ) from last_exc

    def _structured(self, system: str, user: str, schema: dict, name: str,
                    models: list[str] | None = None) -> dict:
        def factory(model: str) -> dict:
            kwargs = self._request_kwargs(system, user, model)
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": name,
                    # dimensions is an open map, so strict mode can't apply;
                    # Pydantic re-validates everything regardless.
                    "strict": False,
                    "schema": schema,
                }
            }
            return kwargs

        return self._run(factory, models or self._text_models, label=name, parse=json.loads)

    def _text(self, system: str, user: str) -> str:
        return self._run(
            lambda model: self._request_kwargs(system, user, model),
            self._text_models, label="text", parse=lambda t: t.strip(),
        )

    # --- interface --------------------------------------------------------
    def parse_prompt(self, prompt: str) -> dict:
        return self.parse_prompt_to_design_spec(prompt)

    def parse_prompt_to_design_spec(self, prompt: str) -> dict:
        return self._structured(SYSTEM_PROMPT, prompt, DESIGN_SPEC_SCHEMA, "design_spec")

    def parse_modification(self, prompt: str, current_spec: dict) -> dict:
        user = f"Current design spec:\n{json.dumps(current_spec)}\n\nChange request: {prompt}"
        return self._structured(
            MODIFICATION_SYSTEM_PROMPT, user, DESIGN_MODIFICATION_SCHEMA, "design_modification"
        )

    def repair(self, prompt: str, previous: dict, errors: str) -> dict | None:
        user = (
            f"Original request: {prompt}\n\nYour previous JSON:\n{json.dumps(previous)}\n\n"
            f"It failed validation with these errors:\n{errors}\n\n"
            "Return corrected JSON that fixes every error."
        )
        try:
            return self._structured(SYSTEM_PROMPT, user, DESIGN_SPEC_SCHEMA, "design_spec")
        except Exception:  # noqa: BLE001 - repair is best-effort
            return None

    def generate_clarification_question(self, prompt: str, errors: str = "") -> str:
        user = f"Request: {prompt}\nProblem: {errors or 'missing critical details'}"
        return self._text(_CLARIFY_SYSTEM, user)

    def generate_explanation(self, spec: dict) -> str:
        return self._text(_EXPLAIN_SYSTEM, json.dumps(spec))

    def plan_cad(self, prompt: str, feedback: str | None = None) -> dict | None:
        user = prompt
        if feedback:
            user = (
                f"Original request: {prompt}\n\nYour previous CAD plan failed "
                f"validation:\n{feedback}\n\nReturn a corrected CadPlan that fixes "
                "every failure (keep the requested dimensions exact)."
            )
        # The planner degrades to the legacy/deterministic pipeline on failure,
        # so a provider outage here returns None rather than raising.
        try:
            return self._structured(
                CAD_PLAN_SYSTEM_PROMPT, user, CAD_PLAN_SCHEMA, "cad_plan",
                models=self._cad_models,
            )
        except LLMUnavailableError:
            log_event("cad_plan_failed", reason="provider_unavailable")
            return None

    def plan_feature_graph(self, prompt: str) -> dict | None:
        try:
            return self._structured(
                FEATURE_GRAPH_SYSTEM_PROMPT, prompt, CAD_FEATURE_GRAPH_SCHEMA, "cad_feature_graph"
            )
        except Exception:  # noqa: BLE001 - fall back to template/clarification
            return None

    def plan_general_cad(self, prompt: str) -> dict | None:
        try:
            return self._structured(
                GENERAL_CAD_PLAN_SYSTEM_PROMPT, prompt, GENERAL_CAD_PLAN_SCHEMA, "general_cad_plan"
            )
        except Exception:  # noqa: BLE001 - fall back to clarification
            return None

    def interpret_drawing(
        self, image_b64: str, media_type: str = "image/png", hint: str | None = None
    ) -> dict:
        instruction = DRAWING_SYSTEM_PROMPT
        if hint:
            instruction += f"\n\nUser's correction/hint about this drawing: {hint}"

        def factory(model: str) -> dict:
            kwargs: dict = {
                "model": model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": instruction},
                            {
                                "type": "input_image",
                                # High detail matters for reading mechanical drawings.
                                "detail": "high",
                                "image_url": f"data:{media_type};base64,{image_b64}",
                            },
                        ],
                    }
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "drawing_interpretation",
                        "strict": False,
                        "schema": DRAWING_INTERPRETATION_SCHEMA,
                    }
                },
            }
            if self._reasoning_effort:
                kwargs["reasoning"] = {"effort": self._reasoning_effort}
            return kwargs

        log_event(
            "openai_interpret_drawing",
            model=self._model,
            media_type=media_type,
            image_b64_len=len(image_b64),
            image_passed_as="data_url",
            has_hint=bool(hint),
        )
        # All fallback models (gpt-4.1/gpt-4o/-mini) are vision-capable. On total
        # failure this raises LLMUnavailableError, which interpret_image catches
        # and surfaces to the user as a provider_error (never a crash).
        return self._run(
            factory, self._text_models, label="drawing_interpretation", parse=json.loads
        )


def _dedupe(models: list[str]) -> list[str]:
    """Order-preserving de-duplication of a model fallback chain (drops blanks)."""
    seen: set[str] = set()
    return [m for m in models if m and not (m in seen or seen.add(m))]


def _output_text(resp) -> str:
    """Extract text from a Responses API result across SDK shapes."""
    # Newer SDKs expose a convenience aggregate.
    text = getattr(resp, "output_text", None)
    if text:
        return text
    # Fallback: walk the output items.
    chunks: list[str] = []
    for item in getattr(resp, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            t = getattr(part, "text", None)
            if t:
                chunks.append(t)
    if chunks:
        return "".join(chunks)
    raise ValueError("OpenAI response contained no text output")
