"""Registry mapping object types to trusted template generators.

This is the *only* dispatch point from a validated spec to geometry. Adding a
part type means registering a template here — there is no dynamic code path.
"""
from __future__ import annotations

from app.cad.base import BaseTemplate
from app.cad.templates.adapter_plate import AdapterPlateTemplate
from app.cad.templates.bracket import RectangularBracketTemplate
from app.cad.templates.crankshaft import CrankshaftTemplate
from app.cad.templates.drill_jig import DrillJigTemplate
from app.cad.templates.flanged_pipe_branch import FlangedPipeBranchTemplate
from app.cad.templates.gear_pulley import GearPulleyTemplate
from app.cad.templates.enclosure import EnclosureTemplate
from app.cad.templates.handle import HandleTemplate
from app.cad.templates.l_bracket import LBracketTemplate
from app.cad.templates.pipe_clamp import PipeClampTemplate
from app.cad.templates.spacer import SpacerTemplate

_TEMPLATES: dict[str, BaseTemplate] = {
    t.object_type: t
    for t in (
        RectangularBracketTemplate(),
        LBracketTemplate(),
        EnclosureTemplate(),
        SpacerTemplate(),
        PipeClampTemplate(),
        DrillJigTemplate(),
        HandleTemplate(),
        AdapterPlateTemplate(),
        CrankshaftTemplate(),
        FlangedPipeBranchTemplate(),
        GearPulleyTemplate(),
    )
}


def get_template(object_type: str) -> BaseTemplate:
    try:
        return _TEMPLATES[object_type]
    except KeyError as exc:
        raise KeyError(f"No template registered for object_type '{object_type}'") from exc


def all_templates() -> dict[str, BaseTemplate]:
    return dict(_TEMPLATES)
