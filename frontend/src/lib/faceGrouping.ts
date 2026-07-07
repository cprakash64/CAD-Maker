// Phase 2 face-grouping layer: turn a raw triangle soup (a loaded CAD preview
// mesh) into "visual faces" — connected patches of triangles that read as one
// surface (a flat face, a cylinder wall, a fillet). Pure and three.js-free so it
// unit-tests with plain arrays; the viewer feeds it the geometry's position +
// index buffers (already in world frame) and caches the result per geometry.
//
// See [[drawing-to-cad-pipeline]]. Backend is untouched — this is display-only.

export type FaceKind = "planar" | "cylindrical" | "curved" | "unknown";

/** A 3-component vector as a fixed-length tuple, so literal indices (`v[0]`,
 *  `v[1]`, `v[2]`) are known-present under `noUncheckedIndexedAccess`. */
export type Vec3 = [number, number, number];

/** A 3×3 matrix (row-major) as a tuple of Vec3 rows. */
type Mat3 = [Vec3, Vec3, Vec3];

export interface FaceBBox {
  min: Vec3;
  max: Vec3;
}

export interface FaceLocalFrame {
  origin: Vec3;
  normal: Vec3;
  tangent: Vec3;
  bitangent: Vec3;
}

export interface VisualFace {
  id: string;
  kind: FaceKind;
  /** Triangle indices (into the geometry) that make up this face. */
  triangleIndices: number[];
  /** Area-weighted average normal (planar) or axis-perp reference (world frame). */
  normal: Vec3;
  /** Area-weighted centroid. */
  center: Vec3;
  /** Total surface area (mm²). */
  area: number;
  bbox: FaceBBox;
  /** Estimated axis for cylindrical faces (world frame). */
  axis?: Vec3;
  localFrame: FaceLocalFrame;
}

export interface FaceGrouping {
  faces: VisualFace[];
  /** faceIndex (raycast triangle) → index into `faces`, or -1 if ungrouped. */
  triangleToFace: Int32Array;
  triangleCount: number;
}

export interface FaceGroupingOptions {
  /** Max dihedral between adjacent triangles that still counts as the same
   *  surface. Splits real edges (≥90°) and chamfers (~45°) while keeping
   *  reasonably-tessellated cylinders whole. Default 20°. */
  creaseAngleDeg?: number;
  /** A group whose normals stay within this of their average is "planar".
   *  Default 4° (CAD planar tol is 2–5°). */
  planarAngleDeg?: number;
  /** Vertex weld quantization (mm) — merges shared edge vertices. Default 1e-3. */
  weldTol?: number;
  /** Skip grouping above this triangle count (keeps the UI responsive). */
  maxTriangles?: number;
}

// --- small vector helpers (plain tuples, no three.js) ----------------------

