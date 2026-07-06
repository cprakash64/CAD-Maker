"""Drawing ingestion for Drawing → CAD.

Turns one uploaded file (PNG / JPG / JPEG / WEBP / PDF / SVG / DXF) into the
inputs the analysis stage needs:

* ``image_bytes`` + ``media_type`` — vision-ready raster (images pass through,
  PDFs render their first page via pypdfium2),
* ``vector`` — deterministic geometry parsed from SVG / DXF (exact profiles,
  hole positions, dimension texts). Deterministic vector data always outranks
  vision output downstream.

File types are detected from magic bytes first, the filename second — a
mislabeled upload never routes to the wrong parser. Unsupported types raise
``UnsupportedDrawingFile`` with the accepted list.
"""
from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

from app.observability import log_event

SUPPORTED_TYPES_LABEL = "PNG, JPG/JPEG, WEBP, PDF, SVG, or DXF"

# Anything smaller than this fraction of the outer profile's minor size is a
# hole; larger concentric circles are bores / inner profiles.
_HOLE_MAX_FRACTION = 0.6
# mm per unit for DXF $INSUNITS codes.
_INSUNITS_TO_MM = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}
_SVG_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72.0,
                   "pc": 25.4 / 6.0, "px": 25.4 / 96.0, "": None}


class UnsupportedDrawingFile(ValueError):
    def __init__(self, detail: str | None = None):
        super().__init__(
            detail
            or f"Unsupported drawing file type. Upload a {SUPPORTED_TYPES_LABEL} file."
        )


class UnreadableDrawingFile(ValueError):
    """The file type is supported but its content could not be read."""


@dataclass
class VectorCircle:
    x: float
    y: float
    diameter: float


@dataclass
class VectorAnalysisRaw:
    """Deterministic geometry pulled from a vector file, in mm, y-up,
    NOT yet centered (raw drawing coordinates)."""

    source: str  # "svg" | "dxf"
    units_assumed: bool = False
    units_note: str | None = None
    min_x: float = math.inf
    min_y: float = math.inf
    max_x: float = -math.inf
    max_y: float = -math.inf
    circles: list[VectorCircle] = field(default_factory=list)
    outer_circle: VectorCircle | None = None
    outer_rect: tuple[float, float, float, float] | None = None  # x, y, w, h
    corner_radius: float | None = None
    polygon: list[tuple[float, float]] = field(default_factory=list)
    dimension_texts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def has_geometry(self) -> bool:
        return self.max_x > self.min_x and self.max_y > self.min_y

    def include(self, x: float, y: float) -> None:
        self.min_x, self.max_x = min(self.min_x, x), max(self.max_x, x)
        self.min_y, self.max_y = min(self.min_y, y), max(self.max_y, y)


@dataclass
class IngestedDrawing:
    file_type: str  # png | jpeg | webp | pdf | svg | dxf
    image_bytes: Optional[bytes] = None
    media_type: Optional[str] = None
    vector: Optional[VectorAnalysisRaw] = None
    text_context: Optional[str] = None  # extracted PDF text, if any
    notes: list[str] = field(default_factory=list)


def detect_file_type(data: bytes, filename: str | None = None,
                     content_type: str | None = None) -> str:
    """Magic bytes first; filename/content-type only as a tie-breaker."""
    head = data[:4096]
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF"):
        return "pdf"
    lowered = head.lstrip()[:2048].lower()
    if lowered.startswith(b"<?xml") or lowered.startswith(b"<svg") or b"<svg" in lowered:
        return "svg"
    if head.startswith(b"AutoCAD Binary DXF"):
        return "dxf"
    # Text DXF: group-code structure ("0\nSECTION") near the start.
    if re.match(rb"\s*999|\s*0\s*[\r\n]+\s*SECTION", head) and b"SECTION" in head:
        return "dxf"
    ext = (filename or "").rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
    by_ext = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "webp": "webp",
              "pdf": "pdf", "svg": "svg", "dxf": "dxf"}
    if ext in by_ext:
        return by_ext[ext]
    ct = (content_type or "").lower()
    for token, ftype in (("png", "png"), ("jpeg", "jpeg"), ("jpg", "jpeg"),
                         ("webp", "webp"), ("pdf", "pdf"), ("svg", "svg"),
                         ("dxf", "dxf")):
        if token in ct:
            return ftype
    raise UnsupportedDrawingFile()


