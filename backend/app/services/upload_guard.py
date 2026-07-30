"""Shared upload-inspection gate for every drawing endpoint.

One place that decides whether uploaded bytes are safe to hand to the vision
model or the geometry parsers. Used consistently by /interpret, /generate,
/to-cad and the compatibility alias so limits and rejections can't drift between
endpoints (docs/production-readiness.md, Phase 1D).

Design rules:
  * Type comes from the BYTES, never the filename or Content-Type alone. A
    ``garbage.png`` full of random bytes is rejected, not accepted as an image.
  * Rasters are actually decoded and verified, with pixel/dimension caps that
    stop decompression/pixel bombs, then re-encoded to strip metadata and
    normalise orientation.
  * SVG is scanned for the dangerous constructs (DOCTYPE/entities, scripts,
    event handlers, external references, foreignObject) BEFORE any XML parse.
  * PDF/DXF enforce page/entity/size limits; nothing fetches external resources.

Errors are typed so the router can map them to HTTP status codes:
  TooLargeUpload   -> 413   (size / pixels / pages / complexity)
  UnsupportedUpload-> 415   (unknown type, or content ≠ claimed type)
  MalformedUpload  -> 422   (recognised type, but corrupt / unusable)
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Optional

from app.observability import log_event
from app.services.drawing_ingest import (
    UnsupportedDrawingFile,
    detect_file_type,
)

# --- limits ---------------------------------------------------------------
MAX_UPLOAD_BYTES = 20 * 1024 * 1024        # hard ceiling for any upload
MAX_IMAGE_BYTES = 12 * 1024 * 1024         # raster-specific ceiling
MAX_IMAGE_DIMENSION = 12000                # px, per side
MAX_IMAGE_PIXELS = 40_000_000             # ~40 MP total (decompression-bomb gate)
MAX_PDF_PAGES = 25
MAX_SVG_BYTES = 8 * 1024 * 1024
MAX_SVG_NODES = 50_000
MAX_DXF_BYTES = 16 * 1024 * 1024
MAX_DXF_ENTITIES = 200_000
MAX_DXF_LAYERS = 2_000
# mm. Real mechanical drawings live far inside this; values beyond it are either
# corrupt or crafted to blow up downstream geometry maths (inf/NaN bounding
# boxes, runaway tessellation).
MAX_DXF_COORDINATE = 1e9

MAX_TEXT_FIELD_CHARS = 4000                # hint/notes/family form fields
# All the limits above bound the INPUT (bytes/pixels/entities/pages); none of
# them bound how long a pathological-but-within-limits file takes to actually
# parse. This wall-clock budget is the backstop.
UPLOAD_PARSE_TIMEOUT_SECONDS = 10.0

_RASTER_TYPES = {"png", "jpeg", "webp"}


def _has_raster_magic(data: bytes) -> bool:
    """True only if the bytes carry a real raster signature (not inferred from a
    filename or Content-Type)."""
    head = data[:12]
    return (
        head.startswith(b"\x89PNG")
        or head.startswith(b"\xff\xd8\xff")               # JPEG
        or (head.startswith(b"RIFF") and head[8:12] == b"WEBP")
    )
_MEDIA_TYPE = {
    "png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp",
    "pdf": "application/pdf", "svg": "image/svg+xml", "dxf": "image/vnd.dxf",
}


class UploadRejected(ValueError):
    """Base for all upload rejections."""

    status_code = 400


class TooLargeUpload(UploadRejected):
    status_code = 413


class UnsupportedUpload(UploadRejected):
    status_code = 415


class MalformedUpload(UploadRejected):
    status_code = 422


class UploadTimeout(UploadRejected):
    status_code = 422


@dataclass
class InspectedUpload:
    file_type: str                       # png|jpeg|webp|pdf|svg|dxf
    media_type: str
    safe_bytes: bytes                    # rasters: re-encoded; others: original
    width: Optional[int] = None
    height: Optional[int] = None
    page_count: Optional[int] = None
    is_raster: bool = False


# --- SVG safety scan (pre-parse, on raw bytes) ----------------------------
_SVG_DANGEROUS = [
    (re.compile(rb"<!DOCTYPE", re.I), "a DOCTYPE declaration"),
    (re.compile(rb"<!ENTITY", re.I), "an entity definition (billion-laughs risk)"),
    (re.compile(rb"<\s*script", re.I), "a <script> element"),
    (re.compile(rb"\son\w+\s*=", re.I), "an inline event handler"),
    (re.compile(rb"<\s*foreignObject", re.I), "a <foreignObject> element"),
    (re.compile(rb"javascript:", re.I), "a javascript: URL"),
]
# External references: href/xlink:href/src pointing off-document (http(s)://,
# //host, file:, data: is allowed only for nothing here — reject all externals).
_SVG_EXTERNAL_REF = re.compile(
    rb"(?:xlink:href|href|src)\s*=\s*[\"'](?!#)\s*(?:https?:|//|file:|ftp:)", re.I)
# CSS can fetch too: url(...) inside <style>/style="" and @import both pull
# remote or filesystem resources without ever using an href attribute.
_SVG_CSS_EXTERNAL = re.compile(
    rb"(?:@import|url\s*\(\s*[\"']?\s*(?:https?:|//|file:|ftp:))", re.I)


def _scan_svg_safety(data: bytes) -> None:
    if len(data) > MAX_SVG_BYTES:
        raise TooLargeUpload("The SVG is too large.")
    head = data[:200_000]  # dangerous constructs live at the top; bound the scan
    for pattern, what in _SVG_DANGEROUS:
        if pattern.search(head) or pattern.search(data):
            raise MalformedUpload(f"This SVG contains {what}, which isn't allowed.")
    if _SVG_EXTERNAL_REF.search(data):
        raise MalformedUpload("This SVG references an external resource, which isn't allowed.")
    if _SVG_CSS_EXTERNAL.search(data):
        raise MalformedUpload("This SVG references an external resource, which isn't allowed.")
    # Node-count guard: cheap tag count, no full parse.
    if data.count(b"<") > MAX_SVG_NODES:
        raise TooLargeUpload("The SVG has too many elements.")
    _parse_svg_strict(data)


def _parse_svg_strict(data: bytes) -> None:
    """Structural parse with a hardened parser, after the byte scan.

    The regex scan above is the primary gate (it rejects DOCTYPE/ENTITY before
    any parser sees them), but a scan alone cannot tell a real SVG from bytes
    that merely avoid the bad substrings. Parsing proves the document is
    well-formed XML and that its root really is ``<svg>``, so a malformed or
    disguised file is rejected here rather than deeper in the geometry parser.

    ``xml.etree.ElementTree`` is used deliberately: it is already a dependency
    (stdlib), it does NOT resolve external entities, and it raises rather than
    expanding undefined ones — so it cannot be turned into an XXE or
    billion-laughs primitive. No new dependency is warranted.
    """
    import xml.etree.ElementTree as ET

    parser = ET.XMLParser()
    # Belt and braces: refuse entity declarations at the parser level too, so a
    # construct that slipped past the byte scan still cannot expand.
    try:
        parser.parser.EntityDeclHandler = _reject_entity_decl
    except AttributeError:  # pragma: no cover - non-expat backend
        pass
    try:
        root = ET.fromstring(data, parser=parser)
    except _EntityDeclarationRejected as exc:
        raise MalformedUpload(
            "This SVG contains an entity definition, which isn't allowed.") from exc
    except ET.ParseError as exc:
        raise MalformedUpload("The SVG is not well-formed XML.") from exc
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag != "svg":
        raise MalformedUpload("This file is not an SVG drawing.")


class _EntityDeclarationRejected(Exception):
    """Raised from the expat handler when an SVG declares an entity."""


def _reject_entity_decl(*_args, **_kwargs):
    raise _EntityDeclarationRejected()


# --- raster verification --------------------------------------------------
def _verify_raster(data: bytes, ftype: str) -> InspectedUpload:
    from PIL import Image, UnidentifiedImageError

    if len(data) > MAX_IMAGE_BYTES:
        raise TooLargeUpload("The image is too large (max 12 MB).")

    # Guard PIL's own decompression-bomb ceiling to our limit.
    prev_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        # 1) structural verify on a throwaway image (catches truncation).
        try:
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
        except Image.DecompressionBombError as exc:
            raise TooLargeUpload("The image has too many pixels.") from exc
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise MalformedUpload("The image is corrupt or truncated.") from exc

        # 2) re-open (verify() leaves the image unusable) to read size + normalise.
        try:
            with Image.open(io.BytesIO(data)) as img:
                w, h = img.size
                if w <= 0 or h <= 0:
                    raise MalformedUpload("The image has no pixels.")
                if w > MAX_IMAGE_DIMENSION or h > MAX_IMAGE_DIMENSION:
                    raise TooLargeUpload(
                        f"The image is too large ({w}×{h}px; max "
                        f"{MAX_IMAGE_DIMENSION}px per side).")
                if w * h > MAX_IMAGE_PIXELS:
                    raise TooLargeUpload("The image has too many pixels.")
                # Normalise EXIF orientation and strip metadata by re-encoding.
                from PIL import ImageOps
                normalised = ImageOps.exif_transpose(img)
                rgb = normalised.convert("RGB")
                buf = io.BytesIO()
                rgb.save(buf, format="PNG")  # metadata-free
                safe = buf.getvalue()
        except Image.DecompressionBombError as exc:
            raise TooLargeUpload("The image has too many pixels.") from exc
        except (UnidentifiedImageError, OSError) as exc:
            raise MalformedUpload("The image could not be decoded.") from exc
    finally:
        Image.MAX_IMAGE_PIXELS = prev_limit

    return InspectedUpload(
        file_type=ftype, media_type="image/png", safe_bytes=safe,
        width=w, height=h, is_raster=True,
    )


# --- PDF verification -----------------------------------------------------
def _verify_pdf(data: bytes) -> InspectedUpload:
    if not data[:5].startswith(b"%PDF-"):
        raise UnsupportedUpload("This file claims to be a PDF but is not.")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - pinned in requirements
        raise MalformedUpload("PDF support is unavailable on the server.") from exc
    try:
        doc = pdfium.PdfDocument(data)
    except Exception as exc:  # noqa: BLE001 - corrupt/encrypted
        raise MalformedUpload("The PDF is corrupt or encrypted.") from exc
    try:
        # pypdfium exposes a form/security flag; a password-protected doc raises
        # above, but double-check page access is possible.
        n = len(doc)
        if n == 0:
            raise MalformedUpload("The PDF has no pages.")
        if n > MAX_PDF_PAGES:
            raise TooLargeUpload(f"The PDF has {n} pages (max {MAX_PDF_PAGES}).")
    finally:
        doc.close()
    return InspectedUpload(file_type="pdf", media_type="application/pdf",
                           safe_bytes=data, page_count=n)


# --- DXF verification -----------------------------------------------------
def _verify_dxf(data: bytes) -> InspectedUpload:
    if len(data) > MAX_DXF_BYTES:
        raise TooLargeUpload("The DXF is too large.")
    if data.startswith(b"AutoCAD Binary DXF"):
        raise UnsupportedUpload(
            "Binary DXF isn't supported — export as ASCII DXF (R12/R2000+).")
    # Cheap entity-count guard before ezdxf parses the whole document. Every
    # entity is a "0\n<TYPE>" group-code record; count them without a full parse.
    text = data.decode("utf-8", errors="replace")
    entity_markers = text.count("\nENTITIES")
    approx_entities = len(re.findall(r"^\s*0\s*$", text[:2_000_000], re.M))
    if entity_markers == 0 and "SECTION" not in text[:4096]:
        raise MalformedUpload("This file is not a valid DXF.")
    if approx_entities > MAX_DXF_ENTITIES:
        raise TooLargeUpload("The DXF has too many entities.")
    if text.count("\nLAYER") > MAX_DXF_LAYERS:
        raise TooLargeUpload("The DXF has too many layers.")
    _check_dxf_coordinates(text)
    return InspectedUpload(file_type="dxf", media_type="image/vnd.dxf", safe_bytes=data)


# Group codes 10-39 are the X/Y/Z coordinate families in ASCII DXF; the value
# sits on the line after its code.
_DXF_COORD_CODE = re.compile(r"^\s*(1[0-9]|2[0-9]|3[0-9])\s*$")
# Every CAD exporter writes ±1e20 into extent fields ($EXTMIN/$EXTMAX and the
# per-block equivalents) to mean "extents not calculated". It is a flag value,
# not a position, so it is allowed through while any OTHER out-of-range number
# is still rejected.
_DXF_UNSET_EXTENT = 1e20


def _check_dxf_coordinates(text: str) -> None:
    """Reject non-finite or absurd coordinates before any geometry maths runs.

    ezdxf will happily hand back ``1e308`` or ``nan``; those propagate into
    bounding boxes and tessellation as inf/NaN and can turn a conversion into an
    unbounded or crashing computation.

    Only the geometry-bearing sections are scanned. The HEADER is skipped on
    purpose: ``$EXTMIN``/``$EXTMAX``/``$PEXTMIN``/``$PEXTMAX`` are conventionally
    written as ±1e20 to mean "extents not calculated", so every real CAD export
    carries those sentinels and scanning the header would reject valid files.
    """
    starts = [i for i in (text.find("\nBLOCKS"), text.find("\nENTITIES")) if i != -1]
    if not starts:
        return  # no geometry sections; structure errors are ezdxf's job
    lines = text[min(starts):].split("\n", 400_000)
    for i in range(len(lines) - 1):
        if not _DXF_COORD_CODE.match(lines[i]):
            continue
        raw = lines[i + 1].strip()
        try:
            value = float(raw)
        except ValueError:
            continue  # not a numeric payload; ezdxf will report the structure error
        if value != value or value in (float("inf"), float("-inf")):
            raise MalformedUpload("The DXF contains a non-finite coordinate.")
        if abs(value) == _DXF_UNSET_EXTENT:
            continue  # documented "extents not calculated" sentinel
        if abs(value) > MAX_DXF_COORDINATE:
            raise TooLargeUpload(
                "The DXF contains coordinates far outside any real part "
                f"(max ±{MAX_DXF_COORDINATE:g}).")


# --- bounded intake -------------------------------------------------------
async def read_upload_bounded(file, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read an ``UploadFile`` in chunks, refusing to buffer more than ``limit``.

    ``await file.read()`` with no argument pulls the entire body into memory
    before any size check can run, so a multi-gigabyte POST becomes an OOM
    before the guard is ever consulted. Reading one byte past the limit is
    enough to know the upload is over budget; we stop there and never allocate
    the rest.
    """
    chunk_size = 1024 * 1024
    buf = bytearray()
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise TooLargeUpload(
                f"The file is too large (max {limit // (1024 * 1024)} MB).")
    return bytes(buf)


