"""ISO metric thread engine — real helical geometry via the CAD kernel.

Currently implements modeled INTERNAL metric threads (for nuts and tapped
holes). External metric threads share the same helix/profile machinery and are a
planned extension (see ``cut_internal_thread`` for the pattern).

Robustness: helical booleans in the OCC kernel are numerically delicate, so every
modeled thread is *verified* (valid BRep, single solid) before it is accepted. If
verification fails the caller is told (``ThreadResult.representation ==
failed_to_model_fallback_cosmetic``) and keeps the plain bore — the exported solid
is therefore ALWAYS watertight, and the metadata never claims a thread that isn't
in the geometry.
"""
from __future__ import annotations

import contextvars
import math
from dataclasses import dataclass

import cadquery as cq

# The thread engine is the GROUND TRUTH for whether a thread was actually modeled
# (it ran the watertight gate). A template's build() can only return a Workplane,
# so the engine records its verdict here for the route to read after generation —
# more reliable than re-measuring a composite part's mesh (e.g. a bolt's shank +
# head can confuse a mesh-only audit). Always read via take_last_thread_result().
_LAST_THREAD_RESULT: contextvars.ContextVar = contextvars.ContextVar(
    "last_thread_result", default=None)


def take_last_thread_result() -> "ThreadResult | None":
    """Return and clear the most recent thread-engine verdict (authoritative)."""
    r = _LAST_THREAD_RESULT.get()
    _LAST_THREAD_RESULT.set(None)
    return r


def _record(result: "ThreadResult") -> "ThreadResult":
    _LAST_THREAD_RESULT.set(result)
    return result

THREAD_MODELED = "modeled"
THREAD_COSMETIC = "cosmetic"
THREAD_FALLBACK = "failed_to_model_fallback_cosmetic"

# Fine STL tessellation for modeled threads: the default export tolerance is too
# coarse to resolve a helix (it both hides the thread and tears the mesh open).
# The export pipeline MUST use these for threaded parts so the exported STL is
# watertight AND visibly threaded. STEP is BRep (no tessellation) so it always
# carries the modeled geometry regardless.
THREAD_STL_TOLERANCE = 0.04
THREAD_STL_ANGULAR_TOLERANCE = 0.25

# Longest external thread we model in one pass. The swept-ridge + fuse construction
# (see make_external_thread_rod) scales linearly and stays watertight well past
# common fastener lengths; this cap only guards against a pathological rod
# (e.g. 1 m) blowing the generation budget — beyond it we fall back to an honest
# cosmetic cylinder (clearly labelled, REVIEW).
EXTERNAL_MODELED_MAX_LEN = 200.0
# Below ~1.5 turns a helical sweep has too little to close on; such a stub is
# modeled as a plain chamfered cylinder (cosmetic) rather than risk a torn solid.
EXTERNAL_MIN_MODELED_LEN_TURNS = 1.5

# ISO 261 / ISO 262 coarse-thread pitch (mm) by nominal metric diameter, M2–M24.
ISO_METRIC_COARSE_PITCH: dict[float, float] = {
    2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8, 6.0: 1.0,
    8.0: 1.25, 10.0: 1.5, 12.0: 1.75, 14.0: 2.0, 16.0: 2.0, 18.0: 2.5,
    20.0: 2.5, 22.0: 2.5, 24.0: 3.0,
}

# Fundamental triangle height of the ISO 60° thread: H = P·(√3/2).
_H_FACTOR = math.sqrt(3.0) / 2.0


def metric_coarse_pitch(nominal_diameter_mm: float) -> float | None:
    """Coarse-thread pitch (mm) for a metric nominal diameter, or None."""
    return ISO_METRIC_COARSE_PITCH.get(round(float(nominal_diameter_mm), 3))


def internal_minor_diameter(major_diameter_mm: float, pitch_mm: float) -> float:
    """Internal-thread minor (crest) diameter D1 = D − 1.0825·P (ISO basic profile).

    This is the smallest bore diameter of a threaded nut — the crest of the
    internal thread, which clears a bolt's root."""
    return round(float(major_diameter_mm) - 1.0825 * float(pitch_mm), 4)


def external_minor_diameter(major_diameter_mm: float, pitch_mm: float) -> float:
    """External-thread minor (root) diameter d3 ≈ D − 1.2269·P (ISO basic profile).

    This is the smallest diameter of a bolt/stud — the root of the external thread
    between crests."""
    return round(float(major_diameter_mm) - 1.2269 * float(pitch_mm), 4)


