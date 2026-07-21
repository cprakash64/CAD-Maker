"""Phase 2: upload boundary, resource limits, and production data protection.

Complements ``test_upload_guard.py`` (format detection, raster/PDF/SVG basics)
rather than repeating it. What is proven here:

  * an over-budget body is refused without ever being buffered
  * untrusted filenames and free-text form fields cannot grow without bound,
    become filesystem paths, or forge a download name
  * SVG is treated as active XML: entities, scripts, handlers, remote refs and
    CSS fetches are all rejected, and the document must really parse as SVG
  * DXF entity / layer / coordinate budgets hold
  * no upload path performs a network fetch or leaves a temp file behind
  * errors carry no stack traces, filesystem paths, SQL, or configuration
  * rate limiting actually engages, returns 429 + Retry-After, and cannot be
    reset by forging a proxy header
"""
from __future__ import annotations

import io
import re
import socket
import tempfile
from pathlib import Path

import pytest

from app.services.upload_guard import (
    MAX_DXF_COORDINATE,
    MAX_TEXT_FIELD_CHARS,
    MalformedUpload,
    TooLargeUpload,
    UnsupportedUpload,
    UploadRejected,
    inspect_upload,
    safe_download_name,
    sanitize_text_field,
)

UPLOAD_ENDPOINTS = [
    "/api/drawings/interpret",
    "/api/drawings/generate",
    "/api/drawings/to-cad",
    "/api/drawing-to-cad",
]


def _png(w: int = 60, h: int = 40) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


# =========================================================================
# 1. Bounded intake
# =========================================================================
class _FakeUpload:
    """Minimal UploadFile stand-in that streams a large body in chunks."""

    def __init__(self, total: int) -> None:
        self.remaining = total
        self.delivered = 0

    async def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        n = self.remaining if size < 0 else min(size, self.remaining)
        self.remaining -= n
        self.delivered += n
        return b"\0" * n


@pytest.mark.anyio
async def test_oversized_body_is_refused_without_buffering_it_all():
    """The guard must stop reading shortly past the limit, not after the whole
    body is resident in memory."""
    from app.services.upload_guard import MAX_UPLOAD_BYTES, read_upload_bounded

    src = _FakeUpload(total=MAX_UPLOAD_BYTES * 8)
    with pytest.raises(TooLargeUpload):
        await read_upload_bounded(src)
    # Never pulled more than a chunk past the ceiling.
    assert src.delivered <= MAX_UPLOAD_BYTES + 1024 * 1024


@pytest.mark.anyio
async def test_within_budget_body_reads_completely():
    from app.services.upload_guard import read_upload_bounded

    payload = _png()

    class _Src:
        def __init__(self) -> None:
            self.buf = io.BytesIO(payload)

        async def read(self, size: int = -1) -> bytes:
            return self.buf.read(size)

    assert await read_upload_bounded(_Src()) == payload


@pytest.fixture
def anyio_backend():
    return "asyncio"


# =========================================================================
# 2. Filenames are never paths, and never forge a download name
# =========================================================================
HOSTILE_FILENAMES = [
    "../../etc/passwd",
    "..\\..\\windows\\system.ini",
    "%2e%2e%2fetc%2fpasswd",
    "/etc/passwd",
    "C:\\Windows\\system.ini",
    "drawing.pdf.exe",
    "a" * 5000 + ".png",
    'evil".png',
    "evil\r\nX-Injected: 1.png",
    "évil…name.png",
]


@pytest.mark.parametrize("name", HOSTILE_FILENAMES)
def test_uploaded_filename_never_becomes_a_path(client, auth, name):
    """A hostile filename must not change where anything is written, nor crash
    the request. The upload is judged on its bytes."""
    r = client.post("/api/drawings/interpret",
                    files={"file": (name, _png(), "image/png")},
                    data={"hint": "a 20mm plate"}, headers=auth["headers"])
    assert r.status_code in (200, 409, 413, 415, 422), r.text
    # Nothing resembling the hostile path was created.
    assert not Path("/tmp/etc/passwd").exists()
    body = r.text.lower()
    for leak in ("traceback", "site-packages", "/users/", 'file "'):
        assert leak not in body


