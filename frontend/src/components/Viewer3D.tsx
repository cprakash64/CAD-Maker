"use client";

import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber";
import { Edges, Grid, GizmoHelper, GizmoViewport, Html, Line, OrbitControls } from "@react-three/drei";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { PreviewMesh } from "@/lib/types";
import {
  HOLE_PICK_PX,
  matchSelectableFace,
  pickHole,
  type FaceHit,
  type HolePickCandidate,
  type SelectableBody,
  type SelectableEdge,
  type SelectableFace,
  type SelectableHole,
  type SelectionMode,
} from "@/lib/selection";

/** What Viewer3D highlights + anchors the popup to for a selection. */
interface SelectHighlight {
  hit: FaceHit;
  positions?: number[];
  marker?: [number, number, number];
  line?: { a: [number, number, number]; b: [number, number, number] };
}
import {
  buildFaceGroups,
  faceHighlightPositions,
  type FaceGrouping,
} from "@/lib/faceGrouping";
import type { DisplayMode, ViewName } from "./ViewToolbar";

export interface ViewerHandle {
  setView: (view: ViewName) => void;
  capturePng: () => void;
  clearMeasurements: () => void;
  /** Clear the current face-selection highlight. */
  clearSelection: () => void;
  /** Project model-space anchors to screen pixels (null if behind camera). */
  projectPoints: (pts: [number, number, number][]) => ([number, number] | null)[];
}

export interface PickedEntity {
  type: string;
  id: string;
  label: string;
}

/** Highlight color for the selected face patch (reads on light + dark). */
const SELECT_COLOR = "#4c9ffe";

/** Below this pointer travel (px) a press-release counts as a click, not an
 *  orbit/pan drag — so rotating the model never triggers a face selection. */
const DRAG_SLOP_PX = 5;

/**
 * Grow a coplanar triangle patch out from the clicked triangle.
 *
 * We keep every triangle whose face normal is within ~10° of the clicked
 * triangle's normal *and* that lies on the same plane (within 0.5 mm). For the
 * prismatic parts that dominate LunaiCAD (plates, brackets, enclosures) this
 * recovers the whole flat face and gives a clean, Fusion-like highlight without
 * needing real CAD face grouping. Curved surfaces yield a coplanar band, which
 * is acceptable for Phase 1. Coordinates are read straight from the (already
 * transformed) preview geometry, so they share the part's world frame.
 */
function computeCoplanarPatch(geom: THREE.BufferGeometry, faceIndex: number) {
  const pos = geom.attributes.position as THREE.BufferAttribute;
  const index = geom.index;
  const triCount = index ? Math.floor(index.count / 3) : Math.floor(pos.count / 3);
  const vidx = (t: number, c: number) => (index ? index.getX(t * 3 + c) : t * 3 + c);
  const vert = (t: number, c: number) =>
    new THREE.Vector3().fromBufferAttribute(pos, vidx(t, c));
  const triData = (t: number) => {
    const a = vert(t, 0);
    const b = vert(t, 1);
    const c = vert(t, 2);
    const n = new THREE.Vector3()
      .subVectors(b, a)
      .cross(new THREE.Vector3().subVectors(c, a))
      .normalize();
    return { n, a, b, c };
  };

  const base = triData(faceIndex);
  const n0 = base.n;
  const d0 = n0.dot(base.a);
  const NORMAL_EPS = Math.cos((10 * Math.PI) / 180);
  const PLANE_EPS = 0.5;

  const positions: number[] = [];
  const centroid = new THREE.Vector3();
  let vcount = 0;
  for (let t = 0; t < triCount; t++) {
    const { n, a, b, c } = triData(t);
    if (n.dot(n0) < NORMAL_EPS) continue;
    if (
      Math.abs(n0.dot(a) - d0) > PLANE_EPS ||
      Math.abs(n0.dot(b) - d0) > PLANE_EPS ||
      Math.abs(n0.dot(c) - d0) > PLANE_EPS
    )
      continue;
    positions.push(a.x, a.y, a.z, b.x, b.y, b.z, c.x, c.y, c.z);
    centroid.add(a).add(b).add(c);
    vcount += 3;
  }
  if (vcount > 0) {
    centroid.multiplyScalar(1 / vcount);
  } else {
    // Degenerate fallback: just the clicked triangle.
    centroid.copy(base.a).add(base.b).add(base.c).multiplyScalar(1 / 3);
    positions.push(
      base.a.x, base.a.y, base.a.z,
      base.b.x, base.b.y, base.b.z,
      base.c.x, base.c.y, base.c.z
    );
  }
  return { positions, normal: n0, center: centroid, triangleCount: positions.length / 9 };
}

/** Translucent overlay (+ boundary outline for the selected patch; hover is
 *  a quieter fill with no outline). */