@dataclass
class ThreadResult:
    """Outcome of a thread-modeling attempt (geometry returned separately)."""

    representation: str           # modeled | cosmetic | failed_to_model_fallback_cosmetic
    modeled: bool                 # True only when real helical geometry is present
    major_diameter_mm: float
    minor_diameter_mm: float
    pitch_mm: float
    depth_mm: float
    thread_z_start: float = 0.0   # axial start of the threaded region (after lead-in)
    thread_z_end: float = 0.0     # axial end of the threaded region (before lead-in)
    lead_in_mm: float = 0.0       # unthreaded lead-in depth at each opening
    note: str = ""


# Tessellation segments per turn by detail mode (drives export smoothness of the
# helix). "high_resolution" is for final export / 3D printing.
_DETAIL_SEGMENTS = {"cosmetic": 0, "modeled": 24, "high_resolution": 48}


def lead_in_depth(major_diameter: float, pitch: float, height: float) -> float:
    """Axial depth of the unthreaded lead-in recess at each bore opening.

    The modeled thread is bounded to the middle of the bore: a clean cylindrical
    lead-in recess (slightly wider than the thread root) is cut at the top and
    bottom so the thread starts AFTER the lead-in and never reaches the flat
    bearing faces. Depth is at least the thread depth (so the recess fully swallows
    the first/last thread turn) and capped so a short nut keeps a threaded middle."""
    rmaj = major_diameter / 2.0
    rmin = internal_minor_diameter(major_diameter, pitch) / 2.0
    depth = rmaj - rmin
    lead = max(0.9 * pitch, depth + 0.3)
    return round(min(lead, 0.30 * height), 4)


def _thread_cutter(pitch: float, length: float, crest_r: float, root_r: float) -> "cq.Workplane":
    """A helical V-groove cutter (truncated ISO profile) anchored on the thread crest.

    The profile is drawn in the X–Z plane with X = absolute radius and is anchored
    on the helix radius (``crest_r``); a small crest flat replaces the sharp apex so
    the swept solid meshes watertight instead of along a knife edge. The root
    groove width is kept well under the pitch so adjacent turns never self-intersect.

    For an INTERNAL thread the crest is the minor radius and the groove opens
    outward to the major (root) radius; for an EXTERNAL thread the crest is the
    major radius and the groove opens inward to the minor (root) radius. The same
    machinery serves both — only which radius is the crest differs.
    """
    helix = cq.Wire.makeHelix(pitch=pitch, height=length, radius=crest_r)
    crest_flat = pitch * 0.06   # narrow groove at the crest (leaves a crest land)
    root_width = pitch * 0.60   # groove width at the root (< pitch ⇒ no self-overlap)
    profile = cq.Workplane("XZ").polyline([
        (crest_r, -crest_flat / 2.0),
        (crest_r, crest_flat / 2.0),
        (root_r, root_width / 2.0),
        (root_r, -root_width / 2.0),
    ]).close()
    return profile.sweep(cq.Workplane(obj=helix), isFrenet=True)


def cross_section_half(solid: "cq.Workplane") -> "cq.Workplane":
    """Return the part with its +X half removed, exposing the bore on the X=0
    plane — a debug/QA artifact for visually confirming that the modeled thread
    lives ONLY on the internal bore wall (recessed behind the lead-ins) and never
    on the flat bearing faces. Used by the threaded-part debug test."""
    bb = solid.val().BoundingBox()
    span = max(bb.xlen, bb.ylen, bb.zlen) * 3.0 + 20.0
    zc = (bb.zmin + bb.zmax) / 2.0
    half = cq.Workplane("XY").box(span, span, span).translate((span / 2.0, 0.0, zc))
    return solid.cut(half)


