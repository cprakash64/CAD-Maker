"""Deterministic 2D profile extraction from raster drawings (PNG/JPG).

For a dimensioned side-profile / stepped-outline drawing the SHAPE is right
there in the pixels — no LLM should invent it. This module:

1. resizes to a sane working size and converts to grayscale,
2. thresholds near-black geometry strokes (light-gray watermarks/graphics are
   ignored by the threshold),
3. flood-fills the background from the border: light areas NOT reached are
   enclosed by closed ink loops — the part's interior (largest region) and any
   drawn holes (smaller enclosed regions inside it),
4. traces the interior's boundary (Moore neighbours) and simplifies it
   (Ramer–Douglas–Peucker) into a closed polygon, y-up.

The LLM is used afterwards only to read dimensions/labels for scaling — never
to decide the shape. Extraction failure returns None (callers fall back to the
normal vision pipeline); it never raises.
"""
from __future__ import annotations

import io
import math
from collections import deque
from dataclasses import dataclass, field

from app.observability import log_event

_MAX_WORK_DIM = 700          # analysis resolution (profile accuracy ≈ 0.15%)
_INK_THRESHOLD = 100         # gray < this = geometry stroke (watermarks are lighter)
_RDP_EPSILON_PX = 1.6
_MIN_INTERIOR_FRACTION = 0.01   # interior must be ≥1% of the sheet to be a part
_MIN_HOLE_PX = 12
_MAX_POINTS = 200


@dataclass
class RasterHole:
    """An enclosed region inside the part's interior, in the SAME y-up frame as
    the profile points (origin at the profile bbox's bottom-left).

    ``kind`` is the classified shape (circle / ellipse / hexagon /
    regular_polygon / rectangle / rounded_slot / arbitrary_polygon). ``polygon``
    carries the simplified boundary vertices (y-up, local frame) for every
    NON-circular hole so it can be cut as its true shape — a hexagon stays a
    hexagon, never an equivalent-area circle."""

    x_px: float                           # region center
    y_px: float
    diameter_px: float                    # (bbox w + h) / 2
    area_px: float
    is_round: bool                        # circle-ish (aspect ≈ 1, fill ≈ π/4)
    kind: str = "circle"
    polygon: list[tuple[float, float]] = field(default_factory=list)

    @property
    def equivalent_diameter_px(self) -> float:
        """Diameter of the circle with the region's area (for non-round cutouts)."""
        return 2.0 * math.sqrt(max(self.area_px, 1.0) / math.pi)


@dataclass
class RasterProfile:
    """A closed outer profile in pixel coordinates (y-up, origin bottom-left of
    the profile's bounding box)."""

    points: list[tuple[float, float]]     # simplified closed polygon (no repeat)
    width_px: float
    height_px: float
    hole_count: int                       # enclosed regions inside the interior
    interior_fraction: float              # interior area / image area
    notes: list[str] = field(default_factory=list)
    holes: list[RasterHole] = field(default_factory=list)  # geometry per hole

    def is_rectangleish(self, tol: float = 0.98) -> bool:
        """Polygon area ≈ bbox area ⇒ the outline is just a rectangle."""
        return _polygon_area(self.points) >= tol * self.width_px * self.height_px

    def scaled_points(self, scale: float) -> list[tuple[float, float]]:
        return [(round(x * scale, 3), round(y * scale, 3)) for x, y in self.points]


def extract_profile(image_bytes: bytes) -> RasterProfile | None:
    try:
        return _extract(image_bytes)
    except Exception as exc:  # noqa: BLE001 - extraction is best-effort
        log_event("raster_profile_failed", reason=type(exc).__name__,
                  detail=str(exc)[:200])
        return None


