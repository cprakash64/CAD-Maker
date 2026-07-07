// Reusable selection model for Fusion-style face selection (Phase 1).
//
// Phase 1 selects a *mesh face patch* (a coplanar group of triangles under the
// cursor) rather than a true BRep face — the preview mesh carries no per-face
// CAD identity yet. The selection is still mapped, best-effort, to the stable
// bounding-box face id the backend already exposes (`face_top`, `face_+X`, …)
// so the existing circle-to-edit backend keeps working with zero API changes.
//
// See `frontend/src/lib/types.ts` `FeatureInfo` for the backend feature ids and
// [[drawing-to-cad-pipeline]] for the wider CAD flow.

import type { FaceBBox, FaceKind, FaceLocalFrame } from "./faceGrouping";

export type { FaceKind } from "./faceGrouping";

/** Raw hit emitted by the viewer's raycaster, in *world* coordinates. */
export interface FaceHit {
  /** Triangle index from the raycaster (`intersection.faceIndex`). */
  faceIndex: number | null;
  /** Exact clicked point on the surface. */
  point: [number, number, number];
  /** Face normal in world coordinates. */
  normal: [number, number, number];
  /** Centroid of the highlighted patch (used to anchor the floating popup). */
  center: [number, number, number];
  /** Coarse world-axis label of the clicked face ("top", "+X", …). */
  axisLabel: string;
  /** Number of triangles in the highlighted patch. */
  patchTriangleCount: number;
  /** Camera position at click time (view context for later phases). */
  cameraPosition: [number, number, number] | null;

  // --- Phase 2 (present when a visual-face group resolved) -----------------
  /** Stable id of the grouped visual face, or null if this was a fallback patch. */
  visualFaceId?: string | null;
  /** Classified surface kind. */
  faceKind?: FaceKind;
  /** All triangle indices in the visual face. */
  triangleIndices?: number[];
  /** Total face area (mm²). */
  area?: number | null;
  bbox?: FaceBBox | null;
  localFrame?: FaceLocalFrame | null;
  /** Backend semantic face this click matched (Phase 5), if any. */
  backendFace?: SelectableFace | null;

  // --- Phase 6: entity selection (edge/hole/body) --------------------------
  kind?: SelectionKind;
  hole?: SelectableHole | null;
  edge?: SelectableEdge | null;
  body?: SelectableBody | null;
}

/**
 * Wire value for what kind of geometry a selection carries. Faces resolve to a
 * mesh/visual/backend face; Phase 6 entity picks resolve to a backend hole /
 * edge / body. The backend degrades any unknown value to the mesh-face fallback.
 */
export type SelectionType =
  | "mesh_face"
  | "visual_face"
  | "backend_face"
  | "backend_hole"
  | "backend_edge"
  | "backend_body";

/** Stored selection object surfaced to the UI and (later) the backend. */
export interface MeshFaceSelection {
  /** "visual_face" when a grouped face resolved; "mesh_face" for a fallback patch;
   *  "backend_face" / "backend_hole" / "backend_edge" / "backend_body" for a
   *  backend-matched face or Phase 6 entity. */
  selection_type: SelectionType;
  /** Design / part id this selection belongs to. */
  object_id: string | null;
  /** Logical name of the picked object (the preview mesh). */
  object_name: string;
  /** Triangle index under the cursor (mesh-level, not a CAD face). */
  face_index: number | null;
  /** Triangles that make up the highlighted patch/face. */
  patch_triangle_count: number;
  point: [number, number, number];
  normal: [number, number, number];
  center: [number, number, number];
  axis_label: string;
  /** Best-effort mapping to a backend bbox face id (may be null). */
  feature_id: string | null;
  feature_label: string | null;
  camera_position: [number, number, number] | null;
  timestamp: number;

  // --- Phase 2 visual-face detail -----------------------------------------
  visual_face_id: string | null;
  face_kind: FaceKind;
  triangle_indices: number[] | null;
  area_mm2: number | null;
  bbox: FaceBBox | null;
  local_frame: FaceLocalFrame | null;

  // --- Phase 5 backend semantic match (null when unmatched / unavailable) --
  backend_face_id: string | null;
  backend_label: string | null;
  /** Backend-advised operations (chip filter). Null → allow all chips. */
  allowed_operations: string[] | null;
  backend_confidence: number | null;

  // --- Phase 6 entity selection -------------------------------------------
  /** What kind of geometry is selected. Defaults to "face". */
  selection_kind: SelectionKind;
  /** The entity id for non-face selections (hole_id / edge_id / body_id). */
  entity_id: string | null;
}

