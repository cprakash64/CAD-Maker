"use client";

import { Canvas, useThree, type ThreeEvent } from "@react-three/fiber";
import { Grid, GizmoHelper, GizmoViewport, OrbitControls } from "@react-three/drei";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from "react";
import * as THREE from "three";
import type { PreviewMesh } from "@/lib/types";
import type { ViewName } from "./ViewToolbar";

export interface ViewerHandle {
  setView: (view: ViewName) => void;
  capturePng: () => void;
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
  top: [0, 1, 0.0001], // look down model Z
  front: [0, 0, 1], // look along model Y
  right: [1, 0, 0], // look along model X
  left: [-1, 0, 0],
  iso: [1, 0.8, 1],
};

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
  color = "#9aa3ad",
  metalness = 0.45,
  roughness = 0.5,
}: {
  mesh: PreviewMesh;
  centerRef: React.MutableRefObject<THREE.Vector3>;
  radiusRef: React.MutableRefObject<number>;
  onPick?: (e: PickedEntity) => void;
  color?: string;
  metalness?: number;
  roughness?: number;
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
    centerRef.current.copy(c); // pre-translation center (what we subtracted)
    radiusRef.current = geom.boundingSphere?.radius ?? 100;
    return geom;
  }, [mesh, centerRef, radiusRef]);

  function handleClick(e: ThreeEvent<MouseEvent>) {
    if (!onPick || !e.face) return;
    e.stopPropagation();
    onPick({ type: "face", id: faceLabel(e.face.normal), label: `${faceLabel(e.face.normal)} face` });
  }

  return (
    <mesh geometry={geometry} castShadow receiveShadow onClick={handleClick}>
      <meshStandardMaterial color={color} metalness={metalness} roughness={roughness} />
    </mesh>
  );
}

function Rig({
  handleRef,
  centerRef,
  radiusRef,
}: {
  handleRef: React.Ref<ViewerHandle>;
  centerRef: React.MutableRefObject<THREE.Vector3>;
  radiusRef: React.MutableRefObject<number>;
}) {
  const { camera, gl, scene, controls, size } = useThree();

  function place(dir: [number, number, number]) {
    const r = radiusRef.current || 100;
    const persp = camera as THREE.PerspectiveCamera;
    const fov = (persp.fov ?? 45) * (Math.PI / 180);
    const dist = (r / Math.sin(fov / 2)) * 1.25;
    const v = new THREE.Vector3(...dir).normalize().multiplyScalar(dist);
    camera.position.copy(v);
    camera.up.set(0, 1, 0);
    camera.lookAt(0, 0, 0);
    persp.near = Math.max(0.1, dist - r * 4);
    persp.far = dist + r * 8;
    persp.updateProjectionMatrix();
    const oc = controls as unknown as { target?: THREE.Vector3; update?: () => void };
    if (oc?.target) {
      oc.target.set(0, 0, 0);
      oc.update?.();
    }
  }

  // Fit once when the model (radius) first becomes available.
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
    projectPoints(pts) {
      return pts.map((p) => {
        const v = transformAnchor(p, centerRef.current).project(camera);
        if (v.z > 1) return null; // behind camera / clipped
        return [((v.x + 1) / 2) * size.width, ((1 - v.y) / 2) * size.height] as [number, number];
      });
    },
  }));

  return null;
}

interface Props {
  mesh: PreviewMesh | null;
  onPick?: (e: PickedEntity) => void;
  materialColor?: string;
  className?: string;
}

const Viewer3D = forwardRef<ViewerHandle, Props>(function Viewer3D(
  { mesh, onPick, materialColor, className }, ref
) {
  const centerRef = useRef(new THREE.Vector3());
  const radiusRef = useRef(100);

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
        <Canvas camera={{ position: [120, 90, 120], fov: 45 }} shadows gl={{ preserveDrawingBuffer: true }}>
          <ambientLight intensity={0.55} />
          <directionalLight position={[80, 120, 60]} intensity={1.05} castShadow />
          <directionalLight position={[-60, 40, -80]} intensity={0.35} />
          <PartMesh
            mesh={mesh}
            centerRef={centerRef}
            radiusRef={radiusRef}
            onPick={onPick}
            color={materialColor}
          />
          <Grid
            args={[800, 800]}
            cellSize={10}
            cellThickness={0.5}
            sectionSize={50}
            sectionColor="#363a42"
            cellColor="#202329"
            position={[0, -0.01, 0]}
            infiniteGrid
            fadeDistance={900}
          />
          <OrbitControls makeDefault enableDamping />
          <GizmoHelper alignment="bottom-right" margin={[60, 60]}>
            <GizmoViewport labelColor="white" axisHeadScale={0.9} />
          </GizmoHelper>
          <Rig handleRef={ref} centerRef={centerRef} radiusRef={radiusRef} />
        </Canvas>
      )}
    </div>
  );
});

export default Viewer3D;
