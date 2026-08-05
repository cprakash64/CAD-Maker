"""GLB (preview/web format) export: built on the fly from the existing
PreviewMesh, available even when STL/STEP are blocked (concept / critical
failure), never persisted as an ExportFile row."""
import json
import struct

from app.export.glb import build_glb_bytes, build_glb_from_preview_json


def test_build_glb_bytes_is_a_well_formed_binary_gltf():
    data = build_glb_bytes([0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2])
    magic, version, length = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67
    assert version == 2
    assert length == len(data)

    offset = 12
    json_chunk_len, json_chunk_type = struct.unpack("<II", data[offset:offset + 8])
    assert json_chunk_type == 0x4E4F534A
    offset += 8
    gltf = json.loads(data[offset:offset + json_chunk_len])
    assert gltf["asset"]["version"] == "2.0"
    assert gltf["accessors"][0]["count"] == 3
    assert gltf["accessors"][1]["count"] == 3
    offset += json_chunk_len

    bin_chunk_len, bin_chunk_type = struct.unpack("<II", data[offset:offset + 8])
    assert bin_chunk_type == 0x004E4942
    assert bin_chunk_len > 0


def test_build_glb_from_preview_json_none_when_empty():
    assert build_glb_from_preview_json(None) is None
    assert build_glb_from_preview_json({}) is None
    assert build_glb_from_preview_json({"positions": [], "indices": []}) is None


def test_glb_export_listed_and_downloadable(client, auth, legacy_engine):
    h = auth["headers"]
    d = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=h,
    ).json()
    # GLB is deliberately NOT in `exports` (that list is real, persisted
    # ExportFile rows -- STL/STEP only); it has its own DTO field.
    fmts = {e["fmt"] for e in d["exports"]}
    assert fmts == {"stl", "step"}
    assert d["preview_export"]["fmt"] == "glb"
    assert d["preview_export"]["size_bytes"] > 0

    r = client.get(f"/api/designs/{d['id']}/files/glb", headers=h)
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"glTF"
    assert r.headers["content-type"] == "model/gltf-binary"


def test_glb_available_even_when_step_stl_blocked(client, auth, legacy_engine, monkeypatch):
    from app.services import design_service

    h = auth["headers"]
    d = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=h,
    ).json()
    did = d["id"]

    monkeypatch.setattr(design_service, "is_critical_failure", lambda _design: True)
    r_step = client.get(f"/api/designs/{did}/files/step", headers=h)
    assert r_step.status_code == 409

    r_glb = client.get(f"/api/designs/{did}/files/glb", headers=h)
    assert r_glb.status_code == 200
    assert r_glb.content[:4] == b"glTF"


def test_export_eligibility_reports_per_format(client, auth, legacy_engine, monkeypatch):
    from app.services import design_service

    h = auth["headers"]
    d = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x6mm with two M6 holes"},
        headers=h,
    ).json()
    assert d["export_eligibility"]["formats"] == {"stl": True, "step": True, "glb": True}

    monkeypatch.setattr(design_service, "is_critical_failure", lambda _design: True)
    r = client.get(f"/api/designs/{d['id']}", headers=h)
    formats = r.json()["export_eligibility"]["formats"]
    assert formats["stl"] is False
    assert formats["step"] is False
    assert formats["glb"] is True


def test_glb_missing_mesh_404s(client, auth, legacy_engine):
    h = auth["headers"]
    r = client.post(
        "/api/designs/create",
        json={"prompt": "design a complete aircraft fuselage with wings and landing gear"},
        headers=h,
    )
    d = r.json()
    assert d["needs_decomposition"] is True
    got = client.get(f"/api/designs/{d['id']}/files/glb", headers=h)
    assert got.status_code == 404
