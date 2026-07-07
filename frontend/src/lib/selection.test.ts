/** Phase 1 face-selection model: world-normal → backend face id mapping and
 *  selection-object construction. Pure logic, no three.js/DOM needed. */
import { describe, expect, it } from "vitest";
import {
  actionsForOperations,
  buildFaceEditPayload,
  buildMeshFaceSelection,
  FACE_QUICK_ACTIONS,
  faceKindSubtitle,
  filterQuickActions,
  fmtVec,
  HOLE_PICK_PX,
  matchSelectableFace,
  modelFaceFromWorldNormal,
  pickHole,
  type FaceHit,
  type HolePickCandidate,
  type SelectableFace,
  type SelectableHole,
  type WorldSelectableFace,
} from "./selection";

describe("modelFaceFromWorldNormal", () => {
  // Viewer bakes rotateX(-90°): world +Y is the model top (+Z).
  it("maps world +Y to the top face", () => {
    expect(modelFaceFromWorldNormal([0, 1, 0]).id).toBe("face_top");
  });
  it("maps world -Y to the bottom face", () => {
    expect(modelFaceFromWorldNormal([0, -1, 0]).id).toBe("face_bottom");
  });
  it("maps world ±X to the ±X faces", () => {
    expect(modelFaceFromWorldNormal([1, 0, 0]).id).toBe("face_+X");
    expect(modelFaceFromWorldNormal([-1, 0, 0]).id).toBe("face_-X");
  });
  it("maps world Z (model ∓Y) to the ∓Y faces", () => {
    // world +Z -> model -Y ; world -Z -> model +Y
    expect(modelFaceFromWorldNormal([0, 0, 1]).id).toBe("face_-Y");
    expect(modelFaceFromWorldNormal([0, 0, -1]).id).toBe("face_+Y");
  });
  it("classifies by the dominant axis for oblique normals", () => {
    expect(modelFaceFromWorldNormal([0.9, 0.2, 0.1]).id).toBe("face_+X");
  });
});

describe("buildMeshFaceSelection", () => {
  const hit: FaceHit = {
    faceIndex: 12,
    point: [1, 2, 3],
    normal: [0, 1, 0],
    center: [0, 5, 0],
    axisLabel: "top",
    patchTriangleCount: 8,
    cameraPosition: [120, 90, 120],
  };

  it("carries the raw hit data and the mapped feature id", () => {
    const sel = buildMeshFaceSelection(hit, "design-42");
    expect(sel.selection_type).toBe("mesh_face");
    expect(sel.object_id).toBe("design-42");
    expect(sel.face_index).toBe(12);
    expect(sel.patch_triangle_count).toBe(8);
    expect(sel.feature_id).toBe("face_top");
    expect(sel.feature_label).toBe("Top face");
    expect(sel.point).toEqual([1, 2, 3]);
    expect(typeof sel.timestamp).toBe("number");
  });

  it("tolerates a missing object id", () => {
    expect(buildMeshFaceSelection(hit, null).object_id).toBeNull();
  });
});

describe("fmtVec", () => {
  it("formats to one decimal place", () => {
    expect(fmtVec([1.234, -5, 0])).toBe("1.2, -5.0, 0.0");
  });
});