# --- filename / header hygiene -------------------------------------------
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_download_name(base: str | None, extension: str, fallback: str = "part") -> str:
    """Build a Content-Disposition filename from untrusted text.

    ``base`` may come from model output (``design.object_type`` is a free string
    the planner fills in), so it can contain quotes, semicolons, CR/LF, NULs or
    path separators — all of which either break out of the quoted header value
    or forge a different download name. Everything outside a conservative
    allowlist collapses to ``_``; the result can never contain a path separator,
    a quote, or a control character.
    """
    stem = _UNSAFE_FILENAME_CHARS.sub("_", (base or "").strip()).strip("._-")
    if not stem:
        stem = fallback
    stem = stem[:64]
    ext = _UNSAFE_FILENAME_CHARS.sub("", extension or "").lower()[:16]
    return f"{stem}.{ext}" if ext else stem


def sanitize_text_field(value: str | None, limit: int = MAX_TEXT_FIELD_CHARS,
                        field: str = "field") -> str | None:
    """Bound and clean an untrusted free-text form field (hint/notes/family).

    Multipart form fields have no schema-level ``max_length``, so without this a
    client can post an unbounded string that is then forwarded to the vision
    provider or into a log line. NUL and other C0 control characters are
    stripped (they corrupt logs and downstream parsers); tab/newline survive.
    """
    if value is None:
        return None
    if len(value) > limit:
        raise TooLargeUpload(f"The {field} is too long (max {limit} characters).")
    cleaned = "".join(
        ch for ch in value if ch in "\t\n\r" or (ord(ch) >= 32 and ord(ch) != 127)
    )
    return cleaned or None


