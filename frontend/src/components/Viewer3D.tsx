"use client";

import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber";
import { Edges, Grid, GizmoHelper, GizmoViewport, Html, Line, OrbitControls } from "@react-three/drei";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { PreviewMesh } from "@/lib/types";
import type { DisplayMode, ViewName } from "./ViewToolbar";

export interface ViewerHandle {
  setView: (view: ViewName) => void;
  capturePng: () => void;
  clearMeasurements: () => void;
  /** Project model-space anchors to screen pixels (null if behind camera). */
  projectPoints: (pts: [number, number, number][]) => ([number, number] | null)[];
}

export interface PickedEntity {
  type: string;
  id: string;
  label: string;
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
  onPick,
  onMeasure,
  measureMode,
  mode,
  color,
  edgeColor,
  wireColor,
}: {
  mesh: PreviewMesh;
  centerRef: React.MutableRefObject<THREE.Vector3>;
  radiusRef: React.MutableRefObject<number>;
  onPick?: (e: PickedEntity) => void;
  onMeasure?: (p: THREE.Vector3) => void;
  measureMode?: boolean;
  mode: DisplayMode;
  color: string;
  edgeColor: string;
  wireColor: string;
}) {
  const geometry = useMemo(() => {
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
    return geom;
  }, [mesh, centerRef, radiusRef]);

  function handleClick(e: ThreeEvent<MouseEvent>) {
    if (measureMode && onMeasure && e.point) {
      e.stopPropagation();
      onMeasure(e.point.clone());
      return;
    }
    if (!onPick || !e.face) return;
    e.stopPropagation();
    onPick({ type: "face", id: faceLabel(e.face.normal), label: `${faceLabel(e.face.normal)} face` });
  }

  const wireframe = mode === "wireframe";
  const showEdges = mode === "edges" || mode === "technical" || mode === "shaded";
  const ghost = mode === "technical";

  return (
    <mesh geometry={geometry} castShadow={!ghost} receiveShadow={!ghost} onClick={handleClick}>
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
}: {
  handleRef: React.Ref<ViewerHandle>;
  centerRef: React.MutableRefObject<THREE.Vector3>;
  radiusRef: React.MutableRefObject<number>;
  onClear: () => void;
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
  onPick?: (e: PickedEntity) => void;
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
    onPick,
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
  const [pending, setPending] = useState<THREE.Vector3 | null>(null);
  const [measurements, setMeasurements] = useState<Measurement[]>([]);

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
  // Turning the ruler off immediately hides every measurement + temp point.
  useEffect(() => {
    if (!measureMode) clear();
  }, [measureMode]);
  // Switching parts must never leave stale measurements anchored to old geometry.
  useEffect(() => {
    clear();
  }, [mesh]);

  return (
    <div
      className={
        className ??
        "relative h-[520px] w-full overflow-hidden rounded-lg border border-edge bg-viewport lg:h-[640px]"
      }
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
        >
          <color attach="background" args={[pal.bg]} />
          <ambientLight intensity={dark ? 0.55 : 0.75} />
          <directionalLight position={[80, 120, 60]} intensity={dark ? 1.05 : 1.2} castShadow />
          <directionalLight position={[-60, 40, -80]} intensity={0.35} />
          <PartMesh
            mesh={mesh}
            centerRef={centerRef}
            radiusRef={radiusRef}
            onPick={onPick}
            onMeasure={addMeasurePoint}
            measureMode={measureMode}
            mode={displayMode}
            color={partColor}
            edgeColor={pal.edge}
            wireColor={pal.wire}
          />
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
          <Rig handleRef={ref} centerRef={centerRef} radiusRef={radiusRef} onClear={clear} />
        </Canvas>
      )}
    </div>
  );
});

export default Viewer3D;