function sub(a: Vec3, b: Vec3): Vec3 {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}
function cross(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}
function dot(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
function len(a: Vec3): number {
  return Math.hypot(a[0], a[1], a[2]);
}
function norm(a: Vec3): Vec3 {
  const l = len(a) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
}
function perpendicular(n: Vec3): Vec3 {
  // A stable in-plane tangent: cross with whichever axis is least aligned.
  const ax: Vec3 = Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
  return norm(cross(n, ax));
}

/**
 * Jacobi eigen-decomposition of a symmetric 3×3 matrix given as
 * `[m00, m01, m02, m11, m12, m22]`. Returns eigenvalues sorted descending and
 * their (column) eigenvectors. Used to classify a group's normal distribution:
 * one dominant direction ⇒ planar, a plane of directions ⇒ cylindrical, a full
 * spread ⇒ curved.
 */
export function symmetricEig3(m: number[]): { values: Vec3; vectors: Mat3 } {
  const g = (i: number): number => m[i] ?? 0;
  const a: Mat3 = [
    [g(0), g(1), g(2)],
    [g(1), g(3), g(4)],
    [g(2), g(4), g(5)],
  ];
  const v: Mat3 = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  // Row/col indices are always 0..2 by construction; these accessors narrow the
  // tuple element type without changing any arithmetic.
  const A = (i: number, j: number): number => a[i]![j]!;
  const setA = (i: number, j: number, val: number): void => {
    a[i]![j] = val;
  };
  const V = (i: number, j: number): number => v[i]![j]!;
  const setV = (i: number, j: number, val: number): void => {
    v[i]![j] = val;
  };

  for (let iter = 0; iter < 32; iter++) {
    // Largest off-diagonal element.
    let p = 0;
    let q = 1;
    let max = Math.abs(A(0, 1));
    if (Math.abs(A(0, 2)) > max) {
      max = Math.abs(A(0, 2));
      p = 0;
      q = 2;
    }
    if (Math.abs(A(1, 2)) > max) {
      max = Math.abs(A(1, 2));
      p = 1;
      q = 2;
    }
    if (max < 1e-12) break;
    const phi = 0.5 * Math.atan2(2 * A(p, q), A(q, q) - A(p, p));
    const c = Math.cos(phi);
    const s = Math.sin(phi);
    for (let k = 0; k < 3; k++) {
      const akp = A(k, p);
      const akq = A(k, q);
      setA(k, p, c * akp - s * akq);
      setA(k, q, s * akp + c * akq);
    }
    for (let k = 0; k < 3; k++) {
      const apk = A(p, k);
      const aqk = A(q, k);
      setA(p, k, c * apk - s * aqk);
      setA(q, k, s * apk + c * aqk);
    }
    for (let k = 0; k < 3; k++) {
      const vkp = V(k, p);
      const vkq = V(k, q);
      setV(k, p, c * vkp - s * vkq);
      setV(k, q, s * vkp + c * vkq);
    }
  }
  const idx: Vec3 = [0, 1, 2];
  idx.sort((i, j) => A(j, j) - A(i, i));
  const [i0, i1, i2] = idx;
  return {
    values: [A(i0, i0), A(i1, i1), A(i2, i2)],
    vectors: [
      [V(0, i0), V(1, i0), V(2, i0)],
      [V(0, i1), V(1, i1), V(2, i1)],
      [V(0, i2), V(1, i2), V(2, i2)],
    ],
  };
}

/**
 * Group a mesh's triangles into visual faces. Returns `null` when there is
 * nothing to group or the mesh is over the size guard — callers then fall back
 * to Phase 1 coplanar-patch selection.
 */
export function buildFaceGroups(
  positions: ArrayLike<number>,
  index: ArrayLike<number> | null,
  opts: FaceGroupingOptions = {}
): FaceGrouping | null {
  const cosCrease = Math.cos(((opts.creaseAngleDeg ?? 20) * Math.PI) / 180);
  const planarCos = Math.cos(((opts.planarAngleDeg ?? 4) * Math.PI) / 180);
  const weldTol = opts.weldTol ?? 1e-3;
  const maxTri = opts.maxTriangles ?? 300000;

  const triCount = index ? Math.floor(index.length / 3) : Math.floor(positions.length / 9);
  if (triCount === 0 || triCount > maxTri) return null;

  // The buffers are assumed valid (see the module doc); `?? 0` only guards the
  // out-of-range case the algorithm never reaches, and narrows to `number`.
  const vIndex = (t: number, c: number) => (index ? index[t * 3 + c] ?? 0 : t * 3 + c);
  const px = (vi: number) => positions[vi * 3] ?? 0;
  const py = (vi: number) => positions[vi * 3 + 1] ?? 0;
  const pz = (vi: number) => positions[vi * 3 + 2] ?? 0;

  // Weld coincident vertices so triangles that share an edge are detectable
  // even when the source mesh duplicates edge vertices (e.g. parsed STL).
  const weld = new Map<string, number>();
  const weldId = (vi: number) => {
    const k = `${Math.round(px(vi) / weldTol)},${Math.round(py(vi) / weldTol)},${Math.round(
      pz(vi) / weldTol
    )}`;
    let id = weld.get(k);
    if (id === undefined) {
      id = weld.size;
      weld.set(k, id);
    }
    return id;
  };

  // Per-triangle normal, centroid, area, welded corner ids (for adjacency), and
  // original corner vertex indices (for reading positions later).
  const nrm = new Float64Array(triCount * 3);
  const cen = new Float64Array(triCount * 3);
  const area = new Float64Array(triCount);
  const wv = new Int32Array(triCount * 3);
  const ov = new Int32Array(triCount * 3);
  for (let t = 0; t < triCount; t++) {
    const ai = vIndex(t, 0);
    const bi = vIndex(t, 1);
    const ci = vIndex(t, 2);
    const a: Vec3 = [px(ai), py(ai), pz(ai)];
    const b: Vec3 = [px(bi), py(bi), pz(bi)];
    const c: Vec3 = [px(ci), py(ci), pz(ci)];
    const cr = cross(sub(b, a), sub(c, a));
    const l = len(cr);
    area[t] = l / 2;
    nrm[t * 3] = l > 0 ? cr[0] / l : 0;
    nrm[t * 3 + 1] = l > 0 ? cr[1] / l : 0;
    nrm[t * 3 + 2] = l > 0 ? cr[2] / l : 0;
    cen[t * 3] = (a[0] + b[0] + c[0]) / 3;
    cen[t * 3 + 1] = (a[1] + b[1] + c[1]) / 3;
    cen[t * 3 + 2] = (a[2] + b[2] + c[2]) / 3;
    wv[t * 3] = weldId(ai);
    wv[t * 3 + 1] = weldId(bi);
    wv[t * 3 + 2] = weldId(ci);
    ov[t * 3] = ai;
    ov[t * 3 + 1] = bi;
    ov[t * 3 + 2] = ci;
  }

  // Edge → triangles sharing it (welded vertex pair as a numeric key).
  const stride = weld.size + 1;
  const edgeMap = new Map<number, number[]>();
  const edgeKey = (a: number, b: number) => (a < b ? a * stride + b : b * stride + a);
  for (let t = 0; t < triCount; t++) {
    const a = wv[t * 3] ?? 0;
    const b = wv[t * 3 + 1] ?? 0;
    const c = wv[t * 3 + 2] ?? 0;
    const pairs: Array<[number, number]> = [
      [a, b],
      [b, c],
      [c, a],
    ];
    for (const [u, w] of pairs) {
      const key = edgeKey(u, w);
      const arr = edgeMap.get(key);
      if (arr) arr.push(t);
      else edgeMap.set(key, [t]);
    }
  }

  const triNormal = (t: number): Vec3 => [
    nrm[t * 3] ?? 0,
    nrm[t * 3 + 1] ?? 0,
    nrm[t * 3 + 2] ?? 0,
  ];

  // Flood-fill smooth patches across edges whose dihedral is below the crease.
  const triToFace = new Int32Array(triCount).fill(-1);
  const faces: VisualFace[] = [];
  const stack: number[] = [];
  for (let seed = 0; seed < triCount; seed++) {
    if (triToFace[seed] !== -1) continue;
    const faceId = faces.length;
    triToFace[seed] = faceId;
    stack.length = 0;
    stack.push(seed);
    const members: number[] = [];
    while (stack.length) {
      const t = stack.pop() as number;
      members.push(t);
      const nt = triNormal(t);
      const a = wv[t * 3] ?? 0;
      const b = wv[t * 3 + 1] ?? 0;
      const c = wv[t * 3 + 2] ?? 0;
      const pairs: Array<[number, number]> = [
        [a, b],
        [b, c],
        [c, a],
      ];
      for (const [u, w] of pairs) {
        const shared = edgeMap.get(edgeKey(u, w));
        if (!shared) continue;
        for (const o of shared) {
          if (o === t || triToFace[o] !== -1) continue;
          if (dot(nt, triNormal(o)) >= cosCrease) {
            triToFace[o] = faceId;
            stack.push(o);
          }
        }
      }
    }
    faces.push(buildFace(faceId, members, nrm, cen, area, ov, px, py, pz, planarCos));
  }

  return { faces, triangleToFace: triToFace, triangleCount: triCount };
}

function buildFace(
  faceId: number,
  members: number[],
  nrm: Float64Array,
  cen: Float64Array,
  area: Float64Array,
  /** Original corner vertex indices (into the position buffer), per triangle. */
  ov: Int32Array,
  px: (vi: number) => number,
  py: (vi: number) => number,
  pz: (vi: number) => number,
  planarCos: number
): VisualFace {
  let totalArea = 0;
  const avgN: Vec3 = [0, 0, 0];
  const center: Vec3 = [0, 0, 0];
  for (const t of members) {
    const w = area[t] || 1e-9;
    totalArea += area[t] ?? 0;
    avgN[0] += (nrm[t * 3] ?? 0) * w;
    avgN[1] += (nrm[t * 3 + 1] ?? 0) * w;
    avgN[2] += (nrm[t * 3 + 2] ?? 0) * w;
    center[0] += (cen[t * 3] ?? 0) * w;
    center[1] += (cen[t * 3 + 1] ?? 0) * w;
    center[2] += (cen[t * 3 + 2] ?? 0) * w;
  }
  const wsum = totalArea || members.length * 1e-9;
  center[0] /= wsum;
  center[1] /= wsum;
  center[2] /= wsum;
  const avgNormal = norm(avgN);

  // Planar test: every member normal close to the average.
  let planar = true;
  let cov: [number, number, number, number, number, number] = [0, 0, 0, 0, 0, 0]; // m00,m01,m02,m11,m12,m22
  for (const t of members) {
    const n: Vec3 = [nrm[t * 3] ?? 0, nrm[t * 3 + 1] ?? 0, nrm[t * 3 + 2] ?? 0];
    if (dot(n, avgNormal) < planarCos) planar = false;
    const w = area[t] || 1e-9;
    cov = [
      cov[0] + w * n[0] * n[0],
      cov[1] + w * n[0] * n[1],
      cov[2] + w * n[0] * n[2],
      cov[3] + w * n[1] * n[1],
      cov[4] + w * n[1] * n[2],
      cov[5] + w * n[2] * n[2],
    ];
  }

  let kind: FaceKind = "planar";
  let axis: Vec3 | undefined;
  if (!planar) {
    const eig = symmetricEig3(cov);
    const trace = eig.values[0] + eig.values[1] + eig.values[2] || 1;
    const rSmall = eig.values[2] / trace; // smallest
    const rMid = eig.values[1] / trace;
    // Normals confined to a plane (small ⊥ component) but spread within it ⇒
    // cylinder wall; the axis is the eigenvector of the smallest eigenvalue.
    if (rSmall < 0.04 && rMid > 0.12) {
      kind = "cylindrical";
      axis = norm(eig.vectors[2]);
    } else {
      kind = "curved";
    }
  }

  // Bounding box over member vertices (original, un-welded indices).
  const min: Vec3 = [Infinity, Infinity, Infinity];
  const max: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (const t of members) {
    for (let c = 0; c < 3; c++) {
      const vi = ov[t * 3 + c] ?? 0;
      const x = px(vi);
      const y = py(vi);
      const z = pz(vi);
      if (x < min[0]) min[0] = x;
      if (y < min[1]) min[1] = y;
      if (z < min[2]) min[2] = z;
      if (x > max[0]) max[0] = x;
      if (y > max[1]) max[1] = y;
      if (z > max[2]) max[2] = z;
    }
  }

  const frameNormal = kind === "cylindrical" && axis ? axis : avgNormal;
  const tangent = perpendicular(frameNormal);
  const bitangent = norm(cross(frameNormal, tangent));

  return {
    id: `vf_${faceId}`,
    kind,
    triangleIndices: members,
    normal: avgNormal,
    center,
    area: totalArea,
    bbox: { min, max },
    axis,
    localFrame: { origin: center, normal: frameNormal, tangent, bitangent },
  };
}

/** Flatten the given triangles' vertices into a positions array for an overlay. */
export function faceHighlightPositions(
  positions: ArrayLike<number>,
  index: ArrayLike<number> | null,
  triangleIndices: number[]
): number[] {
  const out: number[] = [];
  for (const t of triangleIndices) {
    for (let c = 0; c < 3; c++) {
      const vi = index ? index[t * 3 + c] ?? 0 : t * 3 + c;
      out.push(positions[vi * 3] ?? 0, positions[vi * 3 + 1] ?? 0, positions[vi * 3 + 2] ?? 0);
    }
  }
  return out;
}