function FaceHighlight({
  positions,
  color,
  opacity = 0.32,
  outline = true,
}: {
  positions: number[];
  color: string;
  opacity?: number;
  outline?: boolean;
}) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(Float32Array.from(positions), 3)
    );
    g.computeVertexNormals();
    return g;
  }, [positions]);
  return (
    <mesh geometry={geom} renderOrder={outline ? 10 : 9}>
      <meshBasicMaterial
        color={color}
        transparent
        opacity={opacity}
        side={THREE.DoubleSide}
        depthWrite={false}
        polygonOffset
        polygonOffsetFactor={-2}
        polygonOffsetUnits={-2}
      />
      {/* Coplanar interior edges have ~0 dihedral so EdgesGeometry drops them —
          what remains is the crisp patch outline (selected only; hover is quiet). */}
      {outline && <Edges threshold={15} color={color} />}
    </mesh>
  );
}

// View directions in world space. Geometry is rotated X-90 (model Z -> world Y).
const VIEW_DIRS: Record<Exclude<ViewName, "fit">, [number, number, number]> = {
  top: [0, 1, 0.0001],
  bottom: [0, -1, 0.0001],
  front: [0, 0, 1],
  back: [0, 0, -1],
  right: [1, 0, 0],
  left: [-1, 0, 0],
  iso: [1, 0.8, 1],
};

/** Per-theme, per-mode color palette for the viewer chrome and the part.
 *  `grid`/`gridCell` are the major/minor grid lines; `edge` is the overlay edge
 *  color on shaded faces; `wire` is the wireframe line color (must read against
 *  the empty background). */
function palette(dark: boolean, mode: DisplayMode) {
  if (mode === "technical") {
    // A restrained blueprint: deep technical backdrop, light line work.
    return dark
      ? { bg: "#0d1417", grid: "#33424c", gridCell: "#1b242a", part: "#0d1417", edge: "#bfe4f0", wire: "#bfe4f0" }
      : { bg: "#dde6ea", grid: "#93aab6", gridCell: "#c2d0d7", part: "#dde6ea", edge: "#1d4d66", wire: "#1d4d66" };
  }
  return dark
    ? { bg: "#0a0908", grid: "#6f6554", gridCell: "#3a352b", part: "#b4ab9c", edge: "#262019", wire: "#d8cfbd" }
    : { bg: "#e4ded3", grid: "#a99b82", gridCell: "#c8bda8", part: "#9a9183", edge: "#3c352b", wire: "#4a4338" };
}

/** Apply the same transform the mesh uses (rotateX(-90) then center) to an anchor. */
export function transformAnchor(
  a: [number, number, number],
  center: THREE.Vector3
): THREE.Vector3 {
  return new THREE.Vector3(a[0], a[2], -a[1]).sub(center);
}

// Screen-pixel proximity threshold for picking an edge (holes use HOLE_PICK_PX).
const EDGE_PICK_PX = 14;

/** Perpendicular distance (world mm) from a point to an infinite line through
 *  `origin` along the unit vector `dir`. Used to test whether a click landed on
 *  a hole's cylindrical wall (distance ≈ the hole radius). */
function distPointToLine(
  p: THREE.Vector3,
  origin: THREE.Vector3,
  dir: THREE.Vector3
): number {
  const v = p.clone().sub(origin);
  const t = v.dot(dir);
  return v.sub(dir.clone().multiplyScalar(t)).length();
}

/**
 * Pick an edge/hole/body under the cursor by projecting backend entity anchors
 * to screen and choosing the nearest within a pixel threshold.
 *
 * Hole matching (see {@link pickHole}) is two-signal so it catches both ways a
 * user clicks a hole: the projected screen distance to the hole *center*
 * (clicking the opening) AND the 3D distance from the clicked surface point to
 * the hole *axis* (clicking the cylindrical wall — distance ≈ the hole radius). A
 * hit ON the hole is a strong signal, so in Auto mode it promotes the click to
 * the hole ahead of an edge/face; otherwise Auto falls back edge → hole(by
 * screen) → face. Returns null when nothing qualifies (the caller then handles
 * faces / does nothing).
 */
