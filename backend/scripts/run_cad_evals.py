"""Run the plain-English → CAD eval prompts through a provider.

Offline (deterministic, no key):
    python -m scripts.run_cad_evals --provider mock

Live OpenAI (uses CAD_LLM_MODEL with gpt-5.1 → gpt-4.1 fallback):
    LLM_PROVIDER=openai OPENAI_API_KEY=... CAD_LLM_MODEL=gpt-5.5 \
        python -m scripts.run_cad_evals --provider openai

For each prompt it plans → compiles → validates (one repair pass on failure) and
prints whether the right primitives were composed and no legacy template was used.
Exit code is non-zero if any prompt fails, so it doubles as a CI gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.cad.base import CadGenerationError  # noqa: E402
from app.cad.plan.planner import build_and_validate, plan_from_prompt, repair_plan  # noqa: E402
from tests.test_plain_english_cad import LEGACY_TEMPLATES  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "tests" / "data" / "plain_english_cad_evals.json"


def _provider(name: str):
    import os

    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("TESTING", "true")
    if name == "openai":
        os.environ["LLM_PROVIDER"] = "openai"
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider()
    os.environ["LLM_PROVIDER"] = "mock"
    from app.llm.mock_provider import MockLLMProvider

    return MockLLMProvider()


# A required feature can be satisfied by an equivalent composition (the LLM may
# express "holes" as a pattern, or a spool as a pipe + two flanges).
_EQUIVALENT = {
    "hole": {"hole", "hole_pattern_rect", "hole_pattern_circle", "counterbore", "countersink"},
    "pipe_spool": {"pipe_spool", "pipe"},
    "plate": {"plate", "box"},
    "boss": {"boss", "cylinder"},
}


def _check(plan, outcome, expect: dict) -> list[str]:
    errs: list[str] = []
    if expect.get("forbid_template") and plan.object_type in LEGACY_TEMPLATES:
        errs.append(f"chose legacy template '{plan.object_type}'")
    kind_list = [f.kind.value for f in plan.features]
    kinds = set(kind_list)
    for k in expect.get("must_have", []):
        if not (_EQUIVALENT.get(k, {k}) & kinds):
            errs.append(f"missing feature '{k}' (or an equivalent)")
    if "flanges" in expect and kind_list.count("circular_flange") != expect["flanges"]:
        errs.append(f"expected {expect['flanges']} flanges, got {kind_list.count('circular_flange')}")
    if "min_pipes" in expect and kind_list.count("pipe") < expect["min_pipes"]:
        errs.append(f"expected >={expect['min_pipes']} pipes, got {kind_list.count('pipe')}")
    if outcome is not None:
        if "hole_count" in expect and outcome.result.hole_count != expect["hole_count"]:
            errs.append(f"hole_count {outcome.result.hole_count} != {expect['hole_count']}")
        if not outcome.report.passed:
            errs.append("validation: " + outcome.report.diagnostics().replace("\n", " "))
    else:
        errs.append("did not compile")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="mock", choices=["mock", "openai"])
    args = ap.parse_args()
    provider = _provider(args.provider)
    data = json.loads(DATA.read_text())

    passed = 0
    for entry in data["prompts"]:
        prompt, expect = entry["prompt"], entry["expect"]
        plan = plan_from_prompt(prompt, provider)
        if plan is None:
            print(f"[FAIL] {entry['id']:10s} (planner returned no plan)")
            continue
        outcome = None
        if not plan.clarification_required and plan.features:
            try:
                outcome = build_and_validate(plan)
                if not outcome.report.passed:
                    rep = repair_plan(prompt, plan, outcome.report.diagnostics(), provider)
                    if rep and rep.features:
                        retry = build_and_validate(rep)
                        if retry.report.passed:
                            plan, outcome = rep, retry
            except CadGenerationError as exc:
                outcome = None
                print(f"  compile error: {exc}")
        errs = _check(plan, outcome, expect)
        status = "PASS" if not errs else "FAIL"
        if not errs:
            passed += 1
        bbox = outcome.result.bbox_mm if outcome else None
        print(f"[{status}] {entry['id']:10s} {plan.object_type:18s} bbox={bbox}")
        for e in errs:
            print(f"         - {e}")

    total = len(data["prompts"])
    print(f"\n{passed}/{total} prompts passed ({args.provider} provider)")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
