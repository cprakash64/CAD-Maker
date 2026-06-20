"""Stable feature metadata carried with every generated model.

Feature IDs are deterministic from the spec (e.g. ``hole_2``, ``flange_main_front``,
``journal_rod_1``) so a selection stays valid across regenerations. ``anchor`` is
a representative point in CadQuery model space (mm, Z-up) that the viewer projects
to screen for circle/lasso hit-testing.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FeatureSpec(BaseModel):
    id: str = Field(max_length=64)
    type: str = Field(max_length=32)  # hole | face | edge | flange | boss | vent | web | journal | bolt_pattern | body
    label: str = Field(max_length=80)
    anchor: tuple[float, float, float] = (0.0, 0.0, 0.0)
    meta: dict = Field(default_factory=dict)