def _extract(image_bytes: bytes) -> RasterProfile | None:
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    if max(img.size) > _MAX_WORK_DIM:
        f = _MAX_WORK_DIM / max(img.size)
        img = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))
    gray = np.asarray(img, dtype=np.uint8)
    h, w = gray.shape
    ink = gray < _INK_THRESHOLD          # near-black geometry strokes only
    light = ~ink

    # Flood-fill the background from every border pixel over LIGHT cells.
    background = _flood_from_border(np, light)
    enclosed = light & ~background       # light areas sealed inside ink loops
    if not enclosed.any():
        return None

    regions = _connected_regions(np, enclosed)
    if not regions:
        return None
    regions.sort(key=lambda r: r["area"], reverse=True)
    interior = regions[0]
    if interior["area"] < _MIN_INTERIOR_FRACTION * w * h:
        return None  # nothing part-sized is enclosed — not a closed profile

    # Holes: other enclosed regions fully inside the interior's bbox.
    ix0, iy0, ix1, iy1 = interior["bbox"]
    holes = [r for r in regions[1:]
             if r["area"] >= _MIN_HOLE_PX
             and r["bbox"][0] >= ix0 and r["bbox"][1] >= iy0
             and r["bbox"][2] <= ix1 and r["bbox"][3] <= iy1]

    contour = _trace_boundary(np, interior["mask"])
    if len(contour) < 8:
        return None
    poly = _rdp(contour, _RDP_EPSILON_PX)
    if len(poly) < 3:
        return None
    if len(poly) > _MAX_POINTS:
        poly = poly[:: math.ceil(len(poly) / _MAX_POINTS)]

    # Normalize: origin at the profile bbox's bottom-left, y-up.
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, y1 = min(xs), max(ys)
    pts = [(float(x - x0), float(y1 - y)) for x, y in poly]
    width = max(xs) - x0
    height = y1 - min(ys)
    if width < 4 or height < 4:
        return None
    hole_geoms: list[RasterHole] = []
    for r in holes:
        hole_geoms.append(_classify_hole(np, r, x0, y1))
    profile = RasterProfile(
        points=pts, width_px=float(width), height_px=float(height),
        hole_count=len(holes),
        interior_fraction=interior["area"] / (w * h),
        notes=[f"Outer profile traced from the drawing ({len(pts)} points); "
               f"{len(holes)} enclosed hole region(s) detected"],
        holes=hole_geoms,
    )
    for hle in hole_geoms:
        log_event("drawing_hole_detected", kind=hle.kind,
                  round=hle.is_round, verts=len(hle.polygon))
    log_event("raster_profile_extracted", points=len(pts), holes=len(holes),
              rectangleish=profile.is_rectangleish())
    return profile


# A closed hole polygon with this many-or-more vertices reads as a smooth curve
# (circle/ellipse); fewer sharp corners means a true polygon (hex/rect/slot).
_ROUND_VERTEX_MIN = 10
# Holes are traced with a FINER tolerance than the outer profile so even a small
# circular hole samples enough vertices to read as round (a coarse epsilon
# under-samples a small circle into a spurious polygon).
_HOLE_RDP_EPSILON_PX = 0.8
# GEOMETRIC circularity gates (vertex count alone is NOT sufficient — an arched
# window traces to many arc segments and would otherwise read as a circle):
#  * radial residual (std of point radii / mean radius) must be small — an
#    arched window's flat bottom sits far off any best-fit circle;
#  * no single straight edge may span a large fraction of the diameter (a circle
#    has only short segments; a window has a full-width flat bottom / sides);
#  * the contour must cover nearly the full 360° around the fitted centre.
_CIRCLE_RESIDUAL_MAX = 0.085
_CIRCLE_LONG_EDGE_MAX = 0.55        # a flat bottom spans ~0.8·d; a coarse small
                                   # circle's longest chord is ~0.46·d
_CIRCLE_ARC_COVERAGE_MIN = 300.0   # degrees