def ingest_drawing(data: bytes, filename: str | None = None,
                   content_type: str | None = None) -> IngestedDrawing:
    if not data:
        raise UnreadableDrawingFile("Empty file")
    ftype = detect_file_type(data, filename, content_type)
    log_event("drawing_ingest", file_type=ftype, bytes=len(data), filename=filename)

    if ftype in ("png", "jpeg", "webp"):
        return IngestedDrawing(file_type=ftype, image_bytes=data,
                               media_type=f"image/{ftype}")
    if ftype == "pdf":
        return _ingest_pdf(data)
    if ftype == "svg":
        return IngestedDrawing(file_type="svg", vector=_parse_svg(data))
    if ftype == "dxf":
        return IngestedDrawing(file_type="dxf", vector=_parse_dxf(data))
    raise UnsupportedDrawingFile()  # unreachable; defensive


# --------------------------------------------------------------------------- PDF

def _ingest_pdf(data: bytes) -> IngestedDrawing:
    """Render page 1 to a PNG for the vision provider; extractable text rides
    along as extra context for the interpretation."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise UnreadableDrawingFile(
            "PDF support requires the pypdfium2 package on the server. "
            "Upload the drawing as PNG/JPG/SVG/DXF instead."
        ) from exc
    try:
        doc = pdfium.PdfDocument(data)
    except Exception as exc:  # noqa: BLE001 - corrupt/encrypted PDFs
        raise UnreadableDrawingFile(f"Couldn't open the PDF: {exc}") from exc
    try:
        if len(doc) == 0:
            raise UnreadableDrawingFile("The PDF has no pages")
        page = doc[0]
        bitmap = page.render(scale=2.0)  # ~144 DPI, plenty for dimension text
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        text = ""
        try:
            text = page.get_textpage().get_text_bounded() or ""
        except Exception:  # noqa: BLE001 - text extraction is best-effort
            pass
        notes = ["Rendered page 1 of the PDF for interpretation"]
        if len(doc) > 1:
            notes.append(f"The PDF has {len(doc)} pages; only page 1 was used")
        return IngestedDrawing(
            file_type="pdf", image_bytes=buf.getvalue(), media_type="image/png",
            text_context=text.strip()[:4000] or None, notes=notes,
        )
    finally:
        doc.close()


# --------------------------------------------------------------------------- SVG

_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _svg_len(value: str | None) -> tuple[float | None, str]:
    """Parse an SVG length like '120mm' / '4in' / '340' → (value, unit)."""
    if not value:
        return None, ""
    m = _FLOAT_RE.match(value.strip())
    if not m:
        return None, ""
    return float(m.group()), value.strip()[m.end():].strip().lower()


def _parse_svg(data: bytes) -> VectorAnalysisRaw:
    try:
        root = ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        raise UnreadableDrawingFile(f"Couldn't parse the SVG: {exc}") from exc

    raw = VectorAnalysisRaw(source="svg")

    # Physical scale: width/height attributes with real units beat the viewBox.
    vb = [float(v) for v in _FLOAT_RE.findall(root.get("viewBox") or "")][:4]
    w_val, w_unit = _svg_len(root.get("width"))
    scale = 1.0
    unit_mm = _SVG_UNIT_TO_MM.get(w_unit)
    if w_val and unit_mm and len(vb) == 4 and vb[2] > 0:
        scale = w_val * unit_mm / vb[2]
        raw.units_note = f"SVG physical size {w_val}{w_unit or 'mm'} mapped to millimetres"
    elif w_val and unit_mm:
        scale = unit_mm
        raw.units_note = f"SVG lengths in {w_unit or 'mm'} converted to millimetres"
    else:
        raw.units_assumed = True
        raw.units_note = "SVG user units assumed to be millimetres"

    rects: list[tuple[float, float, float, float, float]] = []  # x,y,w,h,rx

    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]  # strip namespace
        try:
            if tag == "rect":
                x = float(el.get("x", 0)) * scale
                y = float(el.get("y", 0)) * scale
                w = float(el.get("width", 0)) * scale
                h = float(el.get("height", 0)) * scale
                rx = float(el.get("rx", 0) or 0) * scale
                if w > 0 and h > 0:
                    rects.append((x, y, w, h, rx))
                    raw.include(x, y)
                    raw.include(x + w, y + h)
            elif tag in ("circle", "ellipse"):
                cx = float(el.get("cx", 0)) * scale
                cy = float(el.get("cy", 0)) * scale
                if tag == "circle":
                    r = float(el.get("r", 0)) * scale
                else:
                    r = (float(el.get("rx", 0)) + float(el.get("ry", 0))) / 2 * scale
                if r > 0:
                    raw.circles.append(VectorCircle(x=cx, y=cy, diameter=2 * r))
                    raw.include(cx - r, cy - r)
                    raw.include(cx + r, cy + r)
            elif tag == "line":
                for x, y in ((el.get("x1"), el.get("y1")), (el.get("x2"), el.get("y2"))):
                    raw.include(float(x or 0) * scale, float(y or 0) * scale)
            elif tag in ("polyline", "polygon"):
                nums = [float(v) * scale for v in _FLOAT_RE.findall(el.get("points") or "")]
                pts = list(zip(nums[0::2], nums[1::2]))
                for x, y in pts:
                    raw.include(x, y)
                if tag == "polygon" and len(pts) >= 3 and not raw.polygon:
                    raw.polygon = pts
            elif tag == "path":
                # Coordinate scan for the bounding box — good enough to size the
                # part; exact path reconstruction is not required.
                nums = [float(v) * scale for v in _FLOAT_RE.findall(el.get("d") or "")]
                for x, y in zip(nums[0::2], nums[1::2]):
                    raw.include(x, y)
            elif tag in ("text", "tspan") and (el.text or "").strip():
                raw.dimension_texts.append(el.text.strip()[:256])
        except (TypeError, ValueError):
            continue  # one malformed element never rejects the drawing

    if not raw.has_geometry():
        raise UnreadableDrawingFile(
            "The SVG contains no drawable geometry (no rects, circles, lines, or paths)."
        )

    _classify_outer(raw, rects)
    # SVG y grows downward; flip so downstream reasoning is y-up.
    raw.circles = [VectorCircle(c.x, (raw.min_y + raw.max_y) - c.y, c.diameter)
                   for c in raw.circles]
    if raw.outer_circle:
        oc = raw.outer_circle
        raw.outer_circle = VectorCircle(oc.x, (raw.min_y + raw.max_y) - oc.y, oc.diameter)
    if raw.polygon:
        raw.polygon = [(x, (raw.min_y + raw.max_y) - y) for x, y in raw.polygon]
    return raw


# --------------------------------------------------------------------------- DXF

def _parse_dxf(data: bytes) -> VectorAnalysisRaw:
    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover - pinned in requirements
        raise UnreadableDrawingFile(
            "DXF support requires the ezdxf package on the server."
        ) from exc
    if data.startswith(b"AutoCAD Binary DXF"):
        raise UnreadableDrawingFile(
            "Binary DXF isn't supported — export the drawing as ASCII DXF (R12/R2000+)."
        )
    try:
        doc = ezdxf.read(io.StringIO(data.decode("utf-8", errors="replace")))
    except Exception as exc:  # noqa: BLE001 - ezdxf raises various DXF errors
        raise UnreadableDrawingFile(f"Couldn't parse the DXF: {exc}") from exc

    insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    to_mm = _INSUNITS_TO_MM.get(insunits)
    raw = VectorAnalysisRaw(source="dxf")
    if to_mm is None:
        to_mm = 1.0
        raw.warnings.append(f"Unrecognized DXF units code {insunits}; values read as mm")
    if insunits == 0:
        raw.units_assumed = True
        raw.units_note = "DXF has no units header ($INSUNITS); values assumed to be mm"
    elif insunits != 4:
        raw.units_note = f"DXF units converted to millimetres (×{to_mm:g})"

    rects: list[tuple[float, float, float, float, float]] = []
    for e in doc.modelspace():
        t = e.dxftype()
        try:
            if t == "CIRCLE":
                c = e.dxf.center
                raw.circles.append(VectorCircle(
                    x=c.x * to_mm, y=c.y * to_mm, diameter=2 * e.dxf.radius * to_mm))
                raw.include((c.x - e.dxf.radius) * to_mm, (c.y - e.dxf.radius) * to_mm)
                raw.include((c.x + e.dxf.radius) * to_mm, (c.y + e.dxf.radius) * to_mm)
            elif t == "LINE":
                raw.include(e.dxf.start.x * to_mm, e.dxf.start.y * to_mm)
                raw.include(e.dxf.end.x * to_mm, e.dxf.end.y * to_mm)
            elif t == "ARC":
                c, r = e.dxf.center, e.dxf.radius
                raw.include((c.x - r) * to_mm, (c.y - r) * to_mm)
                raw.include((c.x + r) * to_mm, (c.y + r) * to_mm)
                if raw.corner_radius is None or r * to_mm < raw.corner_radius:
                    raw.corner_radius = r * to_mm
            elif t in ("LWPOLYLINE", "POLYLINE"):
                pts = ([(p[0], p[1]) for p in e.get_points()] if t == "LWPOLYLINE"
                       else [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices])
                pts_mm = [(x * to_mm, y * to_mm) for x, y in pts]
                for x, y in pts_mm:
                    raw.include(x, y)
                closed = bool(getattr(e, "closed", False) or getattr(e.dxf, "flags", 0) & 1)
                if closed and len(pts_mm) >= 3:
                    _consider_polyline_profile(raw, rects, pts_mm)
            elif t in ("TEXT", "MTEXT"):
                txt = (e.dxf.text if t == "TEXT" else e.text) or ""
                txt = re.sub(r"\\[A-Za-z][^;]*;", "", txt).strip()  # strip MTEXT codes
                if txt:
                    raw.dimension_texts.append(txt[:256])
            elif t == "DIMENSION":
                meas = getattr(e.dxf, "actual_measurement", None)
                if meas and meas > 0:
                    raw.dimension_texts.append(f"{meas * to_mm:g} mm (dimension)")
        except Exception:  # noqa: BLE001 - one bad entity never rejects the file
            continue

    if not raw.has_geometry():
        raise UnreadableDrawingFile(
            "The DXF contains no drawable geometry in its modelspace."
        )
    _classify_outer(raw, rects)
    return raw


def _consider_polyline_profile(raw: VectorAnalysisRaw,
                               rects: list[tuple[float, float, float, float, float]],
                               pts: list[tuple[float, float]]) -> None:
    """A closed polyline is either an axis-aligned rectangle or a polygon
    candidate for the outer profile (largest wins later)."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    is_rect = len(pts) in (4, 5) and all(
        math.isclose(x, min(xs), abs_tol=1e-6) or math.isclose(x, max(xs), abs_tol=1e-6)
        or math.isclose(y, min(ys), abs_tol=1e-6) or math.isclose(y, max(ys), abs_tol=1e-6)
        for x, y in pts)
    if is_rect and w > 0 and h > 0:
        rects.append((min(xs), min(ys), w, h, 0.0))
    elif w * h > 0:
        cur = raw.polygon
        cur_area = 0.0
        if cur:
            cxs, cys = [p[0] for p in cur], [p[1] for p in cur]
            cur_area = (max(cxs) - min(cxs)) * (max(cys) - min(cys))
        if w * h > cur_area:
            raw.polygon = pts


