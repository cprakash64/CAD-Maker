# Selection & Localized Editing

Fusion 360 / AutoCAD-style click-to-select and localized editing of generated CAD
parts. A user clicks a face / edge / hole / body in the 3D viewer, a contextual
popup offers relevant actions, and applying an edit regenerates **real CAD**
(STL/STEP) through the trusted parametric pipeline — never a mesh-only hack.

This document covers the system across the frontend viewer and the FastAPI/
CadQuery backend. It reflects Phases 1–7.

---

## Architecture

```
┌──────────────────────────── Frontend (Next.js / R3F) ────────────────────────────┐
│  Studio3D  ── selection mode control (Auto/Face/Edge/Hole/Body) + toasts          │
│    └─ Viewer3D (react-three-fiber)                                                │
│         • builds BufferGeometry from design.preview (positions/indices)           │
│         • caches visual-face grouping (faceGrouping.ts) per mesh                   │
│         • raycasts clicks; matches to backend selectable_* metadata               │
│         • hover highlight + selected highlight + drei <Html> popup                 │
│    └─ FaceEditPopup ── type-aware chips from allowed_operations                    │
│    └─ CircleEditPanel (right panel) ── mirrors the selection                       │
│  lib/selection.ts ── pure selection model, matching, payload builders             │
└───────────────────────────────────────────────────────────────────────────────────┘
                                   │  POST /api/designs/{id}/face-edit
                                   ▼
┌──────────────────────────── Backend (FastAPI / CadQuery) ─────────────────────────┐
│  routers/designs.py            ── face-edit endpoint + edit-history recorder       │
│  editing/face_edit.py          ── classify → deterministic handlers → new spec     │
│  services/design_service.py    ── apply_spec_edit (regenerate + validate + guard)  │
│  export/exporter.py            ── generate(): STL/STEP/preview + selectable_* meta │
│  cad/selectable_faces.py       ── BRep inspection → faces/edges/holes/bodies/feats │
└───────────────────────────────────────────────────────────────────────────────────┘
```

Key principle: **the LLM never emits geometry.** Instructions are only classified
by keywords + number extraction; edits mutate a validated `DesignSpec`, and the
trusted templates rebuild the solid.

---

## Frontend selection flow

1. **Mode** — `Studio3D` holds a `SelectionMode` (`auto | face | edge | hole |
   body`; Measure is a separate tool). Auto is the default.
2. **Pick** — On a mesh click, `Viewer3D`:
   - Applies a drag guard (pointer travel > 5px ⇒ orbit, ignored).
   - **Entity picking** (`pickEntity`): projects backend `selectable_holes` /
     `selectable_edges` anchors to screen and selects the nearest within a pixel
     threshold. Auto precedence: **edge → hole → face**.
   - **Face picking**: groups the clicked triangle into a coplanar/curved
     "visual face" (`faceGrouping.ts`), then matches it to a backend
     `selectable_faces` entry (`matchSelectableFace`, normal + center scored).
3. **Highlight** — In-scene overlays that track the camera: a translucent face
   patch, a hole marker, or an edge line. A quiet hover preview is shown for
   faces (Auto/Face modes) while moving the cursor (skipped while dragging).
4. **Selection object** — `buildMeshFaceSelection` produces one
   `MeshFaceSelection` for every kind, carrying `selection_kind`, `entity_id`,
   backend `feature_id`/`label`, and `allowed_operations`.
5. **Popup** — `FaceEditPopup` (drei `<Html>` anchored to the entity) renders
   chips from `allowed_operations`, a free-text input, and Apply/Cancel. Escape
   closes it; it closes on empty-space click, part change, or Cancel.
6. **Apply** — `buildFaceEditPayload` assembles the request (triangle indices are
   **not** sent), and the page calls `api.faceEdit`. Success refreshes the viewer
   with the new geometry; failures show the backend reason via a toast, leaving
   the model unchanged.

### Coordinate frames
The viewer bakes `rotateX(-90°)` + centering into the mesh. Backend metadata is
in the CadQuery model frame; the frontend converts with the shared
`transformAnchor` (center) and `(x, z, -y)` (normal) so faces, holes, and edges
line up with clicks.

---

## Backend metadata flow

On every `generate(spec)`, `cad/selectable_faces.py` inspects the solid and emits
(all advisory, all failure-tolerant — any error yields `[]`, never breaking a
build):

- `selectable_faces` — BRep faces: kind (planar/cylindrical/curved), normal,
  center, area, bounds, local frame, `feature_id` (`face_top`/`face_+X`/…),
  `allowed_operations`, confidence.
- `selectable_edges` — longest edges: kind, start/end, length.
- `selectable_holes` — from the spec's parametric holes: `hole_id`, diameter,
  center, axis.
