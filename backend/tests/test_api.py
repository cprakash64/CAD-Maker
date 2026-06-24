"""End-to-end API: create -> get -> regenerate -> export -> checks (authenticated)."""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_and_full_flow(client, auth):
    h = auth["headers"]
    r = client.post(
        "/api/designs/create",
        json={"prompt": "wall bracket, two M6 holes, 5mm thick, 80mm wide"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    design = r.json()
    assert design["object_type"] == "rectangular_bracket"
    assert design["needs_clarification"] is False
    assert design["preview"]["triangle_count"] > 0
    assert len(design["exports"]) == 2
    assert all(e["size_bytes"] > 0 for e in design["exports"])
    assert any(c["check"] == "min_thickness" for c in design["checks"])
    did = design["id"]

    # File is downloadable through the owner-checked route and non-empty.
    fr = client.get(f"/api/designs/{did}/files/stl", headers=h)
    assert fr.status_code == 200
    assert len(fr.content) > 0

    # Regenerate with a wider plate -> different geometry.
    params = dict(design["editable_parameters"])
    old_hash = design["spec_hash"]
    params["width"] = params["width"] + 40
    r2 = client.post(
        f"/api/designs/{did}/regenerate", json={"dimensions": params}, headers=h
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["spec_hash"] != old_hash

    # Re-run checks endpoint.
    r3 = client.post(f"/api/designs/{did}/checks", headers=h)
    assert r3.status_code == 200
    assert len(r3.json()) > 0

    # Plain-English explanation is present.
    assert design["explanation"] and "bracket" in design["explanation"].lower()


def test_modify_endpoint_changes_geometry_and_clarifies(client, auth):
    h = auth["headers"]
    r = client.post(
        "/api/designs/create",
        json={"prompt": "bracket 80mm wide 40mm deep 5mm thick with two M6 holes"},
        headers=h,
    )
    did = r.json()["id"]
    before = r.json()["spec_hash"]

    m = client.post(
        f"/api/designs/{did}/modify", json={"prompt": "make it 120mm wide"}, headers=h
    )
    assert m.status_code == 200, m.text
    body = m.json()
    assert body["spec_hash"] != before
    assert body["editable_parameters"]["width"] == 120

    m2 = client.post(
        f"/api/designs/{did}/modify", json={"prompt": "make it fly"}, headers=h
    )
    assert m2.status_code == 200
    assert m2.json()["clarification_question"]
    assert m2.json()["spec_hash"] == body["spec_hash"]


def test_clarification_flow(client, auth):
    # v0.3.8 generate-first: clarify only when generation is impossible. An
    # enclosure whose walls are thicker than the cavity can't be built.
    r = client.post(
        "/api/designs/create",
        json={"prompt": "enclosure 30mm wide 30mm deep 20mm tall with 20mm walls"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    design = r.json()
    assert design["needs_clarification"] is True
    assert design["clarification_question"]
    assert design["preview"] is None


def test_generate_first_pipe_clamp_without_diameter(client, auth):
    # The same prompt that used to clarify now generates with a default diameter.
    # v0.7: clamp prompts build a real split tube clamp through the feature
    # graph (assumed pipe Ø) instead of the legacy pipe_clamp template.
    r = client.post(
        "/api/designs/create",
        json={"prompt": "I want a pipe clamp, 6mm thick"},
        headers=auth["headers"],
    )
    assert r.status_code == 200, r.text
    design = r.json()
    assert design["needs_clarification"] is False
    assert design["object_type"] == "tube_clamp_block"
    assert design["route"] == "cad_plan"
    assert len(design["exports"]) == 2


def test_invalid_regenerate_returns_422(client, auth):
    h = auth["headers"]
    r = client.post(
        "/api/designs/create",
        json={"prompt": "spacer 12mm diameter 20mm long M6 bore"},
        headers=h,
    )
    did = r.json()["id"]
    r2 = client.post(
        f"/api/designs/{did}/regenerate",
        json={"dimensions": {"outer_diameter": 5, "length": 20, "bore_diameter": 8}},
        headers=h,
    )
    assert r2.status_code == 422


def test_list_and_templates(client, auth):
    client.post(
        "/api/designs/create", json={"prompt": "L bracket 60mm"}, headers=auth["headers"]
    )
    r = client.get("/api/designs", headers=auth["headers"])
    assert r.status_code == 200
    assert len(r.json()) >= 1
    # Templates are public catalog data (no auth needed).
    t = client.get("/api/templates")
    assert t.status_code == 200
    assert len(t.json()) == 12  # 8 base + crankshaft + flanged_pipe_branch + gear/pulley + hex_standoff