/**
 * Map a *world*-space face normal back to the backend's bounding-box face id.
 *
 * The viewer bakes `rotateX(-90°)` into the preview geometry, so
 * model `(x, y, z)` → world `(x, z, -y)`; inverting, world `(X, Y, Z)` →
 * model `(X, -Z, Y)`. We classify by the dominant model axis so the id lines up
 * with `extract_features()` on the backend (`face_top` = model +Z, etc.).
 */
export function modelFaceFromWorldNormal(
  n: [number, number, number]
): { id: string; label: string } {
  const mx = n[0];
  const my = -n[2];
  const mz = n[1];
  const ax = Math.abs(mx);
  const ay = Math.abs(my);
  const az = Math.abs(mz);
  if (az >= ax && az >= ay) {
    return mz >= 0
      ? { id: "face_top", label: "Top face" }
      : { id: "face_bottom", label: "Bottom face" };
  }
  if (ax >= ay && ax >= az) {
    return mx >= 0
      ? { id: "face_+X", label: "+X face" }
      : { id: "face_-X", label: "-X face" };
  }
  return my >= 0
    ? { id: "face_+Y", label: "+Y face" }
    : { id: "face_-Y", label: "-Y face" };
}

function _base(hit: FaceHit, objectId: string | null, objectName: string) {
  return {
    object_id: objectId,
    object_name: objectName,
    face_index: hit.faceIndex,
    patch_triangle_count: hit.patchTriangleCount,
    point: hit.point,
    normal: hit.normal,
    center: hit.center,
    axis_label: hit.axisLabel,
    camera_position: hit.cameraPosition,
    timestamp: Date.now(),
    visual_face_id: hit.visualFaceId ?? null,
    triangle_indices: hit.triangleIndices ?? null,
    bbox: hit.bbox ?? null,
    local_frame: hit.localFrame ?? null,
  };
}

/**
 * Build the stored selection object from a raw viewer hit. Dispatches on the
 * selected entity kind (hole / edge / body / face) so one object type serves
 * the panel, popup, and edit payload.
 */
export function buildMeshFaceSelection(
  hit: FaceHit,
  objectId: string | null,
  objectName = "preview"
): MeshFaceSelection {
  const base = _base(hit, objectId, objectName);

  if (hit.kind === "hole" && hit.hole) {
    const h = hit.hole;
    return {
      ...base,
      selection_type: "backend_hole",
      selection_kind: "hole",
      entity_id: h.hole_id,
      feature_id: h.feature_id,
      feature_label: `Ø${h.diameter_mm} mm hole`,
      face_kind: "cylindrical",
      area_mm2: null,
      backend_face_id: null,
      backend_label: `Ø${h.diameter_mm} mm hole`,
      allowed_operations: h.allowed_operations,
      backend_confidence: h.confidence,
    };
  }
  if (hit.kind === "edge" && hit.edge) {
    const eg = hit.edge;
    return {
      ...base,
      selection_type: "backend_edge",
      selection_kind: "edge",
      entity_id: eg.edge_id,
      feature_id: eg.feature_id,
      feature_label: `${eg.edge_kind} edge · ${eg.length_mm} mm`,
      face_kind: "unknown",
      area_mm2: null,
      backend_face_id: null,
      backend_label: `${eg.edge_kind} edge · ${eg.length_mm} mm`,
      allowed_operations: eg.allowed_operations,
      backend_confidence: eg.confidence,
    };
  }
  if (hit.kind === "body" && hit.body) {
    const b = hit.body;
    return {
      ...base,
      selection_type: "backend_body",
      selection_kind: "body",
      entity_id: b.body_id,
      feature_id: null,
      feature_label: b.label,
      face_kind: "unknown",
      area_mm2: null,
      backend_face_id: null,
      backend_label: b.label,
      allowed_operations: b.allowed_operations,
      backend_confidence: b.confidence,
    };
  }

  // Face (Phase 5 behavior).
  const axisFace = modelFaceFromWorldNormal(hit.normal);
  const backend = hit.backendFace ?? null;
  return {
    ...base,
    selection_type: backend
      ? "backend_face"
      : hit.visualFaceId
        ? "visual_face"
        : "mesh_face",
    selection_kind: "face",
    entity_id: backend?.face_id ?? null,
    feature_id: backend?.feature_id ?? axisFace.id,
    feature_label: backend?.label ?? axisFace.label,
    face_kind: (backend?.face_kind as FaceKind) ?? hit.faceKind ?? "unknown",
    area_mm2: backend?.area_mm2 ?? hit.area ?? null,
    backend_face_id: backend?.face_id ?? null,
    backend_label: backend?.label ?? null,
    allowed_operations: backend?.allowed_operations ?? null,
    backend_confidence: backend?.confidence ?? null,
  };
}

