"""Base class for trusted CAD template generators.

Every generator is a *local, audited* function. The LLM never reaches this
layer with anything but a validated DesignSpec. Generators declare the
dimensions they require (with sane defaults) so we can fill gaps and reject
nonsense before touching the CAD kernel.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.schemas.design_spec import DesignSpec

if TYPE_CHECKING:  # pragma: no cover - import only for type hints
    import cadquery as cq


class CadGenerationError(Exception):
    """Raised when geometry cannot be built from an otherwise-valid spec."""


@dataclass(frozen=True)
class DimensionSpec:
    """Metadata for one named dimension a template understands."""

    name: str
    label: str
    default: float
    min: float
    max: float
    required: bool = False
    unit: str = "mm"


@dataclass
class ResolvedSpec:
    """A DesignSpec with template defaults applied and dimensions in mm."""

    spec: DesignSpec
    dims_mm: dict[str, float] = field(default_factory=dict)

    def __getitem__(self, key: str) -> float:
        return self.dims_mm[key]


class BaseTemplate:
    """Subclass this for each part type."""

    object_type: str = ""
    name: str = ""
    description: str = ""
    dimensions: list[DimensionSpec] = []

    def resolve(self, spec: DesignSpec) -> ResolvedSpec:
        """Apply defaults, validate required keys, convert to mm."""
        dims_mm: dict[str, float] = {}
        for dim in self.dimensions:
            if dim.name in spec.dimensions:
                value = spec.dimensions[dim.name]
            elif dim.required:
                raise CadGenerationError(
                    f"{self.name}: missing required dimension '{dim.name}'"
                )
            else:
                value = dim.default
            value_mm = spec.to_mm(value)
            min_mm = dim.min  # min/max are authored in mm
            max_mm = dim.max
            if not (min_mm <= value_mm <= max_mm):
                raise CadGenerationError(
                    f"{self.name}: dimension '{dim.name}'={value}{spec.units} "
                    f"({value_mm:.2f}mm) outside allowed range "
                    f"[{min_mm}, {max_mm}]mm"
                )
            dims_mm[dim.name] = value_mm
        return ResolvedSpec(spec=spec, dims_mm=dims_mm)

    def default_dimensions(self) -> dict[str, float]:
        """The editable parameter set surfaced to the UI (in mm)."""
        return {d.name: d.default for d in self.dimensions}

    def build(self, spec: DesignSpec) -> "cq.Workplane":  # pragma: no cover
        raise NotImplementedError