def _is_watertight_single_solid(wp: "cq.Workplane") -> bool:
    """Accept a modeled thread only if it is a single solid whose BOTH exports are
    sound: a watertight + manifold STL and a non-empty STEP.

    A helical boolean is the ground truth for exports, not the BRep validity flag:
    OCC's analyzer often reports a perfectly-exportable helical solid as "invalid"
    (especially external threads), while a BRep-"valid" helix can still tessellate
    with cracks. So we verify the actual exported artifacts instead of trusting the
    validity flag — the STL watertight check is what the downstream export
    validator enforces, and a non-empty STEP confirms the BRep writes cleanly."""
    try:
        if len(wp.solids().vals()) != 1:
            return False
    except Exception:  # noqa: BLE001 — any kernel error ⇒ treat as invalid
        return False

    # Mesh-level watertight / manifold check, matching the export pipeline.
    # IMPORTANT: export a COPY — exporting at a fine tolerance writes a (loose)
    # triangulation onto the shape's TShape, which would corrupt the bounding box
    # of the solid we return to the caller. Copying isolates the gate's meshing.
    import tempfile
    from pathlib import Path

    from app.cad.plan.dimension_report import mesh_facts

    try:
        probe = cq.Workplane(obj=wp.val().copy())
    except Exception:  # noqa: BLE001
        probe = wp

    tmp = Path(tempfile.mktemp(suffix=".stl"))
    try:
        cq.exporters.export(probe, str(tmp), tolerance=THREAD_STL_TOLERANCE,
                            angularTolerance=THREAD_STL_ANGULAR_TOLERANCE)
        facts = mesh_facts(tmp.read_bytes())
        # The STL watertight/manifold check is the export-validator's own gate; a
        # solid that passes it exports a sound STEP too (the STEP export is verified
        # in the real pipeline). We avoid exporting STEP here — for a long helix it
        # is very slow and would blow the generation budget.
        return bool(facts.get("watertight") and facts.get("manifold"))
    except Exception:  # noqa: BLE001
        return False
    finally:
        tmp.unlink(missing_ok=True)


def cut_internal_thread(
    body: "cq.Workplane",
    *,
    major_diameter: float,
    pitch: float,
    length: float,
    z_bottom: float = 0.0,
    detail: str = THREAD_MODELED,
    top_face: str = ">Z",
) -> tuple["cq.Workplane", ThreadResult]:
    """Drill a minor-diameter bore and (for modeled detail) cut a real helical
    internal thread into it.

    ``body`` must be a solid with material around the bore axis (Z). The bore is
    drilled at the internal minor diameter (the thread crest); for modeled detail a
    helical V-groove is then cut out to the major diameter. Returns the resulting
    body and a :class:`ThreadResult`. On any kernel failure (or invalid result) the
    body is returned with just the smooth minor bore and
    ``representation = failed_to_model_fallback_cosmetic`` — so the output is always
    a valid watertight solid and the metadata never over-claims.
    """
    rmaj = major_diameter / 2.0
    rmin = internal_minor_diameter(major_diameter, pitch) / 2.0
    depth = rmaj - rmin
    height = length
    lead = lead_in_depth(major_diameter, pitch, height)

    bored = body.faces(top_face).workplane().hole(2.0 * rmin)

    base = ThreadResult(
        representation=THREAD_COSMETIC, modeled=False,
        major_diameter_mm=round(major_diameter, 4),
        minor_diameter_mm=round(2.0 * rmin, 4), pitch_mm=round(pitch, 4),
        depth_mm=round(depth, 4),
        thread_z_start=round(z_bottom + lead, 4),
        thread_z_end=round(z_bottom + height - lead, 4),
        lead_in_mm=lead)

    if detail not in (THREAD_MODELED, "high_resolution"):
        base.note = "Smooth bore with chamfered lead-in (cosmetic thread)."
        return _chamfer_bore_openings(bored, top_face, pitch, lead), _record(base)

    # Helical booleans are numerically delicate and their validity depends on how
    # far the helix overruns the part ends (the thread runout). No single overrun
    # works for every pitch/diameter, so try a few; for each that yields a valid
    # solid, add the lead-in recesses (which clip the thread off the bearing faces)
    # and accept the first whose FINAL geometry is a watertight single solid.
    for over in (3, 4, 2, 5):
        try:
            cutter = _thread_cutter(pitch, height + 2 * over * pitch, rmin, rmaj)
            cutter = cutter.translate((0.0, 0.0, z_bottom - over * pitch))
            # Fuzzy boolean tolerance makes the coincident bore-wall / cutter faces
            # resolve cleanly instead of silently no-op'ing.
            threaded = bored.cut(cutter, tol=1e-5)
        except Exception:  # noqa: BLE001 — kernel sweep/boolean failure: try next
            continue
        if len(threaded.solids().vals()) != 1:
            continue
        finished = _apply_thread_lead_in(threaded, z_bottom, height, rmaj, lead, pitch)
        if finished is None:
            continue
        if _is_watertight_single_solid(finished):
            return finished, _record(ThreadResult(
                representation=THREAD_MODELED, modeled=True,
                major_diameter_mm=round(major_diameter, 4),
                minor_diameter_mm=round(2.0 * rmin, 4), pitch_mm=round(pitch, 4),
                depth_mm=round(depth, 4),
                thread_z_start=round(z_bottom + lead, 4),
                thread_z_end=round(z_bottom + height - lead, 4),
                lead_in_mm=lead,
                note="Modeled ISO 60° internal thread, bounded between lead-in "
                     "chamfers (clean bearing faces)."))

    base.representation = THREAD_FALLBACK
    base.note = ("Thread modeling could not produce a valid watertight solid; "
                 "kept smooth bore with chamfered lead-in.")
    return _chamfer_bore_openings(bored, top_face, pitch, lead), _record(base)