/** Human label for a face kind, e.g. "planar face". */
export function faceKindLabel(kind: FaceKind): string {
  switch (kind) {
    case "planar":
      return "planar face";
    case "cylindrical":
      return "cylindrical face";
    case "curved":
      return "curved face";
    default:
      return "mesh face";
  }
}

// --- Phase 6: selection modes + entity metadata -----------------------------

export type SelectionMode = "auto" | "face" | "edge" | "hole" | "body" | "measure";
export type SelectionKind = "face" | "edge" | "hole" | "body" | "feature";

export interface SelectableHole {
  hole_id: string;
  feature_id: string | null;
  diameter_mm: number;
  center: [number, number, number];
  axis: [number, number, number];
  through: boolean;
  hole_type?: string;
  allowed_operations: string[];
  confidence: number;
}

/** Screen-pixel proximity threshold for picking a hole by its projected center. */
export const HOLE_PICK_PX = 26;

/** One hole scored against a click, in the two ways a user reaches for a hole. */
export interface HolePickCandidate {
  hole: SelectableHole;
  /** Pixels from the click to the hole center projected to screen (the opening). */
  screenDist: number;
  /** World-mm from the clicked surface point to the hole axis (the wall). */
  axisDist: number;
}

export interface HolePickResult {
  hole: SelectableHole;
  /** True when the click landed ON the hole (opening or wall) — a strong signal
   *  that, in Auto mode, lets the hole win over an edge/face under the cursor. */
  onEntity: boolean;
}

/**
 * Choose the hole a click resolves to, if any. A click is "on" a hole when it
 * lands on the cylindrical wall (axis distance ≈ the hole radius) or squarely on
 * the projected opening; such hits are promoted ahead of edges/faces. Otherwise a
 * hole still matches if its projected center is within {@link HOLE_PICK_PX}.
 * Returns the best (on-entity first, then nearest center) or null.
 *
 * Pure so the picking rule is unit-testable without a live camera/raycaster;
 * Viewer3D computes each candidate's screen/axis distances and calls this.
 */
export function pickHole(
  candidates: HolePickCandidate[],
  wallTol: number
): HolePickResult | null {
  let best: HolePickResult | null = null;
  let bestScore = Infinity;
  for (const c of candidates) {
    const onWall = c.axisDist <= c.hole.diameter_mm / 2 + wallTol;
    const onEntity = onWall || c.screenDist <= HOLE_PICK_PX * 0.5;
    if (!onEntity && c.screenDist > HOLE_PICK_PX) continue;
    const score = (onEntity ? 0 : 1e4) + c.screenDist;
    if (score < bestScore) {
      bestScore = score;
      best = { hole: c.hole, onEntity };
    }
  }
  return best;
}

export interface SelectableEdge {
  edge_id: string;
  feature_id: string | null;
  edge_kind: string;
  start: [number, number, number];
  end: [number, number, number];
  length_mm: number;
  allowed_operations: string[];
  confidence: number;
}

export interface SelectableBody {
  body_id: string;
  label: string;
  volume_mm3: number;
  material?: string | null;
  allowed_operations: string[];
  confidence: number;
}

/**
 * Registry of every localized-edit action: maps a backend operation name to a
 * chip label + starter prompt. The popup renders whichever operations the
 * selected entity advertises (`allowed_operations`), so one popup serves every
 * selection kind. `viewerAction: true` marks actions handled in the viewer
 * (e.g. measure) rather than sent to the backend.
 */