def inspect_upload(data: bytes, filename: str | None,
                   content_type: str | None) -> InspectedUpload:
    """The single upload gate. Returns an :class:`InspectedUpload` or raises a
    typed :class:`UploadRejected`. Never trusts filename/Content-Type over the
    actual bytes."""
    if not data:
        raise MalformedUpload("Empty file.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise TooLargeUpload("The file is too large.")

    try:
        ftype = detect_file_type(data, filename, content_type)
    except UnsupportedDrawingFile as exc:
        raise UnsupportedUpload(str(exc)) from exc

    # Content-Type sanity: if the client claimed a raster but the bytes are not
    # that raster, reject the mismatch (defence in depth over detect_file_type,
    # which may fall back to extension for a signature-less file).
    if ftype in _RASTER_TYPES:
        # detect_file_type may have inferred a raster from the extension /
        # Content-Type for signature-less bytes (e.g. garbage named .png). That
        # is a content/type mismatch, not a malformed image — reject as 415.
        if not _has_raster_magic(data):
            raise UnsupportedUpload(
                "This file claims to be an image but its content is not a "
                "supported image format.")
        result = _verify_raster(data, ftype)
    elif ftype == "pdf":
        result = _verify_pdf(data)
    elif ftype == "svg":
        _scan_svg_safety(data)
        result = InspectedUpload(file_type="svg", media_type="image/svg+xml",
                                 safe_bytes=data)
    elif ftype == "dxf":
        result = _verify_dxf(data)
    else:  # pragma: no cover - detect_file_type only returns the above
        raise UnsupportedUpload()

    log_event("upload_inspected", file_type=result.file_type,
              bytes=len(data), width=result.width, height=result.height,
              pages=result.page_count)
    return result


def inspect_upload_with_timeout(
    data: bytes, filename: str | None, content_type: str | None,
    timeout: float = UPLOAD_PARSE_TIMEOUT_SECONDS,
) -> InspectedUpload:
    """Run :func:`inspect_upload` under a hard wall-clock budget.

    The size/entity/pixel/page limits above bound the INPUT; they do not bound
    how long a pathological-but-within-limits file (deeply nested SVG groups
    under the node cap, a slow-to-open PDF, ...) takes to actually parse.
    Running the parse in a worker thread with a timeout caps worst-case
    latency regardless of what the parser itself does — and, because the
    calling endpoints are ``async def``, it also moves this CPU-bound work off
    the asyncio event loop, so one slow upload can never stall every other
    in-flight request on the same process.
    """
    import concurrent.futures

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = executor.submit(inspect_upload, data, filename, content_type)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        raise UploadTimeout(
            "This file took too long to process and was rejected.") from exc
    finally:
        # Never join/wait on an abandoned worker: a hung parse must not block
        # the request thread any longer than `timeout`.
        executor.shutdown(wait=False)