describe("Phase 3 popup helpers", () => {
  const hit: FaceHit = {
    faceIndex: 3,
    point: [1, 2, 3],
    normal: [0, 1, 0],
    center: [0, 5, 0],
    axisLabel: "top",
    patchTriangleCount: 40,
    cameraPosition: [10, 10, 10],
    visualFaceId: "vf_2",
    faceKind: "planar",
    triangleIndices: [3, 4, 5, 6],
    area: 250,
    bbox: { min: [-5, 5, -5], max: [5, 5, 5] },
    localFrame: { origin: [0, 5, 0], normal: [0, 1, 0], tangent: [1, 0, 0], bitangent: [0, 0, 1] },
  };

  it("subtitles each face kind", () => {
    expect(faceKindSubtitle("planar")).toBe("Planar face selected");
    expect(faceKindSubtitle("cylindrical")).toBe("Cylindrical face selected");
    expect(faceKindSubtitle("curved")).toBe("Curved face selected");
    expect(faceKindSubtitle("unknown")).toBe("Mesh face selected");
  });

  it("exposes all eight quick actions with starter prompts", () => {
    expect(FACE_QUICK_ACTIONS.map((a) => a.key)).toEqual([
      "hole",
      "slot",
      "cutout",
      "boss",
      "vent",
      "fillet",
      "chamfer",
      "pattern",
    ]);
    expect(FACE_QUICK_ACTIONS[0]!.prompt).toBe(
      "Add a 10 mm through hole centered on this face"
    );
  });

  it("builds the localized face-edit payload from a selection", () => {
    const sel = buildMeshFaceSelection(hit, "design-7");
    const payload = buildFaceEditPayload(sel, "Add a 10 mm through hole", "hole");
    expect(payload.design_id).toBe("design-7");
    expect(payload.instruction).toBe("Add a 10 mm through hole");
    expect(payload.quick_action).toBe("hole");
    expect(payload.selection).toMatchObject({
      selection_type: "visual_face",
      frontend_visual_face_id: "vf_2",
      feature_id: "face_top",
      clicked_point: [1, 2, 3],
      normal: [0, 1, 0],
      center: [0, 5, 0],
      face_kind: "planar",
      area: 250,
      triangle_indices: null, // slimmed out of the request (Phase 7)
    });
    expect(payload.selection.bounds).toEqual({ min: [-5, 5, -5], max: [5, 5, 5] });
  });

  it("defaults quick_action to null for free-typed edits", () => {
    const sel = buildMeshFaceSelection(hit, null);
    expect(buildFaceEditPayload(sel, "make it thinner", null).quick_action).toBeNull();
  });

  it("never ships triangle_indices to the backend (payload slimming)", () => {
    const sel = buildMeshFaceSelection(
      { ...hit, triangleIndices: Array.from({ length: 4000 }, (_, i) => i) },
      "d1"
    );
    expect(sel.triangle_indices).not.toBeNull(); // kept locally
    expect(buildFaceEditPayload(sel, "add a hole here", "hole").selection.triangle_indices).toBeNull();
  });
});

describe("Phase 5 backend selectable-face matching", () => {
  function face(over: Partial<SelectableFace>): SelectableFace {
    return {
      face_id: "f0",
      feature_id: null,
      body_id: "main_body",
      face_kind: "planar",
      label: "Face",
      normal: [0, 0, 1],
      center: [0, 0, 0],
      area_mm2: 100,
      allowed_operations: ["add_hole"],
      confidence: 0.9,
      ...over,
    };
  }
  function world(f: SelectableFace, wn: [number, number, number], wc: [number, number, number]): WorldSelectableFace {
    return { face: f, worldNormal: wn, worldCenter: wc };
  }

  it("matches by normal direction incl. sign (top not bottom)", () => {
    const top = face({ face_id: "top", feature_id: "face_top", label: "Top" });
    const bottom = face({ face_id: "bottom", feature_id: "face_bottom", label: "Bottom" });
    const cands = [
      world(top, [0, 1, 0], [0, 5, 0]),
      world(bottom, [0, -1, 0], [0, -5, 0]),
    ];
    const m = matchSelectableFace([0, 1, 0], [0, 5, 0], cands, 10);
    expect(m?.face_id).toBe("top");
  });

  it("uses center distance to disambiguate coplanar faces", () => {
    const near = face({ face_id: "near" });
    const far = face({ face_id: "far" });
    const cands = [
      world(near, [0, 1, 0], [0, 5, 1]),
      world(far, [0, 1, 0], [0, 5, 40]),
    ];
    const m = matchSelectableFace([0, 1, 0], [0, 5, 0], cands, 20);
    expect(m?.face_id).toBe("near");
  });

  it("returns null when nothing aligns (never forces a match)", () => {
    const side = face({ face_id: "side" });
    const cands = [world(side, [1, 0, 0], [5, 0, 0])];
    expect(matchSelectableFace([0, 1, 0], [0, 5, 0], cands, 10)).toBeNull();
  });

  it("carries backend feature id + label + allowed ops into the selection", () => {
    const top = face({ feature_id: "face_top", label: "Top face of bracket", allowed_operations: ["add_hole", "fillet_edges"] });
    const sel = buildMeshFaceSelection({ ...hitBase, backendFace: top }, "d1");
    expect(sel.backend_face_id).toBe(top.face_id);
    expect(sel.feature_id).toBe("face_top");
    expect(sel.backend_label).toBe("Top face of bracket");
    expect(sel.allowed_operations).toEqual(["add_hole", "fillet_edges"]);
  });
});