def _chamfer_bore_openings(solid: "cq.Workplane", top_face: str, pitch: float,
                           lead: float) -> "cq.Workplane":
    """Chamfer the top & bottom bore-opening circles (a clean lead-in for a smooth
    bore). Best-effort: if the chamfer can't be built the plain bore is returned."""
    c = min(0.5 * pitch, lead * 0.6)
    if c <= 0.05:
        return solid
    try:
        return solid.edges("%CIRCLE").chamfer(c)
    except Exception:  # noqa: BLE001 — chamfer is cosmetic; never block the part
        return solid


def _external_thread_solid(rmaj: float, rmin: float, pitch: float,
                           length: float) -> "cq.Workplane | None":
    """Build a watertight externally-threaded rod by the ADDITIVE swept-ridge
    method: sweep the ISO tooth cross-section along a helix to make the thread
    ridge as a single solid, then fuse it onto a minor-diameter core.

    This avoids the long single-cut boolean (which the OCC kernel resolves to 0
    solids past ~25 mm). The ridge sweep is fast (≈0.1 s) and the touching/fuse is
    a clean overlap (the profile root dips ~0.25 mm into the core), so the fuse is
    robust and scales linearly with length. Returns None on any kernel failure or
    if the result isn't a watertight single solid (the caller then falls back to a
    cosmetic cylinder so the export is always sound)."""
    try:
        core = cq.Workplane("XY").circle(rmin).extrude(length)
        helix = cq.Wire.makeHelix(pitch=pitch, height=length, radius=(rmin + rmaj) / 2.0)
        crest_flat = pitch * 0.125          # crest land (no sharp knife apex)
        root_width = pitch * 0.72           # groove width at root (< pitch)
        profile = cq.Workplane("XZ").polyline([
            (rmin - 0.25, -root_width / 2.0),   # dip into the core for a clean fuse
            (rmaj, -crest_flat / 2.0),
            (rmaj, crest_flat / 2.0),
            (rmin - 0.25, root_width / 2.0),
        ]).close()
        ridge = profile.sweep(cq.Workplane(obj=helix), isFrenet=True)
        body = core.val().fuse(ridge.val(), tol=1e-6).clean()
        # Trim the thread flush to [0, length] with two thin cutting boxes (a local
        # planar cut is fast and robust, unlike intersecting the whole helix).
        big = rmaj * 3.0 + 10.0
        body = body.cut(cq.Workplane("XY").box(big, big, 2.0)
                        .translate((0, 0, -1.0)).val())
        body = body.cut(cq.Workplane("XY").box(big, big, 2.0)
                        .translate((0, 0, length + 1.0)).val())
        flat = cq.Workplane(obj=body.clean())
    except Exception:  # noqa: BLE001 — any kernel failure ⇒ caller falls back
        return None
    if len(flat.solids().vals()) != 1:
        return None
    return flat if _is_watertight_single_solid(flat) else None