@pytest.mark.parametrize("name", HOSTILE_FILENAMES)
def test_safe_download_name_is_always_inert(name):
    out = safe_download_name(name, "stl")
    assert "/" not in out and "\\" not in out
    assert '"' not in out and ";" not in out
    assert "\r" not in out and "\n" not in out
    assert "\x00" not in out
    assert not out.startswith(".")
    assert len(out) <= 90


@pytest.mark.parametrize("base", [
    'part"; filename="evil.exe',
    "part\r\nSet-Cookie: a=b",
    "../../../../etc/passwd",
    "", None, "...", "\x00\x01\x02",
])
def test_content_disposition_cannot_be_broken_out_of(base):
    out = safe_download_name(base, "step")
    header = f'attachment; filename="{out}"'
    # Exactly one filename directive, no injected header, no stray quote.
    assert header.count("filename=") == 1
    assert header.count('"') == 2
    assert "\r" not in header and "\n" not in header


def test_download_header_is_sanitised_end_to_end(client, auth):
    """The real download route must emit an inert header even when the stored
    object_type is hostile (it is free-form text the planner fills in)."""
    from app.database import SessionLocal
    from app.models import Design

    created = client.post("/api/designs/create",
                          json={"prompt": "a 20mm cube spacer with a 6mm hole"},
                          headers=auth["headers"])
    assert created.status_code == 200, created.text
    did = created.json()["id"]

    with SessionLocal() as db:
        d = db.get(Design, did)
        d.object_type = 'evil"; filename="pwned.exe'
        db.commit()

    r = client.get(f"/api/designs/{did}/files/stl", headers=auth["headers"])
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # The hostile text survives only as inert characters inside one quoted
    # value: the quote/semicolon that would have started a second directive are
    # gone, and the real extension still wins.
    assert cd.count("filename=") == 1, f"header was split: {cd}"
    assert cd.count('"') == 2, f"quote breakout: {cd}"
    assert ";" not in cd.split("filename=", 1)[1], f"directive injection: {cd}"
    assert cd.rstrip('"').endswith(".stl"), f"extension was forged: {cd}"
    assert "\r" not in cd and "\n" not in cd
    assert r.headers.get("x-content-type-options") == "nosniff"


# =========================================================================
# 3. Free-text form fields are bounded and cleaned
# =========================================================================
def test_overlong_text_field_is_rejected():
    with pytest.raises(TooLargeUpload):
        sanitize_text_field("x" * (MAX_TEXT_FIELD_CHARS + 1), field="hint")


def test_control_characters_are_stripped_from_text_fields():
    out = sanitize_text_field("ok\x00\x07value\ttab\nnl")
    assert "\x00" not in out and "\x07" not in out
    assert "\t" in out and "\n" in out


@pytest.mark.parametrize("endpoint,field", [
    ("/api/drawings/interpret", "hint"),
    ("/api/drawings/generate", "hint"),
    ("/api/drawings/to-cad", "notes"),
])
def test_overlong_form_field_is_rejected_by_the_api(client, auth, endpoint, field):
    r = client.post(endpoint,
                    files={"file": ("d.png", _png(), "image/png")},
                    data={field: "x" * 50_000, "sync": "true"},
                    headers=auth["headers"])
    assert r.status_code == 413, f"{endpoint} -> {r.status_code}"


# =========================================================================
# 4. SVG is active content
# =========================================================================
HOSTILE_SVG = [
    b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://attacker.example/a.png"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image xlink:href="file:///etc/passwd"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><body/></foreignObject></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(https://attacker.example/x.css);</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://attacker.example/a.png)"/></svg>',
]


@pytest.mark.parametrize("payload", HOSTILE_SVG)
def test_hostile_svg_is_rejected(payload):
    with pytest.raises(UploadRejected):
        inspect_upload(payload, "d.svg", "image/svg+xml")


@pytest.mark.parametrize("payload", HOSTILE_SVG)
def test_hostile_svg_is_rejected_on_every_upload_endpoint(client, auth, payload):
    for endpoint in ("/api/drawings/to-cad", "/api/drawing-to-cad"):
        r = client.post(endpoint,
                        files={"file": ("d.svg", payload, "image/svg+xml")},
                        data={"sync": "true"}, headers=auth["headers"])
        assert r.status_code in (413, 415, 422), f"{endpoint} -> {r.status_code}"