- `selectable_bodies` — one per solid: volume, bounds, material.
- `selectable_features` — recognized template features (holes, flanges, bosses…).

These are stored in `semantic_json` and surfaced on `DesignDTO`. Older designs
without them simply return empty lists; the frontend falls back to mesh-only
visual grouping.

### Edit request → regenerate
`POST /api/designs/{id}/face-edit` (`FaceLocalizedEditRequest`):
1. Load the design + `DesignSpec`.
2. `classify_edit(quick_action, instruction)` → a constrained operation.
3. A deterministic handler validates inputs and produces a new `DesignSpec`
   (mm-normalized).
4. `apply_spec_edit(..., guard_critical=True)` regenerates geometry, runs the
   existing manufacturability + dimension-report validation, and **rolls back if
   the edit would turn a previously-valid design critical** (the original is
   preserved).
5. A compact history entry is appended to
   `semantic_json["localized_edits"]` (last 25; no triangle indices / local
   frames).

`selection_type` accepts `backend_face | backend_edge | backend_hole |
backend_body | backend_feature | visual_face`; unknown values degrade to
`visual_face` rather than 4xx.

---

## Supported operations (deterministic, real CAD)

| Operation        | Trigger                    | Applies to                         |
|------------------|----------------------------|------------------------------------|
| `add_hole`       | Face · Hole chip           | plate types, top/bottom planar face |
| `add_vent`       | Face · Vent chip           | enclosures                         |
| `fillet` / `fillet_edge`   | Face/Edge · Fillet | edge-treatment templates           |
| `chamfer` / `chamfer_edge` | Face/Edge · Chamfer| edge-treatment templates           |
| `resize_hole`    | Hole · Resize              | parametric holes                   |
| `delete_hole`    | Hole · Delete              | parametric holes                   |

All validate dimensions (reject zero/negative, oversized-vs-face, part-removal)
and run full generation validation. Failures return HTTP 422 with a reason and
never corrupt the design.

## Fallback behavior
- No backend metadata (old designs / feature-graph) → mesh-only visual face
  grouping; all chips shown; edits still gated server-side.
- No backend face match on a click → the visual face is used, feature id mapped
  from the world normal (`face_top`, …).
- Grouping disabled (mesh > 300k triangles) or failing → Phase-1 coplanar patch.
- Unknown `selection_type` → treated as `visual_face`.

## Limitations
- Edge/hole **hover** is not previewed (only click-select); face hover is.
- Popup can clip near the extreme edge of the viewport (anchored to geometry
  centers, which are usually on-screen).
- Cylindrical/curved classification is heuristic (normal covariance), not a true
  surface fit.
- Per-edge fillet/chamfer applies to the part's edges, not the single selected
  edge; hole placement is face-centered, not at the exact click point.
- `visual_face_id` is stable per geometry but not a persistent CAD identity.

## Future work
- Rectangular `add_slot` / `add_cutout` / `add_boss` and `move_hole` /
  `pattern_hole` via a parametric cutout/pattern schema or feature graph.
- Body ops (rename/material/export/duplicate/mirror) and feature ops
  (edit dimensions / suppress / pattern / mirror).
- True BRep edge/face references for exact-point placement and per-edge edits.
- LLM structured-output layer for freeform localized edits (JSON-only).
- Viewport-aware popup repositioning; edge/hole hover previews.

---

## Manual end-to-end test checklist

Run backend (`uvicorn app.main:app --port 8000` from `backend/`) and frontend
(`npm run dev` from `frontend/`), sign in, then for each part below:

1. **Bracket with holes** (`bracket 80x40x6mm with two M6 holes`)
   - [ ] Click the top face → highlight + popup titled "Edit this face",
     subtitle "Top face of …".
   - [ ] Switch to **Hole** mode, click a hole → "Edit this hole" with
     Resize/Move/Pattern/Delete.
   - [ ] Resize the hole to 10 mm → "Applying edit…" → viewer refreshes, hole is
     larger, STL/STEP re-exported.
   - [ ] Delete a hole → hole count drops.
   - [ ] Enter an invalid resize (e.g. 500 mm) → error toast, model unchanged.
2. **Adapter plate** — top-face hole add; fillet the edges.
3. **Enclosure** — Vent chip adds ventilation slots; body mode shows the body.
4. **Reconstructed drawing part** — faces still selectable with lower confidence;
   unsupported ops return "not supported yet" without corrupting the design.
5. **Tire / wheel assembly** — cylindrical faces selectable; edits on unsupported
   families return needs_review.
6. **General**
   - [ ] Orbit/pan/zoom stays smooth; hovering shows a quiet face highlight.
   - [ ] Escape / empty-space click / Cancel all close the popup.
   - [ ] Loading a different part clears the selection.
   - [ ] Grid/axes/gizmo are never selectable.
   - [ ] Existing generate / export / modify flows still work.
```
