"""CadPlan — a plain-English → parametric CAD feature-graph compiler.

This package replaces whole-part template routing with a strict, composable
feature graph:

    prompt → CadPlan (strict JSON, never code) → deterministic CadQuery compile
           → STEP + STL → validation (bbox / holes / through-holes / exports)

The LLM only ever emits a ``CadPlan`` (data). The compiler dispatches on a fixed
whitelist of feature ``kind`` values — there is no eval/exec and no
LLM-generated Python is executed.
"""
from __future__ import annotations

from app.cad.plan.schema import CadPlan, Expected, Feature, Operation

__all__ = ["CadPlan", "Expected", "Feature", "Operation"]
