"""Deterministic physical-calibration test coupons (docs/calibration.md).

Ten trusted, purpose-built generators for printing and physically measuring a
printer/material/process combination. Every coupon is deliberately SIMPLE,
robust geometry (boxes, cylinders, straightforward extrusions/cuts) — the
point of a calibration coupon is that it prints reliably and measures
unambiguously, not that it demonstrates advanced modeling.

Each template follows the same trusted-template contract as every other
generator in app.cad.templates (BaseTemplate.build -> cq.Workplane), so every
coupon flows through the SAME validated export path
(app.export.exporter.generate) as any other part — no separate export code.

Where a coupon's geometry depends on a fit/clearance value (the ladder step,
the mechanism-coupon bore), that value is resolved through
app.cad.calibration.resolver instead of a bare literal, and the resolution's
provenance (estimate vs a specific validated profile) is recorded as an
assumption on the design.
"""
from __future__ import annotations

import cadquery as cq

from app.cad.base import BaseTemplate, CadGenerationError, DimensionSpec
from app.schemas.design_spec import DesignSpec


def _add_boss(part: "cq.Workplane", *, x: float, y: float, z_top: float,
             diameter: float, height: float) -> "cq.Workplane":
    boss = (
        cq.Workplane("XY").workplane(offset=z_top)
        .center(x, y).circle(diameter / 2.0).extrude(height)
    )
    return part.union(boss)


