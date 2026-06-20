"""Live OpenAI smoke test (NOT run in CI/verify).

    LLM_PROVIDER=openai OPENAI_API_KEY=sk-... python -m scripts.smoke_openai

Calls the real OpenAI structured-output provider for a prompt and a modification,
validates the results through the same Pydantic pipeline used in production, and
prints what was produced. Requires network + a valid key.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.parsing.modification_parser import parse_and_apply  # noqa: E402
from app.parsing.prompt_parser import parse_prompt  # noqa: E402


def main() -> int:
    if settings.llm_provider != "openai" or not settings.openai_api_key:
        print("Set LLM_PROVIDER=openai and OPENAI_API_KEY to run this smoke test.")
        return 2

    prompt = "Mounting bracket 80mm wide, 40mm deep, 5mm thick with two M6 holes."
    print(f"== parse_prompt: {prompt}")
    result = parse_prompt(prompt)
    if result.spec is None:
        print("  clarification:", result.clarification_question)
        return 1
    print("  object_type:", result.spec.object_type)
    print("  dimensions :", result.spec.dimensions)
    print("  holes      :", [h.model_dump() for h in result.spec.holes])
    print("  assumptions:", result.assumptions)

    print("== parse_modification: 'make it 120mm wide and add rounded edges'")
    mod = parse_and_apply("make it 120mm wide and add rounded edges", result.spec)
    if mod.spec is not None:
        print("  new width:", mod.spec.dimensions.get("width"))
        print("  fillet   :", mod.spec.fillet_radius)
        print("  summary  :", mod.summary)
    else:
        print("  clarification:", mod.clarification_question)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