export interface EditAction {
  key: string;
  label: string;
  prompt: string;
  viewerAction?: boolean;
}
const ACTION_LIBRARY: Record<string, Omit<EditAction, "key">> = {
  // face
  add_hole: { label: "Hole", prompt: "Add a 10 mm through hole centered on this face" },
  add_slot: { label: "Slot", prompt: "Cut a 20 mm × 6 mm slot centered on this face" },
  add_cutout: { label: "Cutout", prompt: "Cut a rectangular opening on this face" },
  add_boss: { label: "Boss", prompt: "Add a cylindrical boss on this face" },
  add_vent: { label: "Vent", prompt: "Add evenly spaced ventilation slots on this face" },
  fillet_edges: { label: "Fillet", prompt: "Fillet the selected face edges by 2 mm" },
  chamfer_edges: { label: "Chamfer", prompt: "Chamfer the selected face edges by 1 mm" },
  // edge
  fillet_edge: { label: "Fillet", prompt: "Fillet this edge by 2 mm" },
  chamfer_edge: { label: "Chamfer", prompt: "Chamfer this edge by 1 mm" },
  measure: { label: "Measure", prompt: "Measure this edge", viewerAction: true },
  // hole
  resize_hole: { label: "Resize", prompt: "Resize this hole to 8 mm" },
  move_hole: { label: "Move", prompt: "Move this hole 5 mm along X" },
  delete_hole: { label: "Delete", prompt: "Delete this hole" },
  pattern_hole: { label: "Pattern", prompt: "Pattern this hole into 4 evenly spaced holes" },
  // body
  rename: { label: "Rename", prompt: "Rename this body" },
  material: { label: "Material", prompt: "Change the material of this body" },
  export_body: { label: "Export", prompt: "Export this body" },
  duplicate: { label: "Duplicate", prompt: "Duplicate this body" },
  mirror: { label: "Mirror", prompt: "Mirror this body" },
  // feature
  edit_dimensions: { label: "Dimensions", prompt: "Edit the dimensions of this feature" },
  suppress: { label: "Suppress", prompt: "Suppress this feature" },
  pattern: { label: "Pattern", prompt: "Pattern this feature" },
};

/** Build editable chips from an entity's advertised operations. */
export function actionsForOperations(allowed: string[] | null | undefined): EditAction[] {
  if (!allowed || allowed.length === 0) return [];
  return allowed
    .map((op) => {
      const def = ACTION_LIBRARY[op];
      return def ? { key: op, ...def } : null;
    })
    .filter((a): a is EditAction => a !== null);
}

// --- Phase 5: backend semantic selectable faces ----------------------------

/** Backend-emitted CAD face metadata (CadQuery model frame, mm, Z-up). */
export interface SelectableFace {
  face_id: string;
  feature_id: string | null;
  body_id: string | null;
  face_kind: string;
  label: string;
  normal: [number, number, number];
  center: [number, number, number];
  area_mm2: number;
  bounds_mm?: { width: number; height: number; depth: number } | null;
  local_frame?: {
    origin: [number, number, number];
    x_axis: [number, number, number];
    y_axis: [number, number, number];
    z_axis: [number, number, number];
  } | null;
  allowed_operations: string[];
  confidence: number;
}

/** A backend face pre-transformed into the viewer's world frame for matching. */
export interface WorldSelectableFace {
  face: SelectableFace;
  worldNormal: [number, number, number];
  worldCenter: [number, number, number];
}

function dot3(a: [number, number, number], b: [number, number, number]): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

/**
 * Match a clicked visual face to the nearest backend selectable face.
 *
 * Candidates are already in the viewer world frame. We score by normal
 * alignment (primary — must agree in direction *and* sign, so a top face never
 * matches the parallel bottom face) plus center proximity (secondary, to
 * disambiguate coplanar faces). Returns null when nothing aligns well enough —
 * we never force a low-confidence match onto an unrelated face.
 */
export function matchSelectableFace(
  clickedNormal: [number, number, number],
  clickedCenter: [number, number, number],
  candidates: WorldSelectableFace[],
  scale: number
): SelectableFace | null {
  const NORMAL_MIN = 0.6; // ~53° — reject clearly different orientations
  const radius = scale > 1e-6 ? scale : 1;
  let best: { face: SelectableFace; score: number } | null = null;
  for (const c of candidates) {
    const nsim = dot3(clickedNormal, c.worldNormal);
    if (nsim < NORMAL_MIN) continue;
    const dx = clickedCenter[0] - c.worldCenter[0];
    const dy = clickedCenter[1] - c.worldCenter[1];
    const dz = clickedCenter[2] - c.worldCenter[2];
    const distNorm = Math.sqrt(dx * dx + dy * dy + dz * dz) / radius;
    // Normal agreement dominates; nearer centers break ties.
    const score = nsim - 0.35 * Math.min(distNorm, 2);
    if (!best || score > best.score) best = { face: c.face, score };
  }
  return best ? best.face : null;
}

/** Subtitle for the contextual popup, e.g. "Planar face selected". */
export function faceKindSubtitle(kind: FaceKind): string {
  switch (kind) {
    case "planar":
      return "Planar face selected";
    case "cylindrical":
      return "Cylindrical face selected";
    case "curved":
      return "Curved face selected";
    default:
      return "Mesh face selected";
  }
}

/** Quick-action chips for the contextual popup. Each fills the input with a
 *  useful starter prompt the user can edit before applying. */
