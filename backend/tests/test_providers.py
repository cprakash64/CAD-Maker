"""LLM provider conformance + OpenAI structured-output behavior (mocked API)."""
import json

import pytest

from app.llm.base import LLMProvider
from app.llm.mock_provider import MockLLMProvider
from app.llm.openai_provider import OpenAIProvider
from app.parsing.prompt_parser import parse_prompt
from app.schemas.design_spec import DesignSpec


# --- Fake OpenAI Responses client ----------------------------------------
class _Resp:
    def __init__(self, text: str):
        self.output_text = text


class FakeOpenAIClient:
    """Mimics client.responses.create for the Responses API."""

    def __init__(self, replies: dict[str, dict | str]):
        # Keyed by json_schema "name" (or "_text" for plain-text calls).
        self.replies = replies
        self.calls: list[dict] = []

    class _Responses:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls.append(kwargs)
            fmt = (kwargs.get("text") or {}).get("format") or {}
            key = fmt.get("name", "_text")
            reply = self.outer.replies[key]
            # A list is a queue consumed across successive calls (e.g. repair).
            if isinstance(reply, list):
                reply = reply.pop(0) if len(reply) > 1 else reply[0]
            return _Resp(reply if isinstance(reply, str) else json.dumps(reply))

    @property
    def responses(self):
        return FakeOpenAIClient._Responses(self)


def _openai(replies) -> OpenAIProvider:
    return OpenAIProvider(client=FakeOpenAIClient(replies))


# --- Conformance ----------------------------------------------------------
@pytest.mark.parametrize("provider", [MockLLMProvider(), _openai({})])
def test_providers_implement_interface(provider):
    assert isinstance(provider, LLMProvider)
    for method in ("parse_prompt", "parse_modification", "repair"):
        assert callable(getattr(provider, method))


def test_anthropic_class_conforms_without_instantiation():
    # Importing must not require an API key; the class must expose the interface.
    from app.llm.anthropic_provider import AnthropicProvider

    assert issubclass(AnthropicProvider, LLMProvider)
    for method in ("parse_prompt", "repair", "parse_modification"):
        assert hasattr(AnthropicProvider, method)


# --- OpenAI structured outputs -------------------------------------------
def test_openai_parse_prompt_uses_json_schema_and_parses():
    spec = {
        "object_type": "rectangular_bracket",
        "units": "mm",
        "manufacturing_method": "fdm_3d_print",
        "material": "PLA",
        "dimensions": {"width": 80, "depth": 40, "thickness": 5},
        "holes": [{"diameter": 6.6, "x": -25, "y": 0}],
        "assumptions": ["M6 -> 6.6mm"],
    }
    provider = _openai({"design_spec": spec})
    out = provider.parse_prompt("bracket 80x40x5 with two M6 holes")
    assert out["object_type"] == "rectangular_bracket"
    # It requested a json_schema structured output.
    fmt = provider._client.calls[-1]["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["name"] == "design_spec"


def test_openai_parse_prompt_feeds_validation_pipeline():
    spec = {
        "object_type": "rectangular_bracket",
        "dimensions": {"width": 80, "depth": 40, "thickness": 5},
        "holes": [],
    }
    result = parse_prompt("a bracket", provider=_openai({"design_spec": spec}))
    assert result.spec is not None
    assert isinstance(result.spec, DesignSpec)
    assert result.spec.object_type == "rectangular_bracket"


def test_openai_repair_retries_after_invalid_then_succeeds():
    bad = {"object_type": "rectangular_bracket", "dimensions": {"thickness": -5}}
    good = {"object_type": "rectangular_bracket", "dimensions": {"thickness": 5, "width": 80}}
    # First structured call returns bad; the repair retry returns good.
    provider = _openai({"design_spec": [bad, good]})
    result = parse_prompt("bracket", provider=provider)
    assert result.spec is not None and result.spec.dimensions["width"] == 80
    # Exactly two model calls were made (initial + one repair).
    assert len(provider._client.calls) == 2


def test_openai_parse_modification():
    mod = {"scale_dimensions": {"width": 1.25}, "summary": "wider"}
    provider = _openai({"design_modification": mod})
    out = provider.parse_modification("make it wider", {"object_type": "rectangular_bracket"})
    assert out["scale_dimensions"]["width"] == 1.25
    assert provider._client.calls[-1]["text"]["format"]["name"] == "design_modification"


def test_openai_text_helpers():
    provider = _openai({"_text": "Could you tell me the pipe diameter?"})
    q = provider.generate_clarification_question("pipe clamp", "missing pipe_diameter")
    assert "pipe" in q.lower()
    e = provider.generate_explanation({"object_type": "spacer"})
    assert isinstance(e, str) and len(e) > 0


class _FlakyClient:
    """Fails the first ``fail_first`` create() calls, then returns ``reply``.

    Simulates an invalid/unavailable model so the fallback chain can be exercised.
    """

    def __init__(self, reply: dict, fail_first: int):
        self.reply = reply
        self.fail_first = fail_first
        self.calls: list[dict] = []

    class _Responses:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls.append(kwargs)
            if len(self.outer.calls) <= self.outer.fail_first:
                raise RuntimeError("model_not_found: invalid model id")
            return _Resp(json.dumps(self.outer.reply))

    @property
    def responses(self):
        return _FlakyClient._Responses(self)


def test_openai_falls_back_to_next_model_on_failure():
    spec = {"object_type": "spacer", "dimensions": {}}
    provider = OpenAIProvider(client=_FlakyClient(spec, fail_first=1))
    out = provider.parse_prompt("a spacer")  # first model errors -> fallback succeeds
    assert out["object_type"] == "spacer"
    assert len(provider._client.calls) == 2
    # The two calls used different models (the fallback chain advanced).
    assert provider._client.calls[0]["model"] != provider._client.calls[1]["model"]


def test_openai_raises_user_safe_error_when_all_models_fail():
    from app.llm.base import LLMUnavailableError

    provider = OpenAIProvider(client=_FlakyClient({}, fail_first=99))
    with pytest.raises(LLMUnavailableError):
        provider.parse_prompt("anything")
    # It tried every model in the chain before giving up.
    assert len(provider._client.calls) == len(provider._text_models)


def test_openai_plan_cad_returns_none_when_unavailable():
    # plan_cad degrades to the deterministic planner, so it must NOT raise.
    provider = OpenAIProvider(client=_FlakyClient({}, fail_first=99))
    assert provider.plan_cad("a bracket") is None


def test_openai_passes_reasoning_effort(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "openai_reasoning_effort", "medium")
    provider = _openai({"design_spec": {"object_type": "spacer", "dimensions": {}}})
    provider.parse_prompt("a spacer")
    assert provider._client.calls[-1]["reasoning"] == {"effort": "medium"}