def _classify_hole(np, region: dict, ox: float, oy_top: float) -> RasterHole:
    """Classify one enclosed hole region and, for non-circular holes, keep its
    simplified boundary polygon (y-up, in the profile's local frame at origin
    ``(ox, oy_top)``). Circles keep only a diameter; every polygon keeps its
    vertices so it is cut as its TRUE shape — a hexagonal hole never collapses
    to a circle, and an arched/curved-top window never collapses to a circle.

    Roundness needs BOTH a many-segment trace AND a geometric circle fit (low
    radial residual, no dominant straight edge, ~360° coverage); a shape with a
    flat bottom + vertical sides + curved top is an ``arched_rectangular_cutout``."""
    hx0, hy0, hx1, hy1 = region["bbox"]
    hw, hh = hx1 - hx0 + 1, hy1 - hy0 + 1
    aspect = hw / hh if hh else 1.0
    fill = region["area"] / (hw * hh)
    cx, cy = (hx0 + hx1) / 2, (hy0 + hy1) / 2

    contour = _trace_boundary(np, region["mask"])
    poly_img = _rdp(contour, _HOLE_RDP_EPSILON_PX) if len(contour) >= 6 else []
    if len(poly_img) >= 2 and poly_img[0] == poly_img[-1]:
        poly_img = poly_img[:-1]
    poly_img = _merge_short_sides(poly_img)  # drop spurious split corners
    n = len(poly_img)
    round_aspect = abs(1 - aspect) <= 0.2
    # Geometric circularity is the PRIMARY test (vertex count alone lets an arched
    # window's many arc segments read as a circle). A near-regular polygon
    # (hexagon/pentagon) also fits a circle tightly, so it is excluded here and
    # classified by corner count below — the only thing that separates a small
    # circle from a hexagon is the hexagon's equal, regular sides.
    residual, long_edge, coverage = _circle_metrics(np, contour, (hw + hh) / 4.0)
    # Only 4–7 near-equal sides read as an intentional polygon (square/pentagon/
    # hexagon/heptagon). An 8+-side near-regular loop is a coarsely-traced small
    # CIRCLE (a true octagonal hole is rare), so it stays eligible for the circle
    # fit — this keeps small round holes round without letting a hexagon pass.
    regular_poly = 4 <= n <= 7 and _near_regular(poly_img)
    geom_circular = (residual <= _CIRCLE_RESIDUAL_MAX
                     and long_edge <= _CIRCLE_LONG_EDGE_MAX
                     and coverage >= _CIRCLE_ARC_COVERAGE_MIN
                     and not regular_poly)

    if geom_circular:
        kind = "circle" if round_aspect else "ellipse"
    elif n == 6:
        kind = "hexagon"
    elif n == 4:
        # A 4-gon is a rectangle, or a slot when clearly elongated.
        kind = "rounded_slot" if max(aspect, 1 / aspect) >= 2.2 else "rectangle"
    elif n in (3, 5, 7, 8) and _near_regular(poly_img):
        kind = "hexagon" if n == 7 else "regular_polygon"
    elif _is_arched_window(poly_img):
        # flat bottom + vertical sides + curved top → arched rectangular window.
        kind = "arched_rectangular_cutout"
    elif max(aspect, 1 / aspect) >= 2.2:
        kind = "rounded_slot"
    else:
        kind = "arbitrary_polygon"
    is_round = kind in ("circle", "ellipse")

    polygon: list[tuple[float, float]] = []
    if not is_round and n >= 3:
        polygon = [(float(x - ox), float(oy_top - y)) for x, y in poly_img]
    return RasterHole(
        x_px=float(cx - ox), y_px=float(oy_top - cy),
        diameter_px=float((hw + hh) / 2), area_px=float(region["area"]),
        is_round=is_round, kind=kind, polygon=polygon)


