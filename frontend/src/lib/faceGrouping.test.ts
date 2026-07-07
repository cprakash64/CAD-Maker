/** Phase 2 visual-face grouping: adjacency flood-fill, planar/cylindrical
 *  classification, coplanar merging, crease splitting, and the size fallback.
 *  Pure array math — no three.js. */
import { describe, expect, it } from "vitest";
import { buildFaceGroups, faceHighlightPositions, symmetricEig3 } from "./faceGrouping";

// --- test mesh builders ----------------------------------------------------

/** Unit cube: 8 verts, 12 triangles (2 per face), consistent outward winding. */
function unitCube() {
  const positions = [
    0, 0, 0, // 0
    1, 0, 0, // 1
    1, 1, 0, // 2
    0, 1, 0, // 3
    0, 0, 1, // 4
    1, 0, 1, // 5
    1, 1, 1, // 6
    0, 1, 1, // 7
  ];
  const index = [
    0, 3, 2, 0, 2, 1, // bottom -z
    4, 5, 6, 4, 6, 7, // top +z
    0, 1, 5, 0, 5, 4, // front -y
    3, 7, 6, 3, 6, 2, // back +y
    0, 4, 7, 0, 7, 3, // left -x
    1, 2, 6, 1, 6, 5, // right +x
  ];
  return { positions, index };
}

/** Two coplanar quads (z=0) sharing an edge → should merge into one face. */
function coplanarStrip() {
  const positions = [
    0, 0, 0, 1, 0, 0, 2, 0, 0, // 0,1,2
    0, 1, 0, 1, 1, 0, 2, 1, 0, // 3,4,5
  ];
  const index = [0, 1, 4, 0, 4, 3, 1, 2, 5, 1, 5, 4];
  return { positions, index };
}

/** Flat quad (z=0) + vertical quad (y=1) meeting at 90° along a shared edge. */
function creaseL() {
  const positions = [
    0, 0, 0, // 0
    1, 0, 0, // 1
    1, 1, 0, // 2  (shared)
    0, 1, 0, // 3  (shared)
    1, 1, 1, // 4
    0, 1, 1, // 5
  ];
  const index = [
    0, 1, 2, 0, 2, 3, // flat, +z
    3, 2, 4, 3, 4, 5, // vertical, +y
  ];
  return { positions, index };
}

/** Open cylinder wall, N segments, radius R, height H (no caps). */
function cylinderWall(segments: number, R = 10, H = 20) {
  const positions: number[] = [];
  for (let i = 0; i < segments; i++) {
    const a = (i / segments) * Math.PI * 2;
    positions.push(Math.cos(a) * R, Math.sin(a) * R, 0);
    positions.push(Math.cos(a) * R, Math.sin(a) * R, H);
  }
  const index: number[] = [];
  for (let i = 0; i < segments; i++) {
    const b0 = (i * 2) % (segments * 2);
    const t0 = b0 + 1;
    const b1 = (((i + 1) % segments) * 2) % (segments * 2);
    const t1 = b1 + 1;
    index.push(b0, b1, t1, b0, t1, t0); // outward-consistent winding
  }
  return { positions, index };
}

// --- tests -----------------------------------------------------------------

describe("buildFaceGroups — planar boxes", () => {
  it("splits a cube into 6 planar faces of 2 triangles each", () => {
    const { positions, index } = unitCube();
    const g = buildFaceGroups(positions, index)!;
    expect(g).not.toBeNull();
    expect(g.faces).toHaveLength(6);
    for (const f of g.faces) {
      expect(f.kind).toBe("planar");
      expect(f.triangleIndices).toHaveLength(2);
    }
    // Every triangle is mapped to exactly one in-range face.
    expect(g.triangleToFace).toHaveLength(12);
    for (const fi of g.triangleToFace) {
      expect(fi).toBeGreaterThanOrEqual(0);
      expect(fi).toBeLessThan(6);
    }
  });

  it("merges coplanar connected quads into a single face", () => {
    const { positions, index } = coplanarStrip();
    const g = buildFaceGroups(positions, index)!;
    expect(g.faces).toHaveLength(1);
    expect(g.faces[0]!.kind).toBe("planar");
    expect(g.faces[0]!.triangleIndices).toHaveLength(4);
  });

  it("splits faces that meet at a 90° crease", () => {
    const { positions, index } = creaseL();
    const g = buildFaceGroups(positions, index)!;
    expect(g.faces).toHaveLength(2);
    expect(g.faces.every((f) => f.kind === "planar")).toBe(true);
  });
});

describe("buildFaceGroups — curved surfaces", () => {
  it("groups a tessellated cylinder wall into one cylindrical face", () => {
    const { positions, index } = cylinderWall(48);
    const g = buildFaceGroups(positions, index)!;
    expect(g.faces).toHaveLength(1);
    const f = g.faces[0]!;
    expect(f.kind).toBe("cylindrical");
    // Axis should be ~Z.
    expect(Math.abs((f.axis ?? [0, 0, 0])[2])).toBeGreaterThan(0.9);
    // Area ≈ 2πRH (within tessellation error).
    expect(f.area).toBeGreaterThan(2 * Math.PI * 10 * 20 * 0.95);
  });
});

describe("buildFaceGroups — guards + fallback", () => {
  it("returns null above the triangle cap (Phase 1 fallback path)", () => {
    const { positions, index } = unitCube();
    expect(buildFaceGroups(positions, index, { maxTriangles: 1 })).toBeNull();
  });

  it("returns null for an empty mesh", () => {
    expect(buildFaceGroups([], [])).toBeNull();
  });
});

describe("faceHighlightPositions", () => {
  it("flattens selected triangles to 9 numbers each", () => {
    const { positions, index } = unitCube();
    const out = faceHighlightPositions(positions, index, [0, 1]);
    expect(out).toHaveLength(2 * 9);
  });
});

describe("symmetricEig3", () => {
  it("returns eigenvalues sorted descending for a diagonal matrix", () => {
    const { values } = symmetricEig3([3, 0, 0, 2, 0, 1]);
    expect(values[0]).toBeCloseTo(3, 6);
    expect(values[1]).toBeCloseTo(2, 6);
    expect(values[2]).toBeCloseTo(1, 6);
  });
});