describe("Phase 6 entity selection", () => {
  const entityHit = (over: Partial<FaceHit>): FaceHit => ({
    faceIndex: null,
    point: [1, 2, 3],
    normal: [0, 0, 1],
    center: [1, 2, 3],
    axisLabel: "hole",
    patchTriangleCount: 0,
    cameraPosition: null,
    ...over,
  });

  it("builds a hole selection with entity id + advertised ops", () => {
    const sel = buildMeshFaceSelection(
      entityHit({
        kind: "hole",
        hole: {
          hole_id: "hole_1",
          feature_id: "holes",
          diameter_mm: 6,
          center: [1, 2, 3],
          axis: [0, 0, 1],
          through: true,
          allowed_operations: ["resize_hole", "delete_hole"],
          confidence: 0.9,
        },
      }),
      "d1"
    );
    expect(sel.selection_kind).toBe("hole");
    expect(sel.selection_type).toBe("backend_hole");
    expect(sel.entity_id).toBe("hole_1");
    expect(sel.allowed_operations).toEqual(["resize_hole", "delete_hole"]);
    const payload = buildFaceEditPayload(sel, "Resize this hole to 8 mm", "resize_hole");
    expect(payload.selection.selection_type).toBe("backend_hole");
    expect(payload.selection.hole_id).toBe("hole_1");
    expect(payload.selection.edge_id).toBeNull();
  });

  it("builds an edge selection routed to backend_edge", () => {
    const sel = buildMeshFaceSelection(
      entityHit({
        kind: "edge",
        edge: {
          edge_id: "edge_x",
          feature_id: null,
          edge_kind: "linear",
          start: [0, 0, 0],
          end: [10, 0, 0],
          length_mm: 10,
          allowed_operations: ["fillet_edge", "chamfer_edge", "measure"],
          confidence: 0.75,
        },
      }),
      "d1"
    );
    expect(sel.selection_kind).toBe("edge");
    const payload = buildFaceEditPayload(sel, "Fillet this edge by 2 mm", "fillet_edge");
    expect(payload.selection.edge_id).toBe("edge_x");
    expect(payload.selection.selection_type).toBe("backend_edge");
  });

  it("maps allowed operations to editable chips", () => {
    const chips = actionsForOperations(["resize_hole", "delete_hole", "unknown_op"]);
    expect(chips.map((c) => c.key)).toEqual(["resize_hole", "delete_hole"]);
    expect(chips[0]!.label).toBe("Resize");
    expect(actionsForOperations(null)).toEqual([]);
  });
});

describe("pickHole — cylindrical/opening click → hole promotion", () => {
  const hole = (over: Partial<SelectableHole> = {}): SelectableHole => ({
    hole_id: "hole_2",
    feature_id: "holes",
    diameter_mm: 10,
    center: [0, 0, 6],
    axis: [0, 0, 1],
    through: true,
    allowed_operations: ["resize_hole", "move_hole", "pattern_hole", "delete_hole"],
    confidence: 0.9,
    ...over,
  });
  const cand = (h: SelectableHole, screenDist: number, axisDist: number): HolePickCandidate => ({
    hole: h,
    screenDist,
    axisDist,
  });

  it("promotes a click on the cylindrical wall (axisDist ≈ radius) to the hole", () => {
    const h = hole(); // radius 5 mm
    // Far from the projected center in screen space, but right on the 5 mm wall.
    const res = pickHole([cand(h, 200, 5.0)], 1.0);
    expect(res).not.toBeNull();
    expect(res!.hole.hole_id).toBe("hole_2");
    expect(res!.onEntity).toBe(true); // strong signal → wins over edge/face in Auto
  });

  it("matches a click squarely on the opening (small screen distance)", () => {
    const res = pickHole([cand(hole(), 4, 999)], 1.0);
    expect(res).not.toBeNull();
    expect(res!.onEntity).toBe(true);
  });

  it("still matches near the projected center but not as an on-entity hit", () => {
    // Between HOLE_PICK_PX*0.5 and HOLE_PICK_PX, off the wall → weak match.
    const res = pickHole([cand(hole(), HOLE_PICK_PX - 2, 999)], 1.0);
    expect(res).not.toBeNull();
    expect(res!.onEntity).toBe(false);
  });

  it("returns null when the click is far from every hole (wall + screen miss)", () => {
    expect(pickHole([cand(hole(), 200, 999)], 1.0)).toBeNull();
  });

  it("prefers an on-entity hole over a merely-near one", () => {
    const near = hole({ hole_id: "hole_near" });
    const onWall = hole({ hole_id: "hole_wall" });
    const res = pickHole(
      [cand(near, HOLE_PICK_PX - 1, 999), cand(onWall, 180, 5.0)],
      1.0
    );
    expect(res!.hole.hole_id).toBe("hole_wall");
    expect(res!.onEntity).toBe(true);
  });
});

