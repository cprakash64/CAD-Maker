"""CAD package: manufacturing report + ZIP contents."""
import io
import json
import zipfile

from app.services.package_service import build_package_zip, manufacturing_report
from app.schemas.design_spec import DesignSpec, Hole


def _bracket() -> DesignSpec:
    return DesignSpec(
        object_type="rectangular_bracket",
        dimensions={"width": 80, "depth": 40, "thickness": 5},
        holes=[Hole(diameter=6.6, x=-25, y=0), Hole(diameter=6.6, x=25, y=0)],
    )


def test_manufacturing_report_shape():
    r = manufacturing_report(_bracket())
    assert r["object_type"] == "rectangular_bracket"
    assert "checks" in r and len(r["checks"]) > 0
    assert "bounding_box_mm" in r and r["bounding_box_mm"]["x"] > 0


def test_package_zip_contents():
    data = build_package_zip(_bracket(), "design-123")
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())

    assert "rectangular_bracket.step" in names
    assert "rectangular_bracket.stl" in names
    assert "design_spec.json" in names
    assert "manufacturing_report.json" in names
    assert "manufacturing_report.txt" in names
    assert "README.txt" in names
    for view in ("top", "front", "right", "left", "iso"):
        assert f"drawings/{view}.png" in names
        assert f"drawings/{view}.svg" in names

    # Every entry is non-empty and the spec round-trips.
    for n in names:
        assert len(zf.read(n)) > 0, f"{n} is empty"
    spec = json.loads(zf.read("design_spec.json"))
    assert spec["object_type"] == "rectangular_bracket"
    assert zf.read("rectangular_bracket.step")[:5] == b"ISO-1"


def test_package_endpoint(client, auth):
    h = auth["headers"]
    did = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80x40x5mm with two M6 holes"},
        headers=h,
    ).json()["id"]
    r = client.get(f"/api/designs/{did}/package", headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert "design_spec.json" in zf.namelist()
    assert "drawings/iso.png" in zf.namelist()


def test_package_requires_owner(client, auth, auth2):
    did = client.post(
        "/api/designs/create", json={"prompt": "bracket 80x40x5mm"}, headers=auth["headers"]
    ).json()["id"]
    assert client.get(
        f"/api/designs/{did}/package", headers=auth2["headers"]
    ).status_code == 404