function pickEntity(
  mode: SelectionMode,
  cx: number,
  cy: number,
  toPx: (a: [number, number, number]) => [number, number],
  holes: SelectableHole[] | undefined,
  edges: SelectableEdge[] | undefined,
  bodies: SelectableBody[] | undefined,
  center: THREE.Vector3,
  clickedPoint: THREE.Vector3,
  scale: number
): SelectHighlight | null {
  // Nearest hole (opening or cylindrical wall), with a flag for whether the click
  // landed ON it — `onEntity` hits win over edges/faces in Auto mode. The scoring
  // rule itself is a pure, unit-tested function; here we only compute each hole's
  // screen + axis distances (which need the live camera/click point).
  const nearestHole = (): { hole: SelectableHole; onEntity: boolean } | null => {
    const wallTol = Math.max(1.0, scale * 0.02); // absolute + zoom-independent slack
    const candidates: HolePickCandidate[] = (holes ?? []).map((h) => {
      const [px, py] = toPx(h.center);
      const axisWorld = new THREE.Vector3(h.axis[0], h.axis[2], -h.axis[1]).normalize();
      const centerWorld = transformAnchor(h.center, center);
      return {
        hole: h,
        screenDist: Math.hypot(px - cx, py - cy),
        axisDist: distPointToLine(clickedPoint, centerWorld, axisWorld),
      };
    });
    return pickHole(candidates, wallTol);
  };
  const nearestEdge = (): SelectableEdge | null => {
    let best: SelectableEdge | null = null;
    let bd = EDGE_PICK_PX;
    for (const eg of edges ?? []) {
      const mid: [number, number, number] = [
        (eg.start[0] + eg.end[0]) / 2,
        (eg.start[1] + eg.end[1]) / 2,
        (eg.start[2] + eg.end[2]) / 2,
      ];
      const d = Math.min(
        ...[eg.start, mid, eg.end].map((p) => {
          const [px, py] = toPx(p);
          return Math.hypot(px - cx, py - cy);
        })
      );
      if (d < bd) {
        bd = d;
        best = eg;
      }
    }
    return best;
  };

  let hole: SelectableHole | null = null;
  let edge: SelectableEdge | null = null;
  let body: SelectableBody | null = null;
  if (mode === "hole") {
    // Hole mode: hole picking is attempted before anything else, and nothing
    // else can win — a click that isn't on a hole selects nothing.
    hole = nearestHole()?.hole ?? null;
  } else if (mode === "edge") edge = nearestEdge();
  else if (mode === "body") body = bodies?.[0] ?? null;
  else if (mode === "auto") {
    const h = nearestHole();
    if (h && h.onEntity) {
      // Clicked on a hole opening / wall — the hole wins over edge & face.
      hole = h.hole;
    } else {
      edge = nearestEdge();
      if (!edge) hole = h?.hole ?? null; // else nearest hole by projected center
    }
  }

  if (hole) {
    const wc = transformAnchor(hole.center, center);
    const wn = new THREE.Vector3(hole.axis[0], hole.axis[2], -hole.axis[1]).normalize();
    const c: [number, number, number] = [wc.x, wc.y, wc.z];
    return {
      hit: {
        faceIndex: null, point: c, normal: [wn.x, wn.y, wn.z], center: c,
        axisLabel: "hole", patchTriangleCount: 0, cameraPosition: null,
        kind: "hole", hole,
      },
      marker: c,
    };
  }
  if (edge) {
    const a = transformAnchor(edge.start, center);
    const b = transformAnchor(edge.end, center);
    const mid: [number, number, number] = [(a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2];
    return {
      hit: {
        faceIndex: null, point: mid, normal: [0, 0, 1], center: mid,
        axisLabel: "edge", patchTriangleCount: 0, cameraPosition: null,
        kind: "edge", edge,
      },
      line: { a: [a.x, a.y, a.z], b: [b.x, b.y, b.z] },
    };
  }
  if (body) {
    const p: [number, number, number] = [clickedPoint.x, clickedPoint.y, clickedPoint.z];
    return {
      hit: {
        faceIndex: null, point: p, normal: [0, 0, 1], center: p,
        axisLabel: "body", patchTriangleCount: 0, cameraPosition: null,
        kind: "body", body,
      },
    };
  }
  return null;
}

function faceLabel(n: THREE.Vector3): string {
  const ax = Math.abs(n.x), ay = Math.abs(n.y), az = Math.abs(n.z);
  if (ay >= ax && ay >= az) return n.y >= 0 ? "top" : "bottom";
  if (ax >= ay && ax >= az) return n.x >= 0 ? "+X" : "-X";
  return n.z >= 0 ? "+Y" : "-Y";
}

function PartMesh({
  mesh,
  centerRef,
  radiusRef,
  onFaceSelect,
  onHover,
  onMeasure,
  measureMode,
  selectEnabled,
  selectionMode,
  selectableFaces,
  selectableHoles,
  selectableEdges,
  selectableBodies,
  pointerDownRef,
  mode,
  color,
  edgeColor,
  wireColor,
}: {
  mesh: PreviewMesh;
  centerRef: React.MutableRefObject<THREE.Vector3>;
  radiusRef: React.MutableRefObject<number>;
  onFaceSelect?: (p: SelectHighlight) => void;
  onHover?: (positions: number[] | null) => void;
  onMeasure?: (p: THREE.Vector3) => void;
  measureMode?: boolean;
  selectEnabled?: boolean;
  selectionMode: SelectionMode;
  selectableFaces?: SelectableFace[];
  selectableHoles?: SelectableHole[];
  selectableEdges?: SelectableEdge[];
  selectableBodies?: SelectableBody[];
  pointerDownRef: React.MutableRefObject<{ x: number; y: number } | null>;
  mode: DisplayMode;
  color: string;
  edgeColor: string;
  wireColor: string;
}) {
  const { camera, size } = useThree();
  // Build the geometry AND its visual-face grouping once per mesh. Grouping is
  // cached here (keyed on `mesh`) so it never runs per frame; it reads the
  // already-transformed position/index buffers so all coordinates share the
  // part's world frame.
  const built = useMemo(() => {
    const geom = new THREE.BufferGeometry();
    geom.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(Float32Array.from(mesh.positions), 3)
    );
    geom.setIndex(mesh.indices);
    geom.computeVertexNormals();
    geom.rotateX(-Math.PI / 2);
    geom.computeBoundingBox();
    const c = new THREE.Vector3();
    geom.boundingBox?.getCenter(c);
    geom.translate(-c.x, -c.y, -c.z);
    geom.computeBoundingSphere();
    centerRef.current.copy(c);
    radiusRef.current = geom.boundingSphere?.radius ?? 100;

    const pos = (geom.attributes.position as THREE.BufferAttribute).array as ArrayLike<number>;
    const idx = geom.index
      ? (geom.index.array as unknown as ArrayLike<number>)
      : null;
    let grouping: FaceGrouping | null = null;
    try {
      grouping = buildFaceGroups(pos, idx); // null when too large / empty
    } catch {
      grouping = null; // defensive: fall back to Phase 1 patch selection
    }
    return { geom, pos, idx, grouping };
  }, [mesh, centerRef, radiusRef]);
  const geometry = built.geom;

  function handleClick(e: ThreeEvent<MouseEvent>) {
    // Ignore the click if the pointer travelled far enough to be an orbit/pan.
    const dn = pointerDownRef.current;
    const ne = e.nativeEvent as MouseEvent;
    if (dn && Math.hypot(ne.clientX - dn.x, ne.clientY - dn.y) > DRAG_SLOP_PX) return;

    if (measureMode && onMeasure && e.point) {
      e.stopPropagation();
      onMeasure(e.point.clone());
      return;
    }
    if (!selectEnabled || !onFaceSelect || e.faceIndex == null || !e.face) return;
    e.stopPropagation();

    // --- Phase 6: entity picking (edge / hole / body) by mode ---------------
    // Backend entities live in the CadQuery model frame; project their anchors
    // to screen and pick the one nearest the click. This reuses the same model→
    // world transform as everything else, no separate raycast geometry needed.
    const cx = ne.offsetX;
    const cy = ne.offsetY;
    const toPx = (anchor: [number, number, number]): [number, number] => {
      const v = transformAnchor(anchor, centerRef.current).project(camera);
      return [((v.x + 1) / 2) * size.width, ((1 - v.y) / 2) * size.height];
    };
    const entityHit = pickEntity(
      selectionMode,
      cx,
      cy,
      toPx,
      selectableHoles,
      selectableEdges,
      selectableBodies,
      centerRef.current,
      e.point,
      radiusRef.current
    );
    if (entityHit) {
      onFaceSelect(entityHit);
      return;
    }
    // In an explicit entity mode with no entity under the cursor, do nothing
    // (don't fall through to a face selection).
    if (selectionMode === "hole" || selectionMode === "edge" || selectionMode === "body") {
      return;
    }

    // The clicked triangle's own world normal — meaningful even on curved faces
    // where the group's average normal cancels out (e.g. a full cylinder wall).
    const wn = e.face.normal.clone().transformDirection(e.object.matrixWorld).normalize();
    const normal: [number, number, number] = [wn.x, wn.y, wn.z];

    let positions: number[] | null = null;
    let center: [number, number, number] | null = null;
    let extra: Partial<FaceHit> = {};

    // Phase 2: map the clicked triangle to its grouped visual face.
    const g = built.grouping;
    if (g && e.faceIndex >= 0 && e.faceIndex < g.triangleToFace.length) {
      const fid = g.triangleToFace[e.faceIndex];
      const face =
        fid != null && fid >= 0 && fid < g.faces.length ? g.faces[fid] : undefined;
      if (face) {
        positions = faceHighlightPositions(built.pos, built.idx, face.triangleIndices);
        center = face.center;
        extra = {
          visualFaceId: face.id,
          faceKind: face.kind,
          triangleIndices: face.triangleIndices,
          area: face.area,
          bbox: face.bbox,
          localFrame: face.localFrame,
        };
      }
    }

    // Phase 1 fallback: coplanar triangle patch (grouping disabled/failed).
    if (!positions) {
      const patch = computeCoplanarPatch(geometry, e.faceIndex);
      positions = patch.positions;
      center = [patch.center.x, patch.center.y, patch.center.z];
      extra = { faceKind: "unknown" };
    }
    // `center` is always set above (visual-face centroid or the patch centroid);
    // fall back to the clicked point as a defensive, type-narrowing default.
    const faceCenter: [number, number, number] = center ?? [e.point.x, e.point.y, e.point.z];

    // Phase 5: match this click to a backend semantic face. Backend faces are in
    // the CadQuery model frame; bring them into the viewer world frame with the
    // same transform the mesh uses (normal rotate-X(-90); center via transformAnchor).
    let backendFace = null;
    if (selectableFaces && selectableFaces.length) {
      const cands = selectableFaces.map((face) => {
        const n = new THREE.Vector3(face.normal[0], face.normal[2], -face.normal[1]).normalize();
        const c = transformAnchor(face.center, centerRef.current);
        return {
          face,
          worldNormal: [n.x, n.y, n.z] as [number, number, number],
          worldCenter: [c.x, c.y, c.z] as [number, number, number],
        };
      });
      backendFace = matchSelectableFace(normal, faceCenter, cands, radiusRef.current);
    }

    onFaceSelect({
      hit: {
        faceIndex: e.faceIndex,
        point: [e.point.x, e.point.y, e.point.z],
        normal,
        center: faceCenter,
        axisLabel: faceLabel(wn),
        patchTriangleCount: extra.triangleIndices?.length ?? positions.length / 9,
        cameraPosition: [e.camera.position.x, e.camera.position.y, e.camera.position.z],
        backendFace,
        ...extra,
      },
      positions,
    });
  }

  // Debounced face hover (Auto/Face modes only). Recomputes only when the
  // hovered triangle's face group changes, and never while a button is held
  // (i.e. while orbiting), so it stays smooth on complex models.
  const lastHover = useRef<number | null>(null);
  function handleMove(e: ThreeEvent<PointerEvent>) {
    if (!onHover || !selectEnabled) return;
    if (selectionMode !== "auto" && selectionMode !== "face") {
      if (lastHover.current !== null) {
        lastHover.current = null;
        onHover(null);
      }
      return;
    }
    if ((e.nativeEvent as PointerEvent).buttons) return; // dragging → no hover
    if (e.faceIndex == null || e.faceIndex === lastHover.current) return;
    lastHover.current = e.faceIndex;
    const g = built.grouping;
    if (g && e.faceIndex >= 0 && e.faceIndex < g.triangleToFace.length) {
      const fid = g.triangleToFace[e.faceIndex];
      const face =
        fid != null && fid >= 0 && fid < g.faces.length ? g.faces[fid] : undefined;
      if (face) {
        onHover(faceHighlightPositions(built.pos, built.idx, face.triangleIndices));
        return;
      }
    }
    onHover(null);
  }
  function handleOut() {
    if (lastHover.current !== null || !onHover) {
      lastHover.current = null;
      onHover?.(null);
    }
  }

  const wireframe = mode === "wireframe";
  const showEdges = mode === "edges" || mode === "technical" || mode === "shaded";
  const ghost = mode === "technical";

  return (
    <mesh
      geometry={geometry}
      castShadow={!ghost}
      receiveShadow={!ghost}
      onClick={handleClick}
      onPointerMove={handleMove}
      onPointerOut={handleOut}
    >
      {wireframe ? (
        <meshBasicMaterial color={wireColor} wireframe />
      ) : (
        <meshStandardMaterial
          color={color}
          metalness={ghost ? 0.1 : 0.45}
          roughness={ghost ? 0.9 : 0.5}
          transparent={ghost}
          opacity={ghost ? 0.12 : 1}
        />
      )}
      {showEdges && !wireframe && (
        // Higher threshold in plain shaded mode = only the prominent silhouette
        // edges (subtle); lower threshold in edges/technical = full CAD linework.
        <Edges threshold={mode === "shaded" ? 32 : 16} color={edgeColor} />
      )}
    </mesh>
  );
}

