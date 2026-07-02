"""Registry mapping object types to trusted template generators.

This is the *only* dispatch point from a validated spec to geometry. Adding a
part type means registering a template here — there is no dynamic code path.
"""
from __future__ import annotations

from app.cad.base import BaseTemplate
from app.cad.templates.adapter_plate import AdapterPlateTemplate
from app.cad.templates.bracket import RectangularBracketTemplate
from app.cad.templates.bearing_holder import BearingHolderTemplate
from app.cad.templates.crankshaft import CrankshaftTemplate
from app.cad.templates.device_enclosure import (
    BoardEnclosureTemplate,
    RPi4EnclosureTemplate,
    RPi5EnclosureTemplate,
)
from app.cad.templates.drill_jig import DrillJigTemplate
from app.cad.templates.generic_fitted_box import GenericFittedBoxTemplate
from app.cad.templates.motor_mount import MotorMountTemplate
from app.cad.templates.phone_holder import PhoneHolderTemplate
from app.cad.templates.rim import RimTemplate
from app.cad.templates.tire import TireTemplate
from app.cad.templates.wheel_assembly import WheelAssemblyTemplate
from app.cad.templates.flanged_pipe_branch import FlangedPipeBranchTemplate
from app.cad.templates.gear_pulley import GearPulleyTemplate
from app.cad.templates.enclosure import EnclosureTemplate
from app.cad.templates.bolt import BoltTemplate
from app.cad.templates.handle import HandleTemplate
from app.cad.templates.hex_nut import HexNutTemplate
from app.cad.templates.hex_standoff import HexStandoffTemplate
from app.cad.templates.l_bracket import LBracketTemplate
from app.cad.templates.pipe_clamp import PipeClampTemplate
from app.cad.templates.shaft_coupler import ShaftCouplerTemplate
from app.cad.templates.spacer import SpacerTemplate
from app.cad.templates.square_nut import SquareNutTemplate
from app.cad.templates.threaded_rod import ThreadedRodTemplate
from app.cad.templates.timing_pulley_gt2 import TimingPulleyGT2Template

_TEMPLATES: dict[str, BaseTemplate] = {
    t.object_type: t
    for t in (
        RectangularBracketTemplate(),
        LBracketTemplate(),
        EnclosureTemplate(),
        SpacerTemplate(),
        HexStandoffTemplate(),
        HexNutTemplate(),
        SquareNutTemplate(),
        BoltTemplate(),
        ThreadedRodTemplate(),
        ShaftCouplerTemplate(),
        TimingPulleyGT2Template(),
        RPi4EnclosureTemplate(),
        RPi5EnclosureTemplate(),
        BoardEnclosureTemplate(),
        MotorMountTemplate(),
        BearingHolderTemplate(),
        GenericFittedBoxTemplate(),
        PhoneHolderTemplate(),
        TireTemplate(),
        RimTemplate(),
        WheelAssemblyTemplate(),
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