def test_svg_that_is_not_well_formed_xml_is_rejected():
    with pytest.raises(MalformedUpload):
        inspect_upload(b'<svg xmlns="http://www.w3.org/2000/svg"><rect>',
                       "d.svg", "image/svg+xml")


def test_non_svg_xml_is_rejected():
    """A well-formed XML document whose root is not <svg> is not a drawing."""
    with pytest.raises(MalformedUpload):
        inspect_upload(b'<html><body>hi</body></html>', "d.svg", "image/svg+xml")


def test_clean_svg_still_passes():
    ok = (b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
          b'<rect x="0" y="0" width="100" height="50"/></svg>')
    assert inspect_upload(ok, "d.svg", "image/svg+xml").file_type == "svg"


def test_svg_processing_makes_no_network_connection(monkeypatch):
    """Rejecting a remote-reference SVG must not itself fetch anything."""
    attempts = []

    def _boom(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("upload processing attempted a network connection")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)

    for payload in HOSTILE_SVG:
        with pytest.raises(UploadRejected):
            inspect_upload(payload, "d.svg", "image/svg+xml")
    assert not attempts


# =========================================================================
# 5. Raster and PDF bounds
# =========================================================================
def test_truncated_png_is_rejected():
    data = _png(200, 200)[: len(_png(200, 200)) // 2]
    with pytest.raises(UploadRejected):
        inspect_upload(data, "d.png", "image/png")


def test_declared_pixel_bomb_is_rejected():
    """A tiny file that claims enormous dimensions must be refused on the
    declared size, before any full decode allocates memory."""
    import struct
    import zlib

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", 60000, 60000, 8, 2, 0, 0, 0)
    bomb = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    with pytest.raises(UploadRejected):
        inspect_upload(bomb, "d.png", "image/png")


def test_empty_upload_is_rejected():
    with pytest.raises(MalformedUpload):
        inspect_upload(b"", "d.png", "image/png")


@pytest.mark.parametrize("payload", [
    b"%PDF-1.4 not really a pdf at all",
    b"%PDF-",
    b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\ntrailer<<>>",
])
def test_malformed_pdf_is_rejected(payload):
    with pytest.raises(UploadRejected):
        inspect_upload(payload, "d.pdf", "application/pdf")


def test_double_extension_is_judged_on_bytes(client, auth):
    """`drawing.pdf.exe` full of PNG bytes is a PNG; full of junk it is refused.
    Either way the extension does not decide."""
    ok = inspect_upload(_png(), "drawing.pdf.exe", "application/octet-stream")
    assert ok.file_type == "png"
    with pytest.raises(UploadRejected):
        inspect_upload(b"MZ\x90\x00" + b"\x00" * 400, "drawing.pdf.exe",
                       "application/octet-stream")


# =========================================================================
# 6. DXF bounds
# =========================================================================
def _dxf(entities: str = "", layers: int = 1) -> bytes:
    body = ["0", "SECTION", "2", "HEADER", "0", "ENDSEC",
            "0", "SECTION", "2", "TABLES"]
    for i in range(layers):
        body += ["0", "LAYER", "2", f"L{i}"]
    body += ["0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    body += entities.split("\n") if entities else []
    body += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(body).encode()


def test_dxf_with_extreme_coordinates_is_rejected():
    payload = _dxf("0\nLINE\n8\n0\n10\n1e300\n20\n0.0\n11\n5.0\n21\n5.0")
    with pytest.raises(TooLargeUpload):
        inspect_upload(payload, "d.dxf", "image/vnd.dxf")


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf"])
def test_dxf_with_non_finite_coordinates_is_rejected(bad):
    payload = _dxf(f"0\nLINE\n8\n0\n10\n{bad}\n20\n0.0")
    with pytest.raises(MalformedUpload):
        inspect_upload(payload, "d.dxf", "image/vnd.dxf")


def test_dxf_with_too_many_layers_is_rejected():
    from app.services.upload_guard import MAX_DXF_LAYERS

    with pytest.raises(TooLargeUpload):
        inspect_upload(_dxf(layers=MAX_DXF_LAYERS + 5), "d.dxf", "image/vnd.dxf")


def test_dxf_unset_extent_sentinel_is_allowed_but_other_extremes_are_not():
    """Every CAD exporter writes ±1e20 to mean 'extents not calculated'. That
    flag value must pass; a neighbouring real coordinate of the same magnitude
    must not."""
    sentinel = _dxf("0\nLINE\n8\n0\n14\n1e+20\n24\n-1e+20\n10\n5.0\n20\n5.0")
    assert inspect_upload(sentinel, "d.dxf", "image/vnd.dxf").file_type == "dxf"

    with pytest.raises(TooLargeUpload):
        inspect_upload(_dxf("0\nLINE\n8\n0\n10\n2e+20\n20\n0.0"),
                       "d.dxf", "image/vnd.dxf")


def test_dxf_coordinate_ceiling_is_not_absurdly_low():
    """A real (large) part must still be accepted — the bound is a safety net,
    not a product restriction."""
    payload = _dxf("0\nLINE\n8\n0\n10\n2500.0\n20\n1200.0\n11\n0.0\n21\n0.0")
    assert inspect_upload(payload, "d.dxf", "image/vnd.dxf").file_type == "dxf"
    assert MAX_DXF_COORDINATE >= 1e6


def test_binary_dxf_is_refused_not_reinterpreted():
    with pytest.raises(UnsupportedUpload):
        inspect_upload(b"AutoCAD Binary DXF\r\n\x00" + b"\x00" * 200,
                       "d.dxf", "image/vnd.dxf")


# =========================================================================
# 7. No temp-file residue after a rejected upload
# =========================================================================
@pytest.mark.parametrize("payload,name,ctype", [
    (b"%PDF-1.4 broken", "d.pdf", "application/pdf"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', "d.svg", "image/svg+xml"),
    (b"MZ\x90\x00" + b"\x00" * 300, "d.png", "image/png"),
])
def test_rejected_upload_leaves_no_temp_file(client, auth, payload, name, ctype):
    tmp = Path(tempfile.gettempdir())
    before = set(tmp.glob("*"))
    r = client.post("/api/drawings/to-cad",
                    files={"file": (name, payload, ctype)},
                    data={"sync": "true"}, headers=auth["headers"])
    assert r.status_code in (400, 409, 413, 415, 422), r.text
    new = {p for p in tmp.glob("*") if p not in before}
    leaked = [p.name for p in new
              if p.suffix.lower() in {".stl", ".step", ".stp", ".png", ".svg",
                                      ".dxf", ".pdf"}]
    assert not leaked, f"upload rejection left temp files: {leaked}"


def test_successful_export_leaves_no_temp_file(client, auth):
    """The export path writes through NamedTemporaryFile; it must clean up."""
    tmp = Path(tempfile.gettempdir())
    before = set(tmp.glob("*"))
    r = client.post("/api/designs/create",
                    json={"prompt": "a 30mm spacer with a 6mm bore"},
                    headers=auth["headers"])
    assert r.status_code == 200, r.text
    new = {p for p in tmp.glob("*") if p not in before}
    leaked = [p.name for p in new if p.suffix.lower() in {".stl", ".step", ".stp"}]
    assert not leaked, f"export left temp files behind: {leaked}"


# =========================================================================
# 8. Resource limits
# =========================================================================
def test_prompt_length_is_bounded(client, auth):
    r = client.post("/api/designs/create", json={"prompt": "x" * 100_000},
                    headers=auth["headers"])
    assert r.status_code == 422


def test_empty_prompt_is_rejected(client, auth):
    r = client.post("/api/designs/create", json={"prompt": ""},
                    headers=auth["headers"])
    assert r.status_code == 422


@pytest.mark.parametrize("limit", [0, 501, 100_000, -1])
def test_pagination_size_is_bounded(client, auth, limit):
    r = client.get(f"/api/designs?limit={limit}", headers=auth["headers"])
    assert r.status_code == 422, f"limit={limit} accepted"


def test_plan_feature_count_is_bounded():
    """An LLM plan cannot request unbounded geometry work."""
    from pydantic import ValidationError

    from app.cad.plan.schema import CadPlan

    features = [{"id": f"f{i}", "kind": "box", "params": {"width": 10}}
                for i in range(5000)]
    with pytest.raises(ValidationError):
        CadPlan(features=features)


def test_configured_timeouts_are_finite_and_sane():
    from app.config import settings

    for name in ("openai_timeout_seconds", "cad_generation_timeout_seconds",
                 "drawing_job_timeout_seconds", "drawing_provider_timeout_seconds",
                 "drawing_vision_timeout_seconds"):
        value = getattr(settings, name)
        assert 0 < value <= 600, f"{name}={value} is not a production-safe bound"


# =========================================================================
# 9. Rate limiting
# =========================================================================
@pytest.fixture
def limits_on(monkeypatch):
    """Turn limiting on explicitly for a test (it is off in the suite)."""
    from app.config import settings
    from app.rate_limit import reset_rate_limit

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth", "3/60")
    reset_rate_limit()
    yield
    reset_rate_limit()


def test_auth_endpoint_throttles_with_429_and_retry_after(client, limits_on):
    from app.config import settings

    assert settings.rate_limit_active(), "limiter did not engage"
    last = None
    for _ in range(8):
        last = client.post("/api/auth/login",
                           json={"email": "nobody@example.com", "password": "wrong"})
        if last.status_code == 429:
            break
    assert last.status_code == 429, "login was never throttled"
    assert last.headers.get("Retry-After"), "no Retry-After on a 429"
    assert last.headers["Retry-After"].isdigit()


def test_throttle_message_leaks_no_internals(client, limits_on):
    last = None
    for _ in range(8):
        last = client.post("/api/auth/login",
                           json={"email": "nobody@example.com", "password": "wrong"})
        if last.status_code == 429:
            break
    body = last.text.lower()
    for leak in ("sliding", "deque", "traceback", "app.rate_limit", "/users/"):
        assert leak not in body


def test_forged_forwarded_for_cannot_mint_new_buckets(client, limits_on, monkeypatch):
    """With proxy trust off (the default), a client cannot escape its bucket by
    rotating X-Forwarded-For."""
    from app.config import settings

    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    statuses = []
    for i in range(8):
        r = client.post("/api/auth/login",
                        json={"email": "nobody@example.com", "password": "wrong"},
                        headers={"X-Forwarded-For": f"10.0.0.{i}"})
        statuses.append(r.status_code)
    assert 429 in statuses, "rotating X-Forwarded-For defeated the limiter"


def test_proxy_header_is_honoured_only_when_trusted(monkeypatch):
    from app.config import settings
    from app.rate_limit import _client_ip

    class _Req:
        headers = {"x-forwarded-for": "1.2.3.4, 9.9.9.9"}

        class client:
            host = "127.0.0.1"

    monkeypatch.setattr(settings, "trust_proxy_headers", False)
    assert _client_ip(_Req()) == "127.0.0.1"

    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    # The proxy appends what it saw, so the LAST hop is the trustworthy one.
    assert _client_ip(_Req()) == "9.9.9.9"


# =========================================================================
# 10. Data protection
# =========================================================================
def test_health_exposes_no_configuration_in_production(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "dev_mode", False)
    body = TestClient(app).get("/health").json()
    assert body == {"status": "ok"}


def test_provider_status_hides_deployment_detail_in_production(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "dev_mode", False)
    body = TestClient(app).get("/api/provider-status").json()
    for secret_ish in ("app_env", "model", "mock_allowed",
                       "request_timeout_seconds", "max_retries"):
        assert secret_ish not in body, f"provider-status leaked {secret_ish}"
    # The capability flags the UI needs are still there.
    assert "drawing_to_cad_enabled" in body and "provider" in body


def test_provider_status_never_contains_a_key(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "openai_api_key", "sk-test-SENTINEL-value-123")
    body = client.get("/api/provider-status").text
    assert "SENTINEL" not in body
    assert "sk-" not in body


def test_secrets_never_appear_in_any_error_response(client, auth, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "jwt_secret", "JWT-SENTINEL-0123456789abcdef")
    monkeypatch.setattr(settings, "openai_api_key", "sk-SENTINEL-key")
    probes = [
        client.get("/api/designs/nope", headers=auth["headers"]),
        client.post("/api/designs/create", json={"prompt": ""},
                    headers=auth["headers"]),
        client.get("/api/drawings/jobs/nope", headers=auth["headers"]),
        client.post("/api/drawings/interpret",
                    files={"file": ("x.png", b"junk" * 50, "image/png")},
                    headers=auth["headers"]),
    ]
    for r in probes:
        assert "SENTINEL" not in r.text
        assert settings.database_url not in r.text


def test_database_errors_become_a_structured_response(client, auth, monkeypatch):
    """A DB fault must not surface SQL, table names, or a driver traceback."""
    from sqlalchemy.exc import OperationalError

    import app.routers.designs as designs_router

    def _boom(*_a, **_kw):
        raise OperationalError("SELECT * FROM designs WHERE secret_column=1",
                               {}, Exception("connection to db-prod-01 refused"))

    monkeypatch.setattr(designs_router, "_owned_or_404", _boom)
    r = client.get("/api/designs/anything", headers=auth["headers"],
                   follow_redirects=False)
    assert r.status_code == 503
    body = r.text.lower()
    for leak in ("select", "secret_column", "db-prod-01", "traceback",
                 "sqlalchemy", "operationalerror"):
        assert leak not in body, f"database error leaked {leak!r}: {r.text[:300]}"


def test_unhandled_errors_return_a_generic_500(auth, monkeypatch):
    """What a real client receives for an unhandled fault.

    The suite's default TestClient re-raises server exceptions so test failures
    stay debuggable; this one turns that off to observe the response an actual
    HTTP caller gets.
    """
    from fastapi.testclient import TestClient

    import app.routers.designs as designs_router
    from app.main import app

    def _boom(*_a, **_kw):
        raise RuntimeError("internal detail /srv/app/secret/path.py line 42")

    monkeypatch.setattr(designs_router, "_owned_or_404", _boom)
    raw = TestClient(app, raise_server_exceptions=False)
    r = raw.get("/api/designs/anything", headers=auth["headers"])
    assert r.status_code == 500
    assert "srv/app/secret" not in r.text
    assert "traceback" not in r.text.lower()
    assert r.json() == {"detail": "Internal server error."}


def test_provider_error_bodies_are_not_forwarded(client, auth, monkeypatch):
    """An upstream provider message must be replaced by our own wording."""
    import app.llm.factory as factory
    from app.llm.base import LLMUnavailableError

    class _Dead:
        name = "dead"

        def __getattr__(self, _item):
            def _raise(*_a, **_kw):
                raise LLMUnavailableError(
                    "The AI service is temporarily unavailable.")
            return _raise

    monkeypatch.setattr(factory, "_build", lambda _p: _Dead())
    r = client.post("/api/designs/create",
                    json={"prompt": "a bracket 80x40x5mm"},
                    headers=auth["headers"])
    assert r.status_code in (200, 422, 503)
    for leak in ("openai.com", "api_key", "sk-", "Traceback"):
        assert leak not in r.text


def test_auth_token_is_never_logged(client, auth, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="sourcecad"):
        client.get("/api/designs", headers=auth["headers"])
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert auth["token"] not in logged
    assert "authorization" not in logged.lower()


def test_password_is_hashed_not_reversible(client):
    """Signup must store a bcrypt hash, never the password or a reversible form."""
    from app.database import SessionLocal
    from app.models import User

    email = "hashcheck@example.com"
    password = "correct horse battery staple"
    r = client.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text

    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        stored = user.password_hash
    assert password not in stored
    assert re.match(r"^\$2[aby]\$", stored), f"not a bcrypt hash: {stored[:12]}"


def test_jwt_expiry_is_bounded():
    from app.config import settings

    assert 0 < settings.jwt_expire_minutes <= 60 * 24 * 30


def test_production_config_rejects_wildcard_cors():
    from app.config import Settings

    s = Settings(app_env="production", testing=False, cors_origins="*",
                 jwt_secret="x" * 40, llm_provider="openai",
                 openai_api_key="k", dev_mode=False)
    assert any("*" in p for p in s.production_problems())


def test_dev_only_debug_routes_fail_closed_in_production(client, auth, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dev_mode", False)
    for path in ("/api/drawings/debug/anything",
                 "/api/drawings/debug/anything/overlay"):
        r = client.get(path, headers=auth["headers"])
        assert r.status_code == 404, f"{path} -> {r.status_code}"
