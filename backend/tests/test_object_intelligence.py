"""Object Intelligence layer — known objects build accurate CAD with honest trust.

Covers the product rule: GPT-estimated / unknown dimensions can never PASS, known
local presets/standards build deterministically (no LLM/web), and a known object is
never silently turned into a generic box.
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


def _oi(d: dict) -> dict:
    return d.get("object_intelligence") or {}


def _detail(d: dict) -> dict:
    return d.get("part_family_detail") or {}


# === 1 / 2: Raspberry Pi enclosures use the preset + through-hole ports ======
def test_rpi4_uses_preset_and_through_holes(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a case for Raspberry Pi 4")
    assert d["object_type"] == "rpi4_enclosure"
    v = d["device_enclosure_validation"]
    assert v["all_required_ports_open"] is True and v["blocked_ports"] == []
    assert _oi(d)["source_type"] == "local_verified"
    assert d["validation_status"] != "pass"   # approximate connectors -> REVIEW


def test_rpi5_uses_preset_and_through_holes(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a case for Raspberry Pi 5")
    assert d["object_type"] == "rpi5_enclosure"
    assert d["device_enclosure_validation"]["all_required_ports_open"] is True


# === 3 / 4: Arduino + ESP32 are not generic boxes ===========================
def test_arduino_uno_not_generic_box(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make an enclosure for Arduino Uno")
    assert d["object_type"] == "board_enclosure"
    oi = _oi(d)
    assert "arduino" in oi["object_detected"].lower()
    assert oi["source_type"] == "local_verified"
    v = d["device_enclosure_validation"]
    assert v["mounting_posts_count"] == 4
    assert len(v["port_openings"]) >= 1 and v["all_required_ports_open"] is True
    assert d["validation_status"] != "critical_failure"


def test_esp32_devkit_not_generic_box(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a case for ESP32 DevKit V1")
    assert d["object_type"] == "board_enclosure"
    assert "esp32" in _oi(d)["object_detected"].lower()
    assert d["device_enclosure_validation"]["all_required_ports_open"] is True
    # exact variant uncertain -> REVIEW, never a clean PASS
    assert d["validation_status"] != "pass"


# === 5: NEMA 17 motor mount four-hole pattern ===============================
def test_nema17_motor_mount_pattern(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a mount for a NEMA 17 stepper motor")
    assert d["object_type"] == "motor_mount"
    det = _detail(d)
    assert det["hole_pattern"]["count"] == 4
    assert det["hole_pattern"]["spacing_mm"] == 31.0
    assert _oi(d)["source_type"] == "local_verified"
    # bbox is a flat plate covering the 31mm pattern
    bb = d["bounding_box_mm"]
    assert bb["x"] >= 31 and bb["z"] <= 12


# === 6: 608 bearing holder uses correct dims ================================
def test_608_bearing_holder_dimensions(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a holder for a 608 bearing")
    assert d["object_type"] == "bearing_holder"
    det = _detail(d)
    assert det["dimensions"]["outer_mm"] == 22.0
    assert det["dimensions"]["bore_mm"] == 8.0
    assert det["dimensions"]["width_mm"] == 7.0
    # body is bigger than the bearing OD (wall around the seat)
    bb = d["bounding_box_mm"]
    assert max(bb["x"], bb["y"]) > 22.0


# === 7: unknown object with no source does not PASS =========================
def test_unknown_named_object_does_not_pass(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth, "Make a mount for a Waveshare 7 inch display")
    # No verified spec -> clarification, never a PASS / fake geometry.
    assert d["validation_status"] != "pass"
    assert _oi(d).get("source_type") == "unknown"
    assert d.get("clarification_question")


# === 8: GPT-estimated dimensions can never PASS (policy) ====================
def test_gpt_estimated_never_pass():
    from app.cad.object_intelligence import can_pass
    from app.cad.object_intelligence.mechanical_spec import (
        SOURCE_GPT,
        SOURCE_LOCAL_VERIFIED,
        SOURCE_UNKNOWN,
        SOURCE_USER,
    )

    assert can_pass(SOURCE_GPT) is False
    assert can_pass(SOURCE_UNKNOWN) is False
    assert can_pass(SOURCE_LOCAL_VERIFIED) is True
    assert can_pass(SOURCE_USER) is True


# === 9: cached spec avoids repeat web/LLM calls ============================
def test_cached_spec_no_repeat_lookup(monkeypatch, tmp_path):
    """A cached source-backed spec is served WITHOUT calling the search provider."""
    import app.cad.object_intelligence.cache as cache
    import app.cad.object_intelligence.extraction_pipeline as pipe
    import app.cad.object_intelligence.resolver as resolver
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

    prompt = "Make a case for the Frobnitz 3000 widget board"
    # The resolver looks the spec up by the detected device name, cache-first.
    cache.put("Frobnitz 3000 widget board", MechanicalObjectSpec(
        object_name="Frobnitz 3000", source_type=SOURCE_OFFICIAL, confidence_score=0.9,
        board_outline={"length_mm": 70, "width_mm": 40, "height_mm": 18}))
    res = resolver.resolve_object(prompt)
    assert res is not None and res.spec.object_name == "Frobnitz 3000"
    assert calls["n"] == 0   # served from cache, no provider search


# === 10: user-provided dimensions can PASS; generic fallback is honest ======
def test_user_pcb_box_can_pass(client, auth, monkeypatch):
    _no_llm(monkeypatch)
    d = _create(client, auth,
                "Make a box for my custom PCB 80 mm by 50 mm with four 3 mm mounting holes")
    assert d["object_type"] == "generic_fitted_box"
    assert _oi(d)["source_type"] == "user_provided"
    bb = d["bounding_box_mm"]
    assert bb["x"] >= 80 and bb["y"] >= 50
    assert d["validation_status"] in ("pass", "warning")
