"""Single source of truth for the CAD prompt/template version in effect.

Bump this string whenever the LLM system prompt, the deterministic template
library, or the CadPlan compiler's routing rules change in a way that could
change generation output for the same input. Stored on every design's
``semantic_json["prompt_version"]`` at creation time (see
``design_service._attach_contract``) and surfaced in telemetry and bad-result
reports so a quality regression can be correlated to the prompt/template
version that produced it, not just the wall-clock date.
"""

CAD_PROMPT_VERSION = "2026.1"