export interface FaceQuickAction {
  key: string;
  label: string;
  prompt: string;
}
export const FACE_QUICK_ACTIONS: FaceQuickAction[] = [
  { key: "hole", label: "Hole", prompt: "Add a 10 mm through hole centered on this face" },
  { key: "slot", label: "Slot", prompt: "Cut a 20 mm × 6 mm slot centered on this face" },
  { key: "cutout", label: "Cutout", prompt: "Cut a rectangular opening on this face" },
  { key: "boss", label: "Boss", prompt: "Add a cylindrical boss on this face" },
  { key: "vent", label: "Vent", prompt: "Add evenly spaced ventilation slots on this face" },
  { key: "fillet", label: "Fillet", prompt: "Fillet the selected face edges by 2 mm" },
  { key: "chamfer", label: "Chamfer", prompt: "Chamfer the selected face edges by 1 mm" },
  { key: "pattern", label: "Pattern", prompt: "Create a symmetric pattern on this face" },
];

// Chip key -> backend allowed-operation name (kept in sync with the backend).
const CHIP_TO_OPERATION: Record<string, string> = {
  hole: "add_hole",
  slot: "add_slot",
  cutout: "add_cutout",
  boss: "add_boss",
  vent: "add_vent",
  fillet: "fillet_edges",
  chamfer: "chamfer_edges",
  pattern: "pattern",
};

/**
 * Filter the quick-action chips by the backend's advised operations. When the
 * backend gave no guidance (null/empty — older designs or unmatched clicks) we
 * show every chip and let the backend gate the edit.
 */
export function filterQuickActions(
  allowed: string[] | null | undefined
): FaceQuickAction[] {
  if (!allowed || allowed.length === 0) return FACE_QUICK_ACTIONS;
  const set = new Set(allowed);
  return FACE_QUICK_ACTIONS.filter((a) => {
    const op = CHIP_TO_OPERATION[a.key];
    return op !== undefined && set.has(op);
  });
}

// --- Localized face-edit payload (prepared for the future backend) ----------

export interface FaceEditSelectionPayload {
  selection_type: MeshFaceSelection["selection_type"];
  frontend_visual_face_id: string | null;
  /** Backend semantic CAD face id (Phase 5), when the click matched one. */
  backend_face_id: string | null;
  /** Entity ids for Phase 6 edge / hole / body selections. */
  edge_id: string | null;
  hole_id: string | null;
  body_id: string | null;
  /** Stable bbox-face id the normal maps to (face_top/face_+X/…). */
  feature_id: string | null;
  clicked_point: [number, number, number];
  normal: [number, number, number];
  center: [number, number, number];
  face_kind: FaceKind;
  bounds: FaceBBox | null;
  area: number | null;
  local_frame: FaceLocalFrame | null;
  triangle_indices: number[] | null;
}

export interface FaceEditPayload {
  design_id: string | null;
  selection: FaceEditSelectionPayload;
  instruction: string;
  /** Chip key ("hole", "slot", …) when a quick action seeded the text, else null. */
  quick_action: string | null;
}

/** Assemble the structured payload the popup prepares on "Apply edit". */
export function buildFaceEditPayload(
  sel: MeshFaceSelection,
  instruction: string,
  quickAction: string | null
): FaceEditPayload {
  return {
    design_id: sel.object_id,
    selection: {
      selection_type: sel.selection_type,
      frontend_visual_face_id: sel.visual_face_id,
      backend_face_id: sel.backend_face_id,
      edge_id: sel.selection_kind === "edge" ? sel.entity_id : null,
      hole_id: sel.selection_kind === "hole" ? sel.entity_id : null,
      body_id: sel.selection_kind === "body" ? sel.entity_id : null,
      feature_id: sel.feature_id,
      clicked_point: sel.point,
      normal: sel.normal,
      center: sel.center,
      face_kind: sel.face_kind,
      bounds: sel.bbox,
      area: sel.area_mm2,
      local_frame: sel.local_frame,
      // Never ship the (potentially large) triangle index list to the backend —
      // it isn't used server-side and only bloats the request.
      triangle_indices: null,
    },
    instruction,
    quick_action: quickAction,
  };
}

/** Format an area estimate in mm² (or cm² when large). */
export function fmtArea(mm2: number | null | undefined): string | null {
  if (mm2 == null || !isFinite(mm2)) return null;
  if (mm2 >= 10000) return `${(mm2 / 100).toFixed(1)} cm²`;
  return `${mm2.toFixed(mm2 < 10 ? 1 : 0)} mm²`;
}

/** Compact "x, y, z" for display (1-dp mm). */
export function fmtVec(v: [number, number, number]): string {
  return `${v[0].toFixed(1)}, ${v[1].toFixed(1)}, ${v[2].toFixed(1)}`;
}
