"""Live OpenAI vision smoke test for Drawing-to-CAD Assist (NOT run in CI/verify).

    LLM_PROVIDER=openai OPENAI_API_KEY=sk-... \\
        python -m scripts.smoke_drawing_image_openai path/to/image.png ["optional hint"]

Calls the real OpenAI vision provider, validates DrawingInterpretationSpec, prints
the detection + confidence + dimensions + assumptions, and generates CAD if the
interpretation is actionable. Requires network + a valid key.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.drawing.interpret import interpret_image, to_design_spec  # noqa: E402


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Drawing-to-CAD OpenAI smoke test")
    ap.add_argument("image")
    ap.add_argument("--hint", default=None, help="correction hint describing the part")
    args = ap.parse_args()
    image_path = Path(args.image)
    hint = args.hint
    if not image_path.exists():
        print(f"No such file: {image_path}")
        return 2
    if settings.llm_provider != "openai" or not settings.openai_api_key:
        print("Set LLM_PROVIDER=openai and OPENAI_API_KEY to run this smoke test.")
        return 2

    media = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
    interp = interpret_image(image_path.read_bytes(), media, hint=hint)

    print("=== Drawing interpretation ===")
    print(f"title               : {interp.title}")
    print(f"detected_object_type: {interp.detected_object_type}")
    print(f"template_candidate  : {interp.template_candidate}")
    print(f"suggested_object_type: {interp.suggested_object_type}")
    print(f"overall_confidence  : {interp.overall_confidence}")
    print(f"units conf          : {interp.drawing_units_confidence}")
    print(f"view conf           : {interp.view_detection_confidence}")
    print(f"dimension conf      : {interp.dimension_extraction_confidence}")
    print(f"overall dimensions  : {interp.overall_dimensions}")
    print(f"holes/bolts         : {[h.model_dump() for h in interp.holes]}")
    print(f"missing critical    : {interp.missing_critical_dimensions}")
    print(f"assumptions         : {[a.assumption for a in interp.assumptions]}")
    print(f"clarifications      : {[q.question for q in interp.clarification_questions]}")
    print(f"unsupported_reason  : {interp.unsupported_reason}")
    print(f"rationale           : {interp.interpretation_rationale}")
    print(f"actionable          : {interp.is_actionable()}")

    if interp.provider_error:
        print(f"provider_error      : {interp.provider_error}")
    if interp.is_actionable():
        spec = to_design_spec(interp)
        if spec:
            from app.export.exporter import generate

            out = Path("smoke_out"); out.mkdir(exist_ok=True)
            gen = generate(spec)
            (out / f"{spec.object_type}.stl").write_bytes(gen.stl_bytes)
            (out / f"{spec.object_type}.step").write_bytes(gen.step_bytes)
            print(f"-> generated {spec.object_type}: {out / (spec.object_type + '.stl')} "
                  f"({len(gen.stl_bytes)}B), .step ({len(gen.step_bytes)}B)")
    else:
        print("-> Not actionable: add a --hint describing the part/dimensions and retry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
