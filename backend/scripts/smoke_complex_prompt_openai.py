"""Live OpenAI complex-prompt smoke test (NOT run in CI/verify).

    LLM_PROVIDER=openai OPENAI_API_KEY=sk-... \\
        python -m scripts.smoke_complex_prompt_openai path/to/prompt.txt

Routes a long prompt through ComplexCADPlan with the real provider and prints the
classification + plan, then optionally generates CAD if it maps to a template.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.parsing.complex_plan import build_complex_plan  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    if settings.llm_provider != "openai" or not settings.openai_api_key:
        print("Set LLM_PROVIDER=openai and OPENAI_API_KEY to run this smoke test.")
        return 2

    prompt = Path(sys.argv[1]).read_text()
    print(f"== prompt ({len(prompt)} chars, {len(prompt.split())} words)")
    plan = build_complex_plan(prompt)
    c = plan.classification
    print("kind              :", c.kind)
    print("template_candidate:", c.template_candidate)
    print("confidence        :", c.confidence)
    print("template_object   :", plan.template_object_type)
    print("dimensions(mm)    :", plan.template_dimensions)
    print("materials         :", plan.materials)
    print("visual_notes      :", plan.visual_notes)
    print("clarification     :", plan.clarification_question)

    if plan.template_object_type:
        from app.export.exporter import generate
        from app.schemas.design_spec import DesignSpec

        spec = DesignSpec(
            object_type=plan.template_object_type, units="mm",
            dimensions={k: v for k, v in plan.template_dimensions.items() if v > 0},
        )
        gen = generate(spec)
        print(f"-> generated STL {len(gen.stl_bytes)}B, STEP {len(gen.step_bytes)}B, "
              f"bbox {gen.bounding_box_mm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
