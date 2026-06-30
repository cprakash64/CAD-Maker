"""Raspberry Pi 4/5 device-preset enclosures — accurate board, posts, cutouts.

A "Raspberry Pi enclosure" must route to the LOCAL device preset (no LLM planner),
carry the board's mounting posts + connector cutouts, and NEVER be a generic box
reported as PASS.
"""
from __future__ import annotations

import time


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _no_llm(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("LLM cad planner called — RPi enclosure must be deterministic")
    monkeypatch.setattr("app.llm.factory.get_cad_provider", _boom)


def _val(d: dict) -> dict:
    return ((d.get("semantic_json") or {}).get("device_enclosure_validation")
            or _from_detail(d))


def _from_detail(d: dict) -> dict:
    # device_enclosure_validation is also surfaced via the raw semantic block; tests
    # read it through the DTO's semantic passthrough when present.
    return d.get("device_enclosure_validation") or {}


def _detail(d: dict) -> dict:
    return d.get("part_family_detail") or {}


def test_rpi4_enclosure_basic(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    t0 = time.perf_counter()
    d = _create(client, auth, "Make a Raspberry Pi 4 enclosure")
    assert time.perf_counter() - t0 < 30
    assert d["route"] == "device_preset_raspberry_pi"
    assert d["object_type"] == "rpi4_enclosure"
    det = _detail(d)
    assert det["device"] == "raspberry_pi_4_model_b"
    assert det["mounting_posts"] == 4
    assert det["micro_hdmi_count"] == 2
    # geometry exists and is board-sized, not an arbitrary box
    bb = d["bounding_box_mm"]
    assert 88 <= max(bb["x"], bb["y"]) <= 110
    # never a clean PASS for an approximate-cutout enclosure
    assert d["validation_status"] != "pass"
    assert d["validation_status"] != "critical_failure"


def test_rpi5_enclosure_basic(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a Raspberry Pi 5 enclosure")
    assert d["route"] == "device_preset_raspberry_pi"
    assert d["object_type"] == "rpi5_enclosure"
    det = _detail(d)
    assert det["device"] == "raspberry_pi_5_model_b"
    assert det["mounting_posts"] == 4
    ports = " ".join(det["port_cutouts"]).lower()
    assert "usb_c" in ports and "ethernet" in ports and "micro_hdmi" in ports


def test_rpi4_enclosure_full_options(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(
        client, auth,
        "Make a Raspberry Pi 4 enclosure with 2.5 mm wall thickness, snap-fit lid, "
        "ventilation slots, and embossed logo area on top")
    det = _detail(d)
    assert det["wall_thickness_mm"] == 2.5
    assert det["logo_feature_status"].startswith("embossed")
    assert det["lid_type"] == "snap_fit"
    blob = " ".join(d.get("assumptions") or []).lower()
    assert "snap-fit" in blob or "snap fit" in blob
    assert "logo" in blob
    assert d["validation_status"] != "critical_failure"


def test_rpi5_enclosure_explicit_cutouts(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(
        client, auth,
        "Make a Raspberry Pi 5 enclosure with 2.5 mm walls, removable lid, four "
        "mounting posts, USB-C cutout, two micro-HDMI cutouts, Ethernet cutout, "
        "and side ventilation slots")
    det = _detail(d)
    assert det["device"] == "raspberry_pi_5_model_b"
    assert det["mounting_posts"] == 4
    assert det["micro_hdmi_count"] == 2
    ports = " ".join(det["port_cutouts"]).lower()
    assert "usb_c" in ports and "ethernet" in ports


def test_rpi4_all_cutouts_present_and_through(client, auth, monkeypatch):
    """Every expected Pi 4 port cutout exists AND is a true through-opening."""
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a Raspberry Pi 4 enclosure")
    v = d["device_enclosure_validation"]
    assert v is not None
    assert v["usb_c_cutout_present"] is True
    assert v["micro_hdmi_cutout_count"] == 2
    assert v["usb_ethernet_cutout_present"] is True
    assert v["microsd_access_present"] is True
    # Through-hole verification: every required port opened fully to the cavity.
    assert v["all_required_ports_open"] is True
    assert v["blocked_ports"] == []
    names = {p["name"] for p in v["port_openings"]}
    for expected in ("usb_c_power", "micro_hdmi_0", "micro_hdmi_1", "ethernet",
                     "microsd"):
        assert expected in names
    for p in v["port_openings"]:
        if p["required"]:
            assert p["open"] is True, f"{p['name']} is not a through-hole"
            assert p["residual_mm3"] < 0.1


def test_each_cutout_is_a_true_through_hole_geometry(monkeypatch):
    """Unit-level: build the geometry and audit every required port through-hole,
    across a thin AND a thick wall (the thick wall is what the old pocket-cutter
    failed on)."""
    import app.cad.registry  # noqa: F401 — ensure templates registered
    from app.cad.templates.device_enclosure import take_last_port_audit
    from app.export.exporter import generate
    from app.schemas.design_spec import DesignSpec

    for ot in ("rpi4_enclosure", "rpi5_enclosure"):
        for wall in (2.0, 3.5):
            spec = DesignSpec(object_type=ot, dimensions={"wall_thickness": wall},
                              manufacturing_method="fdm_3d_print", material="PLA")
            generate(spec)
            audit = take_last_port_audit()
            assert audit, f"{ot}: no port audit recorded"
            blocked = [a["name"] for a in audit if a["required"] and not a["open"]]
            assert not blocked, f"{ot} wall={wall}: blocked ports {blocked}"


def test_blocked_port_is_failed_not_pass():
    """A residual (non-through) required port flips the enclosure validation to a
    failure verdict — never PASS."""
    from app.cad.device_presets import RASPBERRY_PI_4_MODEL_B as PI4  # type: ignore
    from app.services.design_service import _device_enclosure_validation

    good = [{"name": "ethernet", "side": "x_max", "kind": "port", "required": True,
             "open": True, "residual_mm3": 0.0}]
    bad = [{"name": "ethernet", "side": "x_max", "kind": "port", "required": True,
            "open": False, "residual_mm3": 2.0}]
    assert _device_enclosure_validation(PI4, 2.5, "removable", "x", good)[
        "all_required_ports_open"] is True
    v = _device_enclosure_validation(PI4, 2.5, "removable", "x", bad)
    assert v["all_required_ports_open"] is False
    assert v["blocked_ports"] == ["ethernet"]


def test_mounting_posts_exist(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a Raspberry Pi 5 enclosure")
    v = d["device_enclosure_validation"]
    assert v["mounting_posts_count"] == 4 and v["mounting_posts_aligned"] is True
    assert _detail(d)["mounting_posts"] == 4


def test_generic_electronics_enclosure_still_works(client, auth):
    """A non-Pi electronics enclosure still routes to the generic builder (not a
    device preset) and builds cleanly."""
    d = _create(client, auth,
                "Make an electronics enclosure 90 x 60 x 40 mm with 2.5 mm walls")
    assert d["object_type"] in ("electronics_enclosure", "sensor_enclosure")
    assert d.get("device_enclosure_validation") is None  # not a device preset
    assert d["validation_status"] != "critical_failure"
    assert d["bounding_box_mm"] is not None


def test_rpi_enclosure_not_a_generic_box(client, auth, monkeypatch):
    """Anti-generic: a Pi enclosure must carry preset posts + cutouts, never PASS as
    a plain box."""
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a Raspberry Pi 4 enclosure")
    det = _detail(d)
    assert det["family"] == "device_enclosure"
    assert len(det["port_cutouts"]) >= 5      # usb-c, 2x hdmi, usb/eth, microsd, ...
    assert det["mounting_posts"] == 4
    # Approximate cutouts => REVIEW, never a clean PASS implying a validated fit.
    assert d["validation_status"] in ("warning", "review")