function Rig({
  handleRef,
  centerRef,
  radiusRef,
  onClear,
  onClearSelection,
}: {
  handleRef: React.Ref<ViewerHandle>;
  centerRef: React.MutableRefObject<THREE.Vector3>;
  radiusRef: React.MutableRefObject<number>;
  onClear: () => void;
  onClearSelection: () => void;
}) {
  const { camera, gl, scene, controls, size } = useThree();

  function place(dir: [number, number, number]) {
    const r = radiusRef.current || 100;
    const d3 = new THREE.Vector3(...dir).normalize();
    if ((camera as THREE.OrthographicCamera).isOrthographicCamera) {
      const ortho = camera as THREE.OrthographicCamera;
      const dist = r * 4;
      ortho.position.copy(d3.multiplyScalar(dist));
      ortho.up.set(0, 1, 0);
      ortho.lookAt(0, 0, 0);
      const minDim = Math.min(size.width, size.height) || 1;
      ortho.zoom = (minDim / (2 * r)) * 0.82;
      ortho.near = 0.1;
      ortho.far = dist + r * 8;
      ortho.updateProjectionMatrix();
    } else {
      const persp = camera as THREE.PerspectiveCamera;
      const fov = (persp.fov ?? 45) * (Math.PI / 180);
      const dist = (r / Math.sin(fov / 2)) * 1.25;
      persp.position.copy(d3.multiplyScalar(dist));
      persp.up.set(0, 1, 0);
      persp.lookAt(0, 0, 0);
      persp.near = Math.max(0.1, dist - r * 4);
      persp.far = dist + r * 8;
      persp.updateProjectionMatrix();
    }
    const oc = controls as unknown as { target?: THREE.Vector3; update?: () => void };
    if (oc?.target) {
      oc.target.set(0, 0, 0);
      oc.update?.();
    }
  }

  useEffect(() => {
    place(VIEW_DIRS.iso);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [radiusRef.current]);

  useImperativeHandle(handleRef, () => ({
    setView(view: ViewName) {
      place(view === "fit" ? VIEW_DIRS.iso : VIEW_DIRS[view]);
    },
    capturePng() {
      gl.render(scene, camera);
      const url = gl.domElement.toDataURL("image/png");
      const a = document.createElement("a");
      a.href = url;
      a.download = "view.png";
      a.click();
    },
    clearMeasurements() {
      onClear();
    },
    clearSelection() {
      onClearSelection();
    },
    projectPoints(pts) {
      return pts.map((p) => {
        const v = transformAnchor(p, centerRef.current).project(camera);
        if (v.z > 1) return null;
        return [((v.x + 1) / 2) * size.width, ((1 - v.y) / 2) * size.height] as [number, number];
      });
    },
  }));

  return null;
}

interface Measurement {
  id: number;
  a: THREE.Vector3;
  b: THREE.Vector3;
}

/** All measurement annotations. Each measurement is its own object; the newest
 *  keeps its mm label by default, and any measurement reveals its label when the
 *  user hovers its line or either endpoint dot. */
function Measurements({
  pending,
  measurements,
  color,
  hoverColor,
  size,
}: {
  pending: THREE.Vector3 | null;
  measurements: Measurement[];
  color: string;
  hoverColor: string;
  size: number;
}) {
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestId = measurements.length ? measurements[measurements.length - 1]!.id : null;

  // Debounce hover-out so moving between a line and its dots never flickers.
  function enter(id: number) {
    if (clearTimer.current) clearTimeout(clearTimer.current);
    setHoveredId(id);
  }
  function leave() {
    if (clearTimer.current) clearTimeout(clearTimer.current);
    clearTimer.current = setTimeout(() => setHoveredId(null), 90);
  }
  useEffect(
    () => () => {
      if (clearTimer.current) clearTimeout(clearTimer.current);
    },
    []
  );

  return (
    <group>
      {pending && <Dot p={pending} color={hoverColor} size={size * 1.15} />}
      {measurements.map((m) => {
        const hovered = hoveredId === m.id;
        return (
          <Segment
            key={m.id}
            m={m}
            color={hovered ? hoverColor : color}
            size={size}
            hovered={hovered}
            showLabel={hovered || m.id === latestId}
            onEnter={() => enter(m.id)}
            onLeave={leave}
          />
        );
      })}
    </group>
  );
}

function Segment({
  m,
  color,
  size,
  hovered,
  showLabel,
  onEnter,
  onLeave,
}: {
  m: Measurement;
  color: string;
  size: number;
  hovered: boolean;
  showLabel: boolean;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const { a, b } = m;
  const mid = useMemo(() => a.clone().add(b).multiplyScalar(0.5), [a, b]);
  const len = useMemo(() => a.distanceTo(b), [a, b]);
  const quat = useMemo(
    () =>
      new THREE.Quaternion().setFromUnitVectors(
        new THREE.Vector3(0, 1, 0),
        b.clone().sub(a).normalize()
      ),
    [a, b]
  );
  const hitR = Math.max(size * 1.9, len * 0.02);
  const over = (e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation();
    onEnter();
  };

  return (
    <group>
      {/* Invisible but raycastable hit cylinder = comfortable hover target. */}
      <mesh position={mid} quaternion={quat} onPointerOver={over} onPointerOut={onLeave}>
        <cylinderGeometry args={[hitR, hitR, Math.max(len, 0.001), 6]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <Line points={[a, b]} color={color} lineWidth={hovered ? 2.8 : 2} dashed dashScale={4} />
      <Dot p={a} color={color} size={hovered ? size * 1.25 : size} hitSize={size * 2.4} onEnter={onEnter} onLeave={onLeave} />
      <Dot p={b} color={color} size={hovered ? size * 1.25 : size} hitSize={size * 2.4} onEnter={onEnter} onLeave={onLeave} />
      {showLabel && (
        <Html position={mid} center zIndexRange={[40, 0]}>
          <div
            className={`pointer-events-none select-none whitespace-nowrap rounded-md border bg-panel px-1.5 py-0.5 font-mono text-[10px] font-medium tabular-nums text-slate-100 shadow-glass ${
              hovered ? "border-[color:var(--glass-border-strong)]" : "border-edge"
            }`}
          >
            {len.toFixed(1)} mm
          </div>
        </Html>
      )}
    </group>
  );
}

function Dot({
  p,
  color,
  size,
  hitSize,
  onEnter,
  onLeave,
}: {
  p: THREE.Vector3;
  color: string;
  size: number;
  hitSize?: number;
  onEnter?: () => void;
  onLeave?: () => void;
}) {
  return (
    <group position={p}>
      <mesh>
        <sphereGeometry args={[size, 16, 16]} />
        <meshBasicMaterial color={color} />
      </mesh>
      {hitSize && (onEnter || onLeave) && (
        <mesh
          onPointerOver={(e) => {
            e.stopPropagation();
            onEnter?.();
          }}
          onPointerOut={() => onLeave?.()}
        >
          <sphereGeometry args={[hitSize, 10, 10]} />
          <meshBasicMaterial transparent opacity={0} depthWrite={false} />
        </mesh>
      )}
    </group>
  );
}

interface Props {
  mesh: PreviewMesh | null;
  /** Enable click-to-select of visible mesh faces. */
  selectEnabled?: boolean;
  /** Fires with the picked face (or null when the selection is cleared). */
  onFacePick?: (hit: FaceHit | null) => void;
  /** Floating content anchored to the selected face (the edit popup). */
  faceOverlay?: React.ReactNode;
  /** Selection mode (Phase 6). Defaults to "auto". */
  selectionMode?: SelectionMode;
  /** Backend semantic geometry (Phase 5/6) to pick a click against. */
  selectableFaces?: SelectableFace[];
  selectableHoles?: SelectableHole[];
  selectableEdges?: SelectableEdge[];
  selectableBodies?: SelectableBody[];
  materialColor?: string;
  className?: string;
  showGrid?: boolean;
  showAxes?: boolean;
  orthographic?: boolean;
  dark?: boolean;
  displayMode?: DisplayMode;
  measureMode?: boolean;
}

const Viewer3D = forwardRef<ViewerHandle, Props>(function Viewer3D(
  {
    mesh,
    selectEnabled = true,
    onFacePick,
    faceOverlay,
    selectionMode = "auto",
    selectableFaces,
    selectableHoles,
    selectableEdges,
    selectableBodies,
    materialColor,
    className,
    showGrid = true,
    showAxes = true,
    orthographic = false,
    dark = true,
    displayMode = "shaded",
    measureMode = false,
  },
  ref
) {
  const centerRef = useRef(new THREE.Vector3());
  const radiusRef = useRef(100);
  const measureId = useRef(0);
  const pointerDown = useRef<{ x: number; y: number } | null>(null);
  const [pending, setPending] = useState<THREE.Vector3 | null>(null);
  const [measurements, setMeasurements] = useState<Measurement[]>([]);
  const [highlight, setHighlight] = useState<{
    positions?: number[];
    marker?: [number, number, number];
    line?: { a: [number, number, number]; b: [number, number, number] };
    center: [number, number, number];
  } | null>(null);
  const [hover, setHover] = useState<number[] | null>(null);

  const pal = palette(dark, displayMode);
  // In technical mode the model uses the blueprint line color; otherwise honor
  // the user's material pick (falls back to the themed metal).
  const partColor = displayMode === "technical" ? pal.part : materialColor ?? pal.part;
  const measureColor = dark ? "#e6bd64" : "#7a5d18";
  const measureHover = dark ? "#ffd98a" : "#9c7a22";

  function addMeasurePoint(p: THREE.Vector3) {
    setPending((prev) => {
      if (!prev) return p;
      setMeasurements((ms) => [...ms, { id: ++measureId.current, a: prev, b: p }]);
      return null;
    });
  }
  function clear() {
    setPending(null);
    setMeasurements([]);
  }
  function handleFaceSelect(p: SelectHighlight) {
    setHighlight({
      positions: p.positions,
      marker: p.marker,
      line: p.line,
      center: p.hit.center,
    });
    onFacePick?.(p.hit);
  }
  // Click on empty space (missing every object) clears the selection — unless
  // the pointer was dragged (an orbit/pan that happened to end off-model).
  function handleMissed(e: MouseEvent) {
    if (!selectEnabled) return;
    const dn = pointerDown.current;
    if (dn && Math.hypot(e.clientX - dn.x, e.clientY - dn.y) > DRAG_SLOP_PX) return;
    if (highlight) {
      setHighlight(null);
      onFacePick?.(null);
    }
  }
  // Turning the ruler off immediately hides every measurement + temp point.
  useEffect(() => {
    if (!measureMode) clear();
  }, [measureMode]);
  // Switching parts must never leave stale measurements/selection on old geometry.
  useEffect(() => {
    clear();
    setHighlight(null);
    setHover(null);
  }, [mesh]);

  return (
    <div
      className={
        className ??
        "relative h-[520px] w-full overflow-hidden rounded-lg border border-edge bg-viewport lg:h-[640px]"
      }
      onPointerDownCapture={(e) => {
        pointerDown.current = { x: e.clientX, y: e.clientY };
      }}
    >
      {!mesh ? (
        <div className="flex h-full items-center justify-center text-sm text-slate-500">
          No preview yet
        </div>
      ) : (
        <Canvas
          key={orthographic ? "ortho" : "persp"}
          orthographic={orthographic}
          camera={
            orthographic
              ? { position: [120, 90, 120], zoom: 4, near: 0.1, far: 6000 }
              : { position: [120, 90, 120], fov: 45 }
          }
          shadows
          gl={{ preserveDrawingBuffer: true }}
          onPointerMissed={handleMissed}
        >
          <color attach="background" args={[pal.bg]} />
          <ambientLight intensity={dark ? 0.55 : 0.75} />
          <directionalLight position={[80, 120, 60]} intensity={dark ? 1.05 : 1.2} castShadow />
          <directionalLight position={[-60, 40, -80]} intensity={0.35} />
          <PartMesh
            mesh={mesh}
            centerRef={centerRef}
            radiusRef={radiusRef}
            onFaceSelect={handleFaceSelect}
            onHover={setHover}
            onMeasure={addMeasurePoint}
            measureMode={measureMode}
            selectEnabled={selectEnabled}
            selectionMode={selectionMode}
            selectableFaces={selectableFaces}
            selectableHoles={selectableHoles}
            selectableEdges={selectableEdges}
            selectableBodies={selectableBodies}
            pointerDownRef={pointerDown}
            mode={displayMode}
            color={partColor}
            edgeColor={pal.edge}
            wireColor={pal.wire}
          />
          {/* Quiet hover preview (only when nothing is strongly selected here). */}
          {hover && !highlight?.positions && (
            <FaceHighlight positions={hover} color={SELECT_COLOR} opacity={0.14} outline={false} />
          )}
          {highlight?.positions && (
            <FaceHighlight positions={highlight.positions} color={SELECT_COLOR} />
          )}
          {highlight?.marker && (
            <mesh position={highlight.marker} renderOrder={11}>
              <sphereGeometry args={[Math.max(0.8, radiusRef.current * 0.02), 16, 16]} />
              <meshBasicMaterial color={SELECT_COLOR} transparent opacity={0.85} depthTest={false} />
            </mesh>
          )}
          {highlight?.line && (
            <Line
              points={[highlight.line.a, highlight.line.b]}
              color={SELECT_COLOR}
              lineWidth={4}
            />
          )}
          {highlight && faceOverlay && (
            <Html position={highlight.center} zIndexRange={[60, 0]} style={{ pointerEvents: "none" }}>
              <div style={{ pointerEvents: "auto" }}>{faceOverlay}</div>
            </Html>
          )}
          <Measurements
            pending={pending}
            measurements={measurements}
            color={measureColor}
            hoverColor={measureHover}
            size={Math.max(0.6, radiusRef.current * 0.014)}
          />
          {showGrid && (
            <Grid
              args={[800, 800]}
              cellSize={10}
              cellThickness={0.8}
              sectionSize={50}
              sectionThickness={1.5}
              sectionColor={pal.grid}
              cellColor={pal.gridCell}
              position={[0, -0.01, 0]}
              infiniteGrid
              fadeDistance={1100}
              fadeStrength={1.2}
            />
          )}
          <OrbitControls makeDefault enableDamping />
          {showAxes && (
            <GizmoHelper alignment="bottom-right" margin={[60, 60]}>
              <GizmoViewport labelColor={dark ? "white" : "#2a2723"} axisHeadScale={0.9} />
            </GizmoHelper>
          )}
          <Rig
            handleRef={ref}
            centerRef={centerRef}
            radiusRef={radiusRef}
            onClear={clear}
            onClearSelection={() => {
              setHighlight(null);
            }}
          />
        </Canvas>
      )}
    </div>
  );
});

export default Viewer3D;
