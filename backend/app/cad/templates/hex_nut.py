"""Regular hex nut (ISO 4032 / DIN 934): a six-sided hex prism with chamfered
bearing faces and a concentric internal thread.

By default the internal thread is MODELED as real helical geometry (see
``app.cad.threads``), so it is physically present in the STL and STEP exports —
not a cosmetic smooth bore. When the prompt/route requests a cosmetic preview, or
when the kernel cannot produce a valid watertight modeled thread, the bore falls
back to the thread minor diameter (a smooth hole) and the downstream geometry
audit reconciles ``thread_representation`` from the actual mesh so the metadata
never over-claims.

The across-flats dimension is preserved EXACTLY; across-corners is derived from
hex geometry (across_corners = across_flats / cos 30°), matching the standard table.

LEGAL / SOURCING NOTE:
McMaster CAD files must not be scraped, cached, redistributed, or used as source
geometry unless LunaiCAD has explicit commercial permission. This geometry is
generated parametrically from public ISO/DIN nominal dimensions.
"""
from __future__ import annotations

import math

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec

_COS30 = math.cos(math.pi / 6.0)


def across_corners(across_flats: float) -> float:
    return across_flats / _COS30


class HexNutTemplate(BaseTemplate):
    object_type = "hex_nut"
    name = "Hex Nut (ISO 4032 / DIN 934)"
    description = "Regular hex nut: six-sided prism, chamfered faces, internal thread."
    dimensions = [
        DimensionSpec("across_flats", "Across-flats width", 18.0, 3.0, 120.0),
        DimensionSpec("height", "Nut height", 10.8, 1.0, 120.0),
        DimensionSpec("bore_diameter", "Through-bore (thread minor) diameter",
                      10.1, 1.0, 110.0),
        # Bearing-face chamfer; 0 = no chamfer (omitted by the resolver when ~0).
        DimensionSpec("chamfer", "Bearing-face chamfer", 0.0, 0.0, 20.0),
        # Thread spec: when both are present (>0) a MODELED internal thread is cut
        # at this major diameter & pitch, and the bore is the derived minor diameter.
        DimensionSpec("thread_major_diameter", "Thread major (nominal) diameter",
                      0.0, 0.0, 120.0),
        DimensionSpec("thread_pitch", "Thread pitch", 0.0, 0.0, 8.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        af = r["across_flats"]
        height = r["height"]
        bore = r["bore_diameter"]
        chamfer = r.dims_mm.get("chamfer", 0.0)
        thread_major = r.dims_mm.get("thread_major_diameter", 0.0)
        thread_pitch = r.dims_mm.get("thread_pitch", 0.0)

        threaded = thread_major > 0 and thread_pitch > 0
        if not threaded and bore >= af:
            raise CadGenerationError(
                f"bore_diameter ({bore}mm) must be smaller than the across-flats "
                f"width ({af}mm) of a hex nut")

        # polygon(6, diameter) inscribes the hexagon in a circle of `diameter`,
        # i.e. diameter is the across-corners distance — derive it from across-flats.
        body = cq.Workplane("XY").polygon(6, across_corners(af)).extrude(height)

        # Chamfer the top & bottom bearing-face perimeter edges before drilling so
        # the bore stays a clean cylinder. Bound the chamfer so it can never fail.
        max_chamfer = min((across_corners(af) - af) / 2.0 * 0.95, height / 2.0 * 0.95)
        c = min(chamfer, max_chamfer) if chamfer and chamfer > 0 else 0.0
        if c > 0.05:
            try:
                body = body.edges(">Z or <Z").chamfer(c)
            except Exception:  # noqa: BLE001 — chamfer is cosmetic; never block the nut
                pass

        if threaded:
            from app.cad.threads import cut_internal_thread
            from app.config import settings

            detail = getattr(settings, "thread_detail", "modeled") or "modeled"
            body, _result = cut_internal_thread(
                body, major_diameter=thread_major, pitch=thread_pitch,
                length=height, z_bottom=0.0, detail=detail)
            return body

        if bore > 0:
            body = body.faces(">Z").workplane().hole(bore)
        return body
