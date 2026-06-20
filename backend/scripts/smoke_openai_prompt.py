"""Live OpenAI prompt-to-CAD smoke test (NOT run in CI/verify).

    LLM_PROVIDER=openai OPENAI_API_KEY=sk-... \\
        python -m scripts.smoke_openai_prompt "Drill jig plate 120mm by 80mm ..."

Runs a single prompt through the real generate-first pipeline and prints what
happened, generating STL/STEP to ./smoke_out if a model was produced.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.export.exporter import generate  # noqa: E402
from app.parsing.complex_plan import build_complex_plan, looks_complex  # noqa: E402
from app.parsing.prompt_parser import parse_prompt  # noqa: E402
from app.schemas.design_spec import DesignSpec  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    prompt = sys.argv[1]
    print(f"provider/model : {settings.llm_provider} / {settings.openai_model}")
    print(f"routing        : {'complex_plan' if looks_complex(prompt) else 'simple_parser'}")

    if looks_complex(prompt):
        plan = build_complex_plan(prompt)
        print(f"classification : {plan.classification.kind} "
              f"({plan.classification.template_candidate})")
        spec = (
            DesignSpec(object_type=plan.template_object_type, units="mm",
                       dimensions={k: v for k, v in plan.template_dimensions.items() if v > 0})
            if plan.template_object_type else None
        )
        assumptions = (plan.materials and ["materials: " + ", ".join(plan.materials)]) or []
        clarification = plan.clarification_question
    else:
        result = parse_prompt(prompt)
        spec = result.spec
        assumptions = result.assumptions
        clarification = result.clarification_question

    print(f"template       : {spec.object_type if spec else None}")
    print(f"assumptions    : {assumptions}")
    print(f"clarification  : {clarification}")
    if spec is None:
        print("=> no CAD generated (clarification needed)")
        return 0

    out = Path("smoke_out"); out.mkdir(exist_ok=True)
    gen = generate(spec)
    (out / f"{spec.object_type}.stl").write_bytes(gen.stl_bytes)
    (out / f"{spec.object_type}.step").write_bytes(gen.step_bytes)
    print(f"=> generated: {out / (spec.object_type + '.stl')} "
          f"({len(gen.stl_bytes)}B), .step ({len(gen.step_bytes)}B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
