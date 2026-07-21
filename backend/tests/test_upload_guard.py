"""Phase 1D: strict, shared upload verification across every drawing endpoint.

Content is judged by its bytes, never by filename or Content-Type. Malicious or
malformed uploads are rejected with the right status (413/415/422) and never
reach the vision model or the geometry parsers. Valid uploads keep working, and
the limits are identical on /interpret, /generate, /to-cad and the alias.
"""
from __future__ import annotations

import io

import pytest

from app.services.upload_guard import (
    MalformedUpload,
    TooLargeUpload,
    UnsupportedUpload,
    inspect_upload,
)


def _png(w=100, h=100) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(w=100, h=100) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (150, 150, 150)).save(buf, format="JPEG")
    return buf.getvalue()


CLEAN_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="120mm" height="80mm" '
             b'viewBox="0 0 120 80"><rect x="0" y="0" width="120" height="80"/>'
             b'<circle cx="30" cy="40" r="3"/></svg>')


# --- unit-level: the gate itself ------------------------------------------
def test_garbage_named_png_is_rejected():
    with pytest.raises(UnsupportedUpload):
        inspect_upload(b"NOT AN IMAGE" * 50, "drawing.png", "image/png")


def test_png_bytes_named_jpg_is_accepted_as_png():
    res = inspect_upload(_png(), "drawing.jpg", "image/jpeg")
    assert res.file_type == "png" and res.is_raster  # magic wins over extension


def test_truncated_png_is_malformed():
    with pytest.raises(MalformedUpload):
        inspect_upload(_png()[:60], "drawing.png", "image/png")


def test_oversized_dimensions_rejected():
    with pytest.raises(TooLargeUpload):
        inspect_upload(_png(13000, 100), "big.png", "image/png")


def test_empty_upload_rejected():
    with pytest.raises(MalformedUpload):
        inspect_upload(b"", "x.png", "image/png")


def test_svg_with_script_rejected():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><rect width="5" height="5"/></svg>'
    with pytest.raises(MalformedUpload):
        inspect_upload(svg, "x.svg", "image/svg+xml")


def test_svg_with_event_handler_rejected():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5" onload="x()"/></svg>'
    with pytest.raises(MalformedUpload):
        inspect_upload(svg, "x.svg", "image/svg+xml")


def test_svg_with_external_reference_rejected():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="http://evil.example/x.png"/></svg>'
    with pytest.raises(MalformedUpload):
        inspect_upload(svg, "x.svg", "image/svg+xml")


def test_svg_entity_expansion_rejected():
    svg = (b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY a "AAAA">]>'
           b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="5" height="5"/></svg>')
    with pytest.raises(MalformedUpload):
        inspect_upload(svg, "x.svg", "image/svg+xml")


def test_svg_foreignobject_rejected():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><body/></foreignObject></svg>'
    with pytest.raises(MalformedUpload):
        inspect_upload(svg, "x.svg", "image/svg+xml")


def test_clean_svg_accepted():
    res = inspect_upload(CLEAN_SVG, "x.svg", "image/svg+xml")
    assert res.file_type == "svg"


def test_fake_pdf_rejected():
    with pytest.raises((MalformedUpload, UnsupportedUpload)):
        inspect_upload(b"%PDF-1.4 this is not really a pdf body", "x.pdf", "application/pdf")


def test_malformed_dxf_rejected():
    with pytest.raises((MalformedUpload, UnsupportedUpload)):
        inspect_upload(b"just some random text, not a dxf at all", "x.dxf", "image/vnd.dxf")


def _pdf(pages: int = 1) -> bytes:
    from PIL import Image
    imgs = [Image.new("RGB", (120, 120), (255, 255, 255)) for _ in range(pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=pages > 1, append_images=imgs[1:])
    return buf.getvalue()


def test_valid_single_page_pdf_accepted():
    res = inspect_upload(_pdf(1), "x.pdf", "application/pdf")
    assert res.file_type == "pdf" and res.page_count == 1


def test_excess_page_pdf_rejected():
    with pytest.raises(TooLargeUpload):
        inspect_upload(_pdf(30), "big.pdf", "application/pdf")


def test_metadata_is_stripped_from_rasters():
    # A re-encoded raster carries no EXIF; safe_bytes must decode cleanly as PNG.
    from PIL import Image
    res = inspect_upload(_jpeg(), "x.jpg", "image/jpeg")
    assert res.media_type == "image/png"
    with Image.open(io.BytesIO(res.safe_bytes)) as im:
        assert not im.getexif()


# --- endpoint-level: consistent across every drawing route ----------------
@pytest.fixture
def _auth(client, auth):
    return auth


ENDPOINTS = ["/api/drawings/interpret", "/api/drawings/generate",
             "/api/drawings/to-cad", "/api/drawing-to-cad"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_garbage_png_rejected_on_every_endpoint(client, _auth, endpoint):
    r = client.post(endpoint, files={"file": ("x.png", b"GARBAGE" * 40, "image/png")},
                    data={"sync": "true"}, headers=_auth["headers"])
    assert r.status_code in (413, 415, 422), f"{endpoint} accepted garbage: {r.status_code}"
    # never a server error, never a path/traceback leak
    assert r.status_code < 500
    body = r.text.lower()
    assert "traceback" not in body and "/users/" not in body


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_svg_script_rejected_on_every_endpoint(client, _auth, endpoint):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script><rect width="5" height="5"/></svg>'
    r = client.post(endpoint, files={"file": ("x.svg", svg, "image/svg+xml")},
                    data={"sync": "true"}, headers=_auth["headers"])
    assert r.status_code in (413, 415, 422), f"{endpoint}: {r.status_code}"


def test_valid_png_accepted_on_interpret(client, auth):
    r = client.post("/api/drawings/interpret",
                    files={"file": ("x.png", _png(200, 150), "image/png")},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text


def test_valid_dxf_fixture_still_works_on_to_cad(client, auth):
    from pathlib import Path
    dxf = Path(__file__).parent / "data" / "simple_plate_4_holes.dxf"
    if not dxf.exists():
        pytest.skip("DXF fixture not present")
    r = client.post("/api/drawings/to-cad",
                    files={"file": ("plate.dxf", dxf.read_bytes(), "image/vnd.dxf")},
                    data={"sync": "true"}, headers=auth["headers"])
    assert r.status_code == 200, r.text


def test_content_type_lie_does_not_fool_detection(client, auth):
    """A real DXF sent as application/octet-stream is still recognised by bytes."""
    from pathlib import Path
    dxf = Path(__file__).parent / "data" / "simple_plate_4_holes.dxf"
    if not dxf.exists():
        pytest.skip("DXF fixture not present")
    r = client.post("/api/drawings/to-cad",
                    files={"file": ("plate.bin", dxf.read_bytes(), "application/octet-stream")},
                    data={"sync": "true"}, headers=auth["headers"])
    assert r.status_code == 200, r.text