def _circle_metrics(np, contour, r_hint: float) -> tuple[float, float, float]:
    """(radial_residual_ratio, longest_edge_ratio, arc_coverage_deg) for a traced
    contour. A true circle: residual≈0, short edges only, ~360° coverage. An
    arched window / flat-bottomed shape: high residual and a long straight edge.

    ``radial_residual_ratio`` = std(point radii)/mean radius about the best-fit
    (Kåsa) circle; ``longest_edge_ratio`` = longest RDP edge / fitted diameter;
    ``arc_coverage_deg`` = angular span of contour points about the centre."""
    if len(contour) < 8 or r_hint <= 0:
        return 1.0, 1.0, 0.0
    pts = np.asarray(contour, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    # Kåsa algebraic circle fit: solve [2x 2y 1]·[a b c] = x²+y².
    A = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    b = x * x + y * y
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:  # noqa: BLE001
        return 1.0, 1.0, 0.0
    cx, cy, c = sol
    r = math.sqrt(max(c + cx * cx + cy * cy, 1e-9))
    radii = np.hypot(x - cx, y - cy)
    mean_r = float(radii.mean()) or 1.0
    residual = float(radii.std()) / mean_r
    # Longest straight edge from the simplified polygon, relative to diameter.
    poly = _rdp([(int(px), int(py)) for px, py in contour], _HOLE_RDP_EPSILON_PX)
    longest = 0.0
    for i in range(len(poly)):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % len(poly)]
        longest = max(longest, math.hypot(bx - ax, by - ay))
    long_edge_ratio = longest / (2 * r) if r > 0 else 1.0
    # Angular coverage about the fitted centre.
    ang = np.degrees(np.arctan2(y - cy, x - cx))
    order = np.sort(ang)
    if len(order) >= 2:
        gaps = np.diff(order)
        wrap = 360.0 - float(order[-1] - order[0])
        max_gap = max(float(gaps.max()), wrap)
        coverage = 360.0 - max_gap
    else:
        coverage = 0.0
    return residual, long_edge_ratio, coverage


def _is_arched_window(poly: list[tuple[int, int]]) -> bool:
    """A closed loop is an arched rectangular window when it has a long straight
    (near-horizontal) bottom edge, two roughly-vertical side edges, and a run of
    short segments forming the curved top — i.e. line + line + arc, not a circle
    and not a plain rectangle. ``poly`` is in image coords (x right, y DOWN)."""
    n = len(poly)
    if n < 5:
        return False
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    w = (max(xs) - min(xs)) or 1
    h = (max(ys) - min(ys)) or 1
    horiz_long = vert_long = curve_segs = 0
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if abs(dy) <= 0.30 * abs(dx) and length >= 0.45 * w:
            horiz_long += 1            # a flat, wide edge (the bottom)
        elif abs(dx) <= 0.30 * abs(dy) and length >= 0.30 * h:
            vert_long += 1             # a tall side edge
        elif length <= 0.45 * max(w, h):
            curve_segs += 1            # a short segment (part of the arc)
    # One flat bottom + at least two vertical sides + several arc segments.
    return horiz_long >= 1 and vert_long >= 2 and curve_segs >= 2