describe("hole selection surfaces only supported hole actions", () => {
  it("renders Resize and Delete only (never Move or Pattern)", () => {
    // Even if the backend advertised more, the capability matrix trims to the
    // operations that actually work end-to-end.
    const chips = actionsForOperations([
      "resize_hole",
      "move_hole",
      "pattern_hole",
      "delete_hole",
    ]);
    expect(chips.map((c) => c.label)).toEqual(["Resize", "Delete"]);
    expect(chips.map((c) => c.key)).not.toContain("move_hole");
    expect(chips.map((c) => c.key)).not.toContain("pattern_hole");
    // Never the face-only chips.
    expect(chips.map((c) => c.label)).not.toContain("Hole");
    expect(chips.map((c) => c.label)).not.toContain("Slot");
    expect(chips.map((c) => c.label)).not.toContain("Boss");
  });

  it("supported backend ops become their chips", () => {
    expect(actionsForOperations(["resize_hole", "delete_hole"]).map((c) => c.label)).toEqual([
      "Resize",
      "Delete",
    ]);
  });

  it("chips carry real backend op ids and produce the correct face-edit payload", () => {
    const sel = buildMeshFaceSelection(
      {
        faceIndex: null,
        point: [0, 0, 6],
        normal: [0, 0, 1],
        center: [0, 0, 6],
        axisLabel: "hole",
        patchTriangleCount: 0,
        cameraPosition: null,
        kind: "hole",
        hole: {
          hole_id: "hole_2",
          feature_id: "holes",
          diameter_mm: 10,
          center: [0, 0, 6],
          axis: [0, 0, 1],
          through: true,
          allowed_operations: ["resize_hole", "delete_hole"],
          confidence: 0.9,
        },
      },
      "d1"
    );
    const chips = actionsForOperations(sel.allowed_operations);
    expect(chips.map((c) => c.key)).toEqual(["resize_hole", "delete_hole"]);

    const resize = chips.find((c) => c.label === "Resize")!;
    const payload = buildFaceEditPayload(sel, resize.prompt, resize.key);
    // Resize chip → real op id + hole_id (never a vague "hole"/"move"/"pattern").
    expect(payload.quick_action).toBe("resize_hole");
    expect(payload.selection.selection_type).toBe("backend_hole");
    expect(payload.selection.hole_id).toBe("hole_2");

    const del = chips.find((c) => c.label === "Delete")!;
    expect(buildFaceEditPayload(sel, del.prompt, del.key).quick_action).toBe("delete_hole");
  });
});

describe("filterQuickActions", () => {
  it("shows only supported chips when the backend gave no guidance", () => {
    // null → every SUPPORTED face chip (Hole/Fillet/Chamfer); never Slot/Cutout/
    // Boss/Vent/Pattern (unimplemented).
    const chips = filterQuickActions(null);
    expect(chips.map((c) => c.key).sort()).toEqual(["chamfer", "fillet", "hole"]);
    expect(chips.map((c) => c.key)).not.toContain("pattern");
    expect(chips.map((c) => c.key)).not.toContain("slot");
  });
  it("returns no chips when the backend advertises an empty op list (e.g. cylindrical face)", () => {
    expect(filterQuickActions([])).toEqual([]);
  });
  it("filters chips to the advised operations", () => {
    const chips = filterQuickActions(["add_hole", "fillet_edges", "chamfer_edges"]);
    expect(chips.map((c) => c.key).sort()).toEqual(["chamfer", "fillet", "hole"]);
  });
  it("drops an advised-but-unimplemented op (pattern) from a cylindrical-ish list", () => {
    // A stale/legacy allowed list that still names pattern must not surface it.
    expect(filterQuickActions(["pattern"])).toEqual([]);
  });
});

const hitBase: FaceHit = {
  faceIndex: 1,
  point: [0, 0, 1],
  normal: [0, 1, 0],
  center: [0, 1, 0],
  axisLabel: "top",
  patchTriangleCount: 4,
  cameraPosition: [5, 5, 5],
  faceKind: "planar",
};
