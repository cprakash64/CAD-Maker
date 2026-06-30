"""Object Intelligence layer — resolve real-world objects (boards, motors, bearings,
custom PCBs, …) from a prompt into a source-tracked structured spec + a buildable
CAD family, with dimension trust levels so GPT-estimated dimensions can never PASS.
"""
from __future__ import annotations

from app.cad.object_intelligence.confidence import (
    can_pass,
    status_ceiling,
    status_to_validation,
)
from app.cad.object_intelligence.mechanical_spec import MechanicalObjectSpec
from app.cad.object_intelligence.resolver import ObjectResolution, resolve_object

__all__ = [
    "MechanicalObjectSpec",
    "ObjectResolution",
    "resolve_object",
    "can_pass",
    "status_ceiling",
    "status_to_validation",
]
