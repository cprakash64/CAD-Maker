"""Object Intelligence v2 — measurement, feature contract, phone holder, Jetson,
source pipeline, cache. Builds on test_object_intelligence.py.
"""
from __future__ import annotations


def _create(client, auth, prompt: str) -> dict:
    r = client.post("/api/designs/create", json={"prompt": prompt}, headers=auth["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def _no_llm(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("LLM cad planner called — must be deterministic")
    monkeypatch.setattr("app.llm.factory.get_cad_provider", _boom)


def _oi(d):
    return d.get("object_intelligence") or {}


def _measured(d):
    return ((d.get("dimension_report") or {}).get("measured")) or {}


# === 1: NEMA 17 hole measurement ===========================================
def test_nema17_hole_measurement(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a mount for a NEMA 17 stepper motor")
    assert d["object_type"] == "motor_mount"
    m = _measured(d)
    assert m.get("motor_mounting_holes") == 4
    assert m.get("center_bore") == 1
    assert m.get("hole_count", 0) >= 5 and m.get("through_holes") == 5
    assert d["validation_status"] in ("pass", "warning")  # PASS-eligible, not critical


# === 2: custom PCB USB-C cutout (feature contract) =========================
def test_custom_pcb_usb_c_required(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(
        client, auth,
        "Make a custom PCB enclosure 100 mm by 60 mm by 30 mm with 2.5 mm walls, "
        "four 3 mm mounting holes, removable lid, and USB-C cutout")
    assert d["object_type"] == "generic_fitted_box"
    fc = d["object_intelligence"]["feature_contract"]
    assert "usb_c_cutout" in fc["requested_features"]
    assert "usb_c_cutout" in fc["generated_features"]
    assert fc["pass_blocking_missing_features"] == []
    m = _measured(d)
    assert m.get("through_port_cutouts_verified") is True
    assert m.get("mounting_holes") == 4


def test_custom_pcb_missing_feature_blocks_pass():
    """If a required requested feature is NOT generated, PASS is blocked."""
    from app.cad.object_intelligence.features import build_feature_contract
    fc = build_feature_contract(["usb_c_cutout", "mounting_holes"], ["mounting_holes"])
    assert fc["missing_features"] == ["usb_c_cutout"]
    assert fc["pass_blocking_missing_features"] == ["usb_c_cutout"]


# === 3: phone holder ========================================================
def test_phone_holder_iphone15(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a phone holder for iPhone 15")
    # iPhone 15 is a known preset -> a real phone_holder is built (cradle/back/lip/
    # cable notch), never geometry while the contract says "unsupported".
    assert d["object_type"] == "phone_holder"
    assert d["bounding_box_mm"] is not None
    oi = _oi(d)
    assert "iphone 15" in oi["object_detected"].lower()
    fc = oi.get("feature_contract") or {}
    for feat in ("cradle", "back_support", "bottom_lip", "cable_notch"):
        assert feat in fc.get("generated_features", [])
    c = d.get("part_family_contract") or {}
    assert c.get("generation_honesty_status") != "unsupported"
    assert d["validation_status"] in ("pass", "warning")


# === 4: Jetson Nano typo ====================================================
def test_jetson_nano_typo(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "make an enclouser for nvidia jetson nano developer kit")
    assert d["object_type"] == "board_enclosure"
    assert not d.get("needs_clarification")
    assert _oi(d)["object_detected"] == "Jetson Nano Developer Kit"
    assert d["device_enclosure_validation"]["mounting_posts_count"] == 4


# === 5: Jetson Orin Nano ====================================================
def test_jetson_orin_nano(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make an enclosure for NVIDIA Jetson Orin Nano Developer Kit")
    assert d["object_type"] == "board_enclosure"
    v = d["device_enclosure_validation"]
    assert v["mounting_posts_count"] == 4 and v["all_required_ports_open"] is True
    assert d["validation_status"] != "pass"   # approximate connectors -> REVIEW


# === 6: unknown branded object ==============================================
def test_unknown_branded_no_pass(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make an enclosure for SuperBoard X9000")
    assert d["validation_status"] != "pass"
    # clarify (no verified source) — never a generic-box PASS
    assert d.get("needs_clarification") or _oi(d).get("source_type") in ("unknown", None)


# === 7: feature contract parsing ===========================================
def test_feature_parsing():
    from app.cad.object_intelligence.features import parse_requested_features
    feats = parse_requested_features(
        "enclosure with USB-C, two micro-HDMI, Ethernet, mounting holes, ventilation, "
        "and a logo area")
    for f in ("usb_c_cutout", "micro_hdmi_cutout", "ethernet_cutout",
              "mounting_holes", "ventilation", "logo_area"):
        assert f in feats


# === 8: cache avoids repeat lookup =========================================
def test_extraction_cache(monkeypatch, tmp_path):
    import app.cad.object_intelligence.cache as cache
    import app.cad.object_intelligence.extraction_pipeline as pipe
    from app.cad.object_intelligence.mechanical_spec import (
        SOURCE_OFFICIAL,
        MechanicalObjectSpec,
    )
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path)

    calls = {"n": 0}

    class _Spy:
        def search(self, *a, **k):
            calls["n"] += 1
            return []
    monkeypatch.setattr(pipe, "get_search_provider", lambda: _Spy())

    name = "Acme Widget Board 9000"
    # First: nothing cached, provider would be consulted.
    assert pipe.extract_object_spec(name) is None
    assert calls["n"] == 1
    # Seed the cache; a second call must NOT consult the provider again.
    cache.put(name, MechanicalObjectSpec(object_name=name, source_type=SOURCE_OFFICIAL,
                                         confidence_score=0.9))
    got = pipe.extract_object_spec(name)
    assert got is not None and got.object_name == name
    assert calls["n"] == 1   # served from cache, no extra search