class MasterCoupon(BaseTemplate):
    """A single reference block for baseline XY/Z print-accuracy checks: one
    designed footprint, one through-hole, one boss — measure all three
    against nominal to characterize general shrink/growth."""

    object_type = "calibration_master_coupon"
    name = "Calibration Master Coupon"
    description = "Reference block with one hole and one boss for baseline XY/Z accuracy."
    dimensions = [
        DimensionSpec("width", "Width (X)", 40.0, 10.0, 200.0),
        DimensionSpec("depth", "Depth (Y)", 40.0, 10.0, 200.0),
        DimensionSpec("thickness", "Thickness (Z)", 5.0, 1.0, 50.0),
        DimensionSpec("hole_diameter", "Reference hole diameter", 10.0, 1.0, 100.0),
        DimensionSpec("boss_diameter", "Reference boss diameter", 10.0, 1.0, 100.0),
        DimensionSpec("boss_height", "Reference boss height", 4.0, 0.5, 50.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        w, d, t = r["width"], r["depth"], r["thickness"]
        hole_d, boss_d, boss_h = r["hole_diameter"], r["boss_diameter"], r["boss_height"]

        part = cq.Workplane("XY").box(w, d, t)
        part = part.faces(">Z").workplane(centerOption="CenterOfBoundBox") \
            .center(-w / 4, 0).hole(hole_d)
        part = _add_boss(part, x=w / 4, y=0, z_top=t / 2.0,
                         diameter=boss_d, height=boss_h)
        return part


class VerticalHoleGauge(BaseTemplate):
    """A flat plate with holes drilled through the Z axis (same axis as the
    print direction) at graduated diameters — measure each to build an XY
    hole-diameter compensation curve."""

    object_type = "calibration_vertical_hole_gauge"
    name = "Vertical Hole Gauge"
    description = "Plate with graduated vertical (Z-axis) through-holes."
    dimensions = [
        DimensionSpec("length", "Length (X)", 110.0, 30.0, 400.0),
        DimensionSpec("width", "Width (Y)", 20.0, 8.0, 100.0),
        DimensionSpec("thickness", "Thickness (Z)", 6.0, 1.0, 30.0),
        DimensionSpec("hole_count", "Number of holes", 8.0, 2.0, 20.0),
        DimensionSpec("min_hole_diameter", "Smallest hole diameter", 1.0, 0.2, 50.0),
        DimensionSpec("max_hole_diameter", "Largest hole diameter", 10.0, 0.5, 100.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        length, width, t = r["length"], r["width"], r["thickness"]
        n = max(2, int(round(r["hole_count"])))
        d_min, d_max = r["min_hole_diameter"], r["max_hole_diameter"]
        if d_max <= d_min:
            raise CadGenerationError("max_hole_diameter must exceed min_hole_diameter")
        if d_max >= width:
            raise CadGenerationError(
                f"max_hole_diameter ({d_max}mm) must be smaller than width ({width}mm)")

        part = cq.Workplane("XY").box(length, width, t)
        pitch = length / (n + 1)
        for i in range(n):
            dia = d_min + (d_max - d_min) * i / (n - 1)
            x = -length / 2.0 + pitch * (i + 1)
            part = (
                part.faces(">Z").workplane(centerOption="CenterOfBoundBox")
                .moveTo(x, 0).hole(dia)
            )
        return part


class HorizontalHolePlate(BaseTemplate):
    """A standing wall with holes drilled through its THICKNESS (axis
    horizontal, parallel to the bed) at graduated diameters — these print as
    unsupported circular bridges/overhangs and typically distort more than a
    vertical-axis hole of the same nominal size."""

    object_type = "calibration_horizontal_hole_plate"
    name = "Horizontal Hole Plate"
    description = "Standing wall with graduated horizontal (bed-parallel) through-holes."
    dimensions = [
        DimensionSpec("wall_width", "Wall width (X)", 30.0, 10.0, 150.0),
        DimensionSpec("wall_height", "Wall height (Z)", 90.0, 20.0, 300.0),
        DimensionSpec("wall_thickness", "Wall thickness (Y)", 6.0, 1.0, 30.0),
        DimensionSpec("hole_count", "Number of holes", 6.0, 2.0, 16.0),
        DimensionSpec("min_hole_diameter", "Smallest hole diameter", 1.0, 0.2, 50.0),
        DimensionSpec("max_hole_diameter", "Largest hole diameter", 8.0, 0.5, 100.0),
        DimensionSpec("base_height", "Stabilizing base height", 6.0, 0.0, 50.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        ww, wh, wt = r["wall_width"], r["wall_height"], r["wall_thickness"]
        n = max(2, int(round(r["hole_count"])))
        d_min, d_max = r["min_hole_diameter"], r["max_hole_diameter"]
        base_h = r["base_height"]
        if d_max <= d_min:
            raise CadGenerationError("max_hole_diameter must exceed min_hole_diameter")
        if d_max >= wh:
            raise CadGenerationError("max_hole_diameter must be smaller than wall_height")

        base_d = max(ww, wt) + 20.0
        wall = (
            cq.Workplane("XY").workplane(offset=base_h)
            .box(ww, wt, wh, centered=(True, True, False))
        )
        # Drill through Y (the wall thickness) BEFORE unioning with the wider
        # base — the base's Y-extent differs from the wall's, so selecting
        # ">Y" after union risks grabbing the wrong face. A cylinder cut
        # oriented along Y (the "XZ" plane's normal) is unambiguous either way.
        pitch = wh / (n + 1)
        for i in range(n):
            dia = d_min + (d_max - d_min) * i / (n - 1)
            z = base_h + pitch * (i + 1)
            cutter = (
                cq.Workplane("XZ", origin=(0, 0, z))
                .circle(dia / 2.0).extrude(wt + 4, both=True)
            )
            wall = wall.cut(cutter)

        if base_h <= 0:
            return wall
        base = cq.Workplane("XY").box(ww, base_d, base_h, centered=(True, True, False))
        return wall.union(base)


class ClearanceFitLadder(BaseTemplate):
    """A fixed reference pin (boss) plus a row of holes at graduated
    diameters around the nominal — test the pin in each hole to find where
    press / snug / normal / loose transitions happen for THIS printer."""

    object_type = "calibration_fit_ladder"
    name = "Clearance & Press-Fit Ladder"
    description = "Fixed reference pin + a row of holes stepped around the nominal diameter."
    dimensions = [
        DimensionSpec("nominal_diameter", "Nominal pin/hole diameter", 8.0, 2.0, 60.0),
        DimensionSpec("step", "Diameter step between holes", 0.1, 0.02, 2.0),
        DimensionSpec("hole_count", "Number of holes", 9.0, 3.0, 21.0),
        DimensionSpec("plate_thickness", "Plate thickness", 6.0, 1.0, 30.0),
        DimensionSpec("pin_height", "Reference pin height", 8.0, 1.0, 60.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        nominal, step = r["nominal_diameter"], r["step"]
        n = max(3, int(round(r["hole_count"])))
        if n % 2 == 0:
            n += 1  # keep the ladder symmetric around the nominal
        t = r["plate_thickness"]
        pin_h = r["pin_height"]

        pitch = max(nominal + n * step, nominal * 1.5) + 4.0
        length = pitch * (n + 1)
        width = nominal + 4 * step + 20.0

        part = cq.Workplane("XY").box(length, width, t)
        half = n // 2
        for i in range(n):
            k = i - half
            dia = max(0.2, nominal + k * step)
            x = -length / 2.0 + pitch * (i + 1)
            part = (
                part.faces(">Z").workplane(centerOption="CenterOfBoundBox")
                .moveTo(x, width / 4.0).hole(dia)
            )
        part = _add_boss(part, x=0, y=-width / 4.0, z_top=t / 2.0,
                         diameter=nominal, height=pin_h)
        return part


class WallPinGapCoupon(BaseTemplate):
    """Three graduated feature rows on one base: thin free-standing fins
    (minimum wall thickness), free-standing pins (minimum feature diameter),
    and narrow slots (minimum resolvable gap)."""

    object_type = "calibration_wall_pin_gap_coupon"
    name = "Wall / Pin / Gap Coupon"
    description = "Graduated fins, pins, and slots for minimum-feature-size calibration."
    dimensions = [
        DimensionSpec("feature_count", "Steps per row", 6.0, 3.0, 12.0),
        DimensionSpec("min_size", "Smallest feature size", 0.3, 0.1, 5.0),
        DimensionSpec("max_size", "Largest feature size", 2.0, 0.2, 20.0),
        DimensionSpec("feature_height", "Fin/pin height", 10.0, 2.0, 50.0),
        DimensionSpec("base_thickness", "Base plate thickness", 4.0, 1.0, 20.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        n = max(3, int(round(r["feature_count"])))
        s_min, s_max = r["min_size"], r["max_size"]
        if s_max <= s_min:
            raise CadGenerationError("max_size must exceed min_size")
        h = r["feature_height"]
        base_t = r["base_thickness"]

        row_span = (s_max + 6.0) * n
        base_w = row_span + 20.0
        base_d = 90.0
        part = cq.Workplane("XY").box(base_w, base_d, base_t)
        top_z = base_t / 2.0

        def sizes():
            return [s_max - (s_max - s_min) * i / (n - 1) for i in range(n)]

        pitch = row_span / n
        x0 = -row_span / 2.0 + pitch / 2.0

        # Row 1 (front, -Y): free-standing fins (thin walls).
        for i, size in enumerate(sizes()):
            x = x0 + i * pitch
            fin = (
                cq.Workplane("XY").workplane(offset=top_z)
                .center(x, -base_d / 3.0).box(size, 14.0, h, centered=(True, True, False))
            )
            part = part.union(fin)

        # Row 2 (middle): free-standing round pins.
        for i, size in enumerate(sizes()):
            x = x0 + i * pitch
            part = _add_boss(part, x=x, y=0, z_top=top_z, diameter=size, height=h)

        # Row 3 (back, +Y): slots cut into a raised bar (minimum gap width).
        bar_h = min(h, 8.0)
        bar = (
            cq.Workplane("XY").workplane(offset=top_z)
            .center(0, base_d / 3.0).box(row_span + 8.0, 16.0, bar_h, centered=(True, True, False))
        )
        part = part.union(bar)
        slot_top = top_z + bar_h
        cutter = part.faces(">Z").workplane(centerOption="CenterOfBoundBox")
        for i, size in enumerate(sizes()):
            x = x0 + i * pitch
            slot = (
                cq.Workplane("XY").workplane(offset=top_z)
                .center(x, base_d / 3.0).rect(size, 16.0).extrude(bar_h + 1.0)
            )
            part = part.cut(slot)
        return part


class OverhangBridgeTower(BaseTemplate):
    """Stepped cantilevered overhangs (increasing unsupported reach) and
    parallel bridges of increasing span, on one base — find the printer's
    reliable overhang angle and maximum unsupported bridge length."""

    object_type = "calibration_overhang_bridge_tower"
    name = "Overhang & Bridge Tower"
    description = "Stepped overhang cantilever + graduated-span bridges."
    dimensions = [
        DimensionSpec("step_count", "Overhang steps", 5.0, 3.0, 10.0),
        DimensionSpec("step_height", "Height per step", 6.0, 2.0, 20.0),
        DimensionSpec("step_overhang", "Overhang per step", 3.0, 0.5, 15.0),
        DimensionSpec("step_size", "Step footprint (X/Y)", 16.0, 6.0, 60.0),
        DimensionSpec("bridge_count", "Bridge spans", 4.0, 2.0, 8.0),
        DimensionSpec("bridge_min_span", "Shortest bridge span", 10.0, 3.0, 100.0),
        DimensionSpec("bridge_max_span", "Longest bridge span", 40.0, 5.0, 200.0),
        DimensionSpec("bridge_pillar_height", "Bridge pillar height", 15.0, 5.0, 80.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        steps = max(3, int(round(r["step_count"])))
        step_h, overhang, size = r["step_height"], r["step_overhang"], r["step_size"]
        n_bridges = max(2, int(round(r["bridge_count"])))
        span_min, span_max = r["bridge_min_span"], r["bridge_max_span"]
        if span_max <= span_min:
            raise CadGenerationError("bridge_max_span must exceed bridge_min_span")
        pillar_h = r["bridge_pillar_height"]

        base_w = size + overhang * (steps - 1) + 20.0
        base_d = 60.0
        base_t = 4.0
        part = cq.Workplane("XY").box(base_w, base_d, base_t)

        # Overhang staircase along -Y: each step shifts +X by `overhang` and
        # sits directly on the previous step (no support material implied).
        z = base_t / 2.0
        x = -base_w / 2.0 + size / 2.0 + 4.0
        for i in range(steps):
            block = (
                cq.Workplane("XY").workplane(offset=z)
                .center(x, -base_d / 4.0).box(size, size, step_h, centered=(True, True, False))
            )
            part = part.union(block)
            z += step_h
            x += overhang

        # Bridges along +Y: n_bridges pairs of pillars with increasing span,
        # each pair capped by a thin horizontal beam.
        beam_t = 4.0
        zone_w = base_w - 20.0
        pitch = zone_w / n_bridges
        x0 = -zone_w / 2.0 + pitch / 2.0
        pillar_wh = 6.0
        for i in range(n_bridges):
            span = span_min + (span_max - span_min) * i / (n_bridges - 1)
            cx = x0 + i * pitch
            for side in (-1, 1):
                pillar = (
                    cq.Workplane("XY").workplane(offset=base_t / 2.0)
                    .center(cx + side * span / 2.0, base_d / 4.0)
                    .box(pillar_wh, pillar_wh, pillar_h, centered=(True, True, False))
                )
                part = part.union(pillar)
            beam = (
                cq.Workplane("XY").workplane(offset=base_t / 2.0 + pillar_h)
                .center(cx, base_d / 4.0)
                .box(span + pillar_wh, pillar_wh, beam_t, centered=(True, True, False))
            )
            part = part.union(beam)
        return part


class FastenerPlate(BaseTemplate):
    """A plate with standards-based clearance holes AND self-tap pilot holes
    for common metric screw sizes — verify real-world fastener fit (the
    hole-diameter standard itself is NOT printer-tuned; see
    docs/calibration.md "standards vs printer compensation")."""

    object_type = "calibration_fastener_plate"
    name = "Fastener Plate"
    description = "Standards-based clearance + pilot holes for M3-M6 screws."
    dimensions = [
        DimensionSpec("plate_thickness", "Plate thickness", 6.0, 2.0, 30.0),
    ]

    _SIZES_MM = {"M3": 3.0, "M4": 4.0, "M5": 5.0, "M6": 6.0}

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        from app.cad.standards.defaults import clearance_hole

        r = self.resolve(spec)
        t = r["plate_thickness"]
        sizes = list(self._SIZES_MM.items())
        pitch = 24.0
        length = pitch * (len(sizes) + 1)
        width = 56.0

        part = cq.Workplane("XY").box(length, width, t)
        for i, (label, major) in enumerate(sizes):
            x = -length / 2.0 + pitch * (i + 1)
            clearance_dia = clearance_hole(label, fit="normal")
            pilot_dia = round(major * 0.8, 2)  # typical self-tap pilot, ~80% of major
            part = (
                part.faces(">Z").workplane(centerOption="CenterOfBoundBox")
                .moveTo(x, width / 4.0).hole(clearance_dia)
            )
            part = (
                part.faces(">Z").workplane(centerOption="CenterOfBoundBox")
                .moveTo(x, -width / 4.0).hole(pilot_dia)
            )
        return part


class SnapFitKit(BaseTemplate):
    """A row of cantilever snap-fit beams (graduated thickness) with a hook
    lip at the free end — deflect each by hand to find the thickest beam
    that still snaps without cracking for this material."""

    object_type = "calibration_snap_fit_kit"
    name = "Snap-Fit Kit"
    description = "Graduated-thickness cantilever snap-fit beams."
    dimensions = [
        DimensionSpec("beam_count", "Number of beams", 5.0, 2.0, 10.0),
        DimensionSpec("min_thickness", "Thinnest beam", 0.8, 0.2, 5.0),
        DimensionSpec("max_thickness", "Thickest beam", 2.4, 0.5, 10.0),
        DimensionSpec("beam_length", "Beam free length", 25.0, 5.0, 80.0),
        DimensionSpec("beam_width", "Beam width", 8.0, 2.0, 40.0),
        DimensionSpec("hook_height", "Hook lip height", 1.2, 0.3, 5.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        n = max(2, int(round(r["beam_count"])))
        t_min, t_max = r["min_thickness"], r["max_thickness"]
        if t_max <= t_min:
            raise CadGenerationError("max_thickness must exceed min_thickness")
        beam_len, beam_w, hook_h = r["beam_length"], r["beam_width"], r["hook_height"]

        spine_h = max(t_max * 4.0, 16.0)   # tall enough to anchor the thickest beam solidly
        spine_t = 8.0
        pitch = beam_w + 10.0
        span = pitch * n
        # Spine centered at the origin: Z in [-spine_h/2, spine_h/2], Y in
        # [-spine_t/2, spine_t/2]. Every beam cantilevers from the spine's +Y
        # face at Z=0 (spine mid-height), guaranteeing a touching, fused seam.
        part = cq.Workplane("XY").box(span + 10.0, spine_t, spine_h)
        y_face = spine_t / 2.0
        hook_len = 4.0

        x0 = -span / 2.0 + pitch / 2.0
        for i in range(n):
            thick = t_max - (t_max - t_min) * i / (n - 1)
            x = x0 + i * pitch
            beam = (
                cq.Workplane("XY")
                .center(x, y_face).box(beam_w, beam_len, thick, centered=(True, False, True))
            )
            part = part.union(beam)
            hook = (
                cq.Workplane("XY")
                .center(x, y_face + beam_len - hook_len)
                .box(beam_w, hook_len, thick + hook_h * 2, centered=(True, False, True))
            )
            part = part.union(hook)
        return part


class MechanismCoupon(BaseTemplate):
    """A rotating/mechanism-fit verification coupon: a fixed shaft peg and a
    matching bore sized shaft + the ACTIVE calibration profile's radial
    clearance for a rotating fit — insert the peg and confirm it turns
    freely without excess play, verifying the profile's recommendation
    (distinct from the fit ladder, which instead SEARCHES for the value)."""

    object_type = "calibration_mechanism_coupon"
    name = "Mechanism (Rotating Fit) Coupon"
    description = "Shaft peg + bore sized by the active calibration profile's rotating clearance."
    dimensions = [
        DimensionSpec("shaft_diameter", "Shaft diameter", 6.0, 1.0, 40.0),
        DimensionSpec("shaft_height", "Shaft height", 12.0, 2.0, 60.0),
        DimensionSpec("plate_thickness", "Base plate thickness", 6.0, 1.0, 30.0),
        # Signed diametral clearance override (mm); 0 (default) resolves it
        # from the active calibration profile instead of a hardcoded value.
        DimensionSpec("bore_clearance_override", "Bore clearance override (0 = auto)",
                     0.0, -1.0, 3.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        shaft_d, shaft_h = r["shaft_diameter"], r["shaft_height"]
        t = r["plate_thickness"]
        override = r.dims_mm.get("bore_clearance_override", 0.0)

        if override:
            clearance = override
        else:
            from app.cad.calibration.resolver import resolve_measurement
            from app.schemas.calibration import CalibrationMeasurementType, FitClass

            resolved = resolve_measurement(
                [], measurement_type=CalibrationMeasurementType.diametral_clearance,
                feature="holder_case_clearance", fit_class=FitClass.normal)
            clearance = resolved.value_mm

        bore_d = shaft_d + clearance
        if bore_d <= 0:
            raise CadGenerationError(
                f"resolved bore diameter ({bore_d:.2f}mm) is not positive — "
                "check the clearance override / active calibration profile")

        gap = max(shaft_d, 10.0)
        base_w = shaft_d * 2 + bore_d * 2 + gap * 2
        base_d_ = max(shaft_d, bore_d) * 2 + 20.0
        part = cq.Workplane("XY").box(base_w, base_d_, t)
        part = _add_boss(part, x=-base_w / 4.0, y=0, z_top=t / 2.0,
                         diameter=shaft_d, height=shaft_h)
        socket = (
            cq.Workplane("XY").workplane(offset=t / 2.0)
            .center(base_w / 4.0, 0).circle(bore_d / 2.0).extrude(shaft_h)
        )
        part = part.union(socket)
        # Bore straight through the socket wall + base so the shaft can pass
        # all the way through and be tested from either face.
        part = part.faces(">Z").workplane(centerOption="CenterOfBoundBox") \
            .center(base_w / 4.0, 0).hole(bore_d, depth=shaft_h + t + 2)
        return part


class TextPlate(BaseTemplate):
    """A plate with the same label engraved at graduated font sizes — find
    the smallest text this printer/nozzle renders legibly. Engraved (cut)
    text is used rather than embossed (raised) text: engraving is both the
    harder legibility case in practice (fine detail can fill in or blur) and
    reliably fuses to a single printable solid; small raised-text glyphs are
    prone to fusing as touching-but-separate solid islands, which is best
    avoided in a coupon whose whole purpose is a dependable print."""

    object_type = "calibration_text_plate"
    name = "Text Legibility Plate"
    description = "Engraved text at graduated font sizes."
    dimensions = [
        DimensionSpec("plate_length", "Plate length (X)", 120.0, 40.0, 300.0),
        DimensionSpec("plate_width", "Plate width (Y)", 50.0, 20.0, 150.0),
        DimensionSpec("plate_thickness", "Plate thickness", 4.0, 1.0, 20.0),
        DimensionSpec("min_font_size", "Smallest font size", 2.0, 0.5, 20.0),
        DimensionSpec("max_font_size", "Largest font size", 7.0, 1.0, 40.0),
        DimensionSpec("step_count", "Number of size steps", 5.0, 2.0, 10.0),
        DimensionSpec("engrave_depth", "Engrave depth", 0.4, 0.1, 3.0),
    ]

    def build(self, spec: DesignSpec) -> "cq.Workplane":
        r = self.resolve(spec)
        length, width, t = r["plate_length"], r["plate_width"], r["plate_thickness"]
        f_min, f_max = r["min_font_size"], r["max_font_size"]
        if f_max <= f_min:
            raise CadGenerationError("max_font_size must exceed min_font_size")
        n = max(2, int(round(r["step_count"])))
        engrave_d = r["engrave_depth"]

        part = cq.Workplane("XY").box(length, width, t)
        row_pitch = width / (n + 1)

        for i in range(n):
            size = f_min + (f_max - f_min) * i / (n - 1)
            y = width / 2.0 - row_pitch * (i + 1)
            label = f"{size:g}mm"
            part = (
                part.faces(">Z").workplane(centerOption="CenterOfBoundBox")
                .center(0, y)
                .text(label, size, -engrave_d, combine="cut", halign="center", valign="center")
            )
        return part


__all__ = [
    "MasterCoupon", "VerticalHoleGauge", "HorizontalHolePlate", "ClearanceFitLadder",
    "WallPinGapCoupon", "OverhangBridgeTower", "FastenerPlate", "SnapFitKit",
    "MechanismCoupon", "TextPlate",
]