def _classify_outer(raw: VectorAnalysisRaw,
                    rects: list[tuple[float, float, float, float, float]]) -> None:
    """Decide the outer profile: the largest rect / circle that spans (nearly)
    the whole drawing. Remaining small circles are holes."""
    span_w = raw.max_x - raw.min_x
    span_h = raw.max_y - raw.min_y
    span = max(span_w, span_h)

    biggest_rect = max(rects, key=lambda r: r[2] * r[3], default=None)
    biggest_circle = max(raw.circles, key=lambda c: c.diameter, default=None)

    rect_area = biggest_rect[2] * biggest_rect[3] if biggest_rect else 0.0
    circle_area = (math.pi * (biggest_circle.diameter / 2) ** 2
                   if biggest_circle else 0.0)

    if biggest_circle and biggest_circle.diameter >= 0.9 * span and circle_area >= rect_area:
        raw.outer_circle = biggest_circle
        raw.circles = [c for c in raw.circles if c is not biggest_circle]
    elif biggest_rect and biggest_rect[2] >= 0.9 * span_w and biggest_rect[3] >= 0.9 * span_h:
        raw.outer_rect = biggest_rect[:4]
        if biggest_rect[4] > 0:
            raw.corner_radius = biggest_rect[4]
    # else: polygon/bbox outer — downstream uses the polygon or overall bbox.

    # Anything bigger than a hole should not be treated as one.
    limit = _HOLE_MAX_FRACTION * (min(span_w, span_h) if span_w and span_h else span)
    kept: list[VectorCircle] = []
    for c in raw.circles:
        if c.diameter <= limit or _is_center_bore(raw, c):
            kept.append(c)
        else:
            raw.warnings.append(
                f"Ignored a Ø{c.diameter:g}mm circle that is neither the outer "
                "profile nor a plausible hole")
    raw.circles = kept


def _is_center_bore(raw: VectorAnalysisRaw, c: VectorCircle) -> bool:
    """A large circle concentric with a circular outer profile is a center bore."""
    if not raw.outer_circle:
        return False
    return (math.hypot(c.x - raw.outer_circle.x, c.y - raw.outer_circle.y)
            < 0.05 * raw.outer_circle.diameter)