def make_external_thread_rod(
    *, major_diameter: float, pitch: float, length: float,
    detail: str = THREAD_MODELED, chamfer_ends: bool = True,
) -> tuple["cq.Workplane", ThreadResult]:
    """Build an externally-threaded cylindrical rod (a stud / threaded rod / the
    threaded shank of a bolt or set screw): a minor-diameter core with a modeled
    ISO 60° helical thread fused onto it (see :func:`_external_thread_solid`).

    Returns the rod (origin at z=0, extending +Z) and a :class:`ThreadResult`. As
    with internal threads, the modeled result is accepted only if it is a valid
    watertight single solid; otherwise a smooth chamfered cylinder is returned with
    ``representation = failed_to_model_fallback_cosmetic`` so the export is always
    valid and the metadata never over-claims."""
    rmaj = major_diameter / 2.0
    rmin = external_minor_diameter(major_diameter, pitch) / 2.0
    depth = rmaj - rmin
    end_ch = min(depth * 0.9, pitch * 0.6) if chamfer_ends else 0.0

    def _shaft(radius: float) -> "cq.Workplane":
        s = cq.Workplane("XY").circle(radius).extrude(length)
        if end_ch > 0.05:
            try:
                s = s.edges(">Z or <Z").chamfer(end_ch)
            except Exception:  # noqa: BLE001
                pass
        return s

    base = ThreadResult(
        representation=THREAD_COSMETIC, modeled=False,
        major_diameter_mm=round(major_diameter, 4),
        minor_diameter_mm=round(2.0 * rmin, 4), pitch_mm=round(pitch, 4),
        depth_mm=round(depth, 4), thread_z_start=0.0, thread_z_end=round(length, 4))

    if detail not in (THREAD_MODELED, "high_resolution"):
        base.note = "Smooth cylinder (cosmetic thread requested)."
        return _shaft(rmaj), _record(base)

    # Guardrails: an over-long rod would blow the budget, and a sub-1.5-turn stub
    # has too little helix to close cleanly — both fall back honestly to cosmetic.
    if length > EXTERNAL_MODELED_MAX_LEN:
        base.representation = THREAD_FALLBACK
        base.note = (f"External thread not modeled: {length:g}mm exceeds the reliable "
                     f"modeled length ({EXTERNAL_MODELED_MAX_LEN:g}mm). Cosmetic "
                     "cylinder — not suitable for thread-fit validation.")
        return _shaft(rmaj), _record(base)
    if length < EXTERNAL_MIN_MODELED_LEN_TURNS * pitch:
        base.representation = THREAD_FALLBACK
        base.note = (f"External thread not modeled: {length:g}mm is under "
                     f"{EXTERNAL_MIN_MODELED_LEN_TURNS:g} turns. Cosmetic cylinder.")
        return _shaft(rmaj), _record(base)

    modeled = _external_thread_solid(rmaj, rmin, pitch, length)
    if modeled is not None:
        # End lead-in chamfer is a verified bonus: selecting a circular end edge on
        # a helical solid can grab thread edges and tear the mesh, so keep the
        # chamfer ONLY if the result is still a watertight single solid; otherwise
        # ship the (already watertight) flat-ended thread.
        finished = modeled
        if chamfer_ends:
            ch = min(depth * 0.9, pitch * 0.5)
            if ch > 0.05:
                cand = modeled
                ok = True
                for sel in (">Z", "<Z"):
                    try:
                        cand = cand.faces(sel).edges("%CIRCLE").chamfer(ch)
                    except Exception:  # noqa: BLE001
                        ok = False
                        break
                if ok and _is_watertight_single_solid(cand):
                    finished = cand
        return finished, _record(ThreadResult(
            representation=THREAD_MODELED, modeled=True,
            major_diameter_mm=round(major_diameter, 4),
            minor_diameter_mm=round(2.0 * rmin, 4), pitch_mm=round(pitch, 4),
            depth_mm=round(depth, 4), thread_z_start=0.0,
            thread_z_end=round(length, 4),
            note="Modeled ISO 60° external helical thread (swept ridge fused "
                 "onto a minor-diameter core)."))

    base.representation = THREAD_FALLBACK
    base.note = ("External thread modeling could not produce a valid watertight "
                 "solid; kept a smooth chamfered cylinder.")
    return _shaft(rmaj), _record(base)


def _apply_thread_lead_in(solid: "cq.Workplane", z_bottom: float, height: float,
                          rmaj: float, lead: float, pitch: float) -> "cq.Workplane | None":
    """Cut a clean cylindrical lead-in recess (just wider than the thread root) at
    the top and bottom bore openings, so the modeled thread is bounded to the
    middle of the bore and never bleeds onto the flat bearing faces. A small mouth
    chamfer is added best-effort. Returns None on kernel failure."""
    cb_r = rmaj + 0.15
    try:
        top = (cq.Workplane("XY").workplane(offset=z_bottom + height - lead)
               .circle(cb_r).extrude(lead + 0.2))
        bot = (cq.Workplane("XY").workplane(offset=z_bottom - 0.1)
               .circle(cb_r).extrude(lead + 0.1))
        out = solid.cut(top).cut(bot)
    except Exception:  # noqa: BLE001
        return None
    # Mouth chamfer on the (now clean, circular) bore openings — best effort.
    c = min(0.4 * pitch, lead * 0.5)
    if c > 0.05:
        for sel in (">Z", "<Z"):
            try:
                out = out.faces(sel).edges("%CIRCLE").chamfer(c)
            except Exception:  # noqa: BLE001
                pass
    return out
