"""Estimated LLM cost from token usage."""
from app.llm.pricing import estimate_cost_usd


def test_known_model_uses_its_own_price():
    cost = estimate_cost_usd("gpt-4o-mini", 1_000_000, 0)
    assert cost == 0.15


def test_output_tokens_priced_separately():
    cost = estimate_cost_usd("gpt-4o-mini", 0, 1_000_000)
    assert cost == 0.60


def test_zero_tokens_is_zero_cost():
    assert estimate_cost_usd("gpt-4o-mini", 0, 0) == 0.0


def test_unknown_model_falls_back_to_default_and_does_not_raise():
    cost = estimate_cost_usd("some-future-model-id", 1_000_000, 1_000_000)
    assert cost > 0