def _merge_short_sides(poly: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Collapse spuriously short edges (a single true corner split into two by
    rasterization, incl. the wraparound edge) so a hexagon reads as 6 sides, not
    7. Iteratively drops the endpoint of the shortest edge until every remaining
    edge is a real side or only 4 vertices remain."""
    pts = list(poly)
    while len(pts) > 4:
        n = len(pts)
        sides = [math.hypot(pts[i][0] - pts[(i + 1) % n][0],
                            pts[i][1] - pts[(i + 1) % n][1]) for i in range(n)]
        mean = sum(sides) / n
        if mean <= 1e-6:
            break
        j = min(range(n), key=lambda i: sides[i])
        if sides[j] >= 0.30 * mean:
            break
        # Drop the far endpoint of the shortest edge (merge the split corner).
        pts.pop((j + 1) % n)
    return pts


def _near_regular(poly: list[tuple[int, int]]) -> bool:
    """True when a simplified polygon reads as a REGULAR polygon: near-equal side
    lengths (a hexagonal hole's six sides are all similar; an arbitrary cutout's
    are not)."""
    n = len(poly)
    if n < 3:
        return False
    sides = [math.hypot(poly[i][0] - poly[(i + 1) % n][0],
                        poly[i][1] - poly[(i + 1) % n][1]) for i in range(n)]
    mean = sum(sides) / n
    if mean <= 1e-6:
        return False
    return all(abs(s - mean) <= 0.35 * mean for s in sides)


# ------------------------------------------------------------------ primitives

def _flood_from_border(np, light):
    """Boolean mask of light cells reachable from the image border
    (iterative half-scan propagation — no recursion, no per-pixel Python loop)."""
    h, w = light.shape
    reach = np.zeros_like(light)
    reach[0, :] = light[0, :]
    reach[-1, :] = light[-1, :]
    reach[:, 0] |= light[:, 0]
    reach[:, -1] |= light[:, -1]
    # Alternating raster sweeps converge quickly for drawing-like layouts.
    for _ in range(64):
        before = int(reach.sum())
        # forward sweep
        for y in range(h):
            row = reach[y]
            if y > 0:
                row |= reach[y - 1] & light[y]
            np.logical_or(row[1:], row[:-1] & light[y, 1:], out=row[1:])
            reach[y] = row
        # backward sweep
        for y in range(h - 1, -1, -1):
            row = reach[y]
            if y < h - 1:
                row |= reach[y + 1] & light[y]
            np.logical_or(row[:-1], row[1:] & light[y, :-1], out=row[:-1])
            reach[y] = row
        if int(reach.sum()) == before:
            break
    return reach


def _connected_regions(np, mask) -> list[dict]:
    """4-connected regions of ``mask`` via BFS (few, part-sized regions)."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    regions: list[dict] = []
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx]:
            continue
        q = deque([(sy, sx)])
        seen[sy, sx] = True
        cells = []
        while q:
            y, x = q.popleft()
            cells.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        cys = [c[0] for c in cells]
        cxs = [c[1] for c in cells]
        rmask = np.zeros_like(mask)
        rmask[tuple(zip(*cells))] = True
        regions.append({
            "area": len(cells), "mask": rmask,
            "bbox": (min(cxs), min(cys), max(cxs), max(cys)),
        })
        if len(regions) >= 40:  # a drawing has few enclosed regions
            break
    return regions


_MOORE = ((0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1))


def _trace_boundary(np, mask) -> list[tuple[int, int]]:
    """Moore-neighbour boundary trace of a filled region → ordered (x, y)."""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return []
    start = (int(ys.min()), int(xs[ys == ys.min()].min()))  # topmost-left
    h, w = mask.shape

    def on(y, x):
        return 0 <= y < h and 0 <= x < w and mask[y, x]

    contour = [start]
    prev_dir = 6  # came from the left
    cur = start
    for _ in range(len(ys) * 4 + 8):
        found = False
        for k in range(8):
            d = (prev_dir + 5 + k) % 8  # start search left-behind of entry dir
            dy, dx = _MOORE[d]
            ny, nx = cur[0] + dy, cur[1] + dx
            if on(ny, nx):
                cur = (ny, nx)
                prev_dir = d
                found = True
                break
        if not found:
            break  # single-pixel region
        if cur == start:
            break
        contour.append(cur)
    return [(x, y) for y, x in contour]


def _rdp(points: list[tuple[int, int]], eps: float) -> list[tuple[int, int]]:
    """Iterative Ramer–Douglas–Peucker polyline simplification."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i0, i1 = stack.pop()
        ax, ay = points[i0]
        bx, by = points[i1]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1.0
        far_i, far_d = -1, 0.0
        for i in range(i0 + 1, i1):
            px, py = points[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / norm
            if d > far_d:
                far_i, far_d = i, d
        if far_d > eps:
            keep[far_i] = True
            stack.append((i0, far_i))
            stack.append((far_i, i1))
    return [p for p, k in zip(points, keep) if k]


def _polygon_area(points: list[tuple[float, float]]) -> float:
    n = len(points)
    s = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2.0
