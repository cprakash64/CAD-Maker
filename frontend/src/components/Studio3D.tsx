"use client";

import { useEffect, useRef, useState } from "react";
import ViewToolbar, { type DisplayMode, type ViewName } from "./ViewToolbar";
// Import directly (NOT next/dynamic) so React refs forward to Viewer3D — the
// previous dynamic() wrapper dropped the ref, so view buttons and circle-edit
// projection never reached the viewer. Studio3D itself is dynamically imported
// (ssr:false) by the studio page, which keeps Three.js off the server.
import Viewer3D, { type ViewerHandle } from "./Viewer3D";
import FaceEditPopup from "./FaceEditPopup";
import {
  buildFaceEditPayload,
  buildMeshFaceSelection,
  type FaceEditPayload,
  type FaceHit,
  type MeshFaceSelection,
  type SelectableBody,
  type SelectableEdge,
  type SelectableFace,
  type SelectableHole,
  type SelectionMode,
} from "@/lib/selection";
import type { FeatureInfo, PreviewMesh } from "@/lib/types";

const SELECT_MODES: { key: SelectionMode; label: string; title: string }[] = [
  { key: "auto", label: "Auto", title: "Auto — pick the most precise geometry under the cursor" },
  { key: "face", label: "Face", title: "Select faces" },
  { key: "edge", label: "Edge", title: "Select edges" },
  { key: "hole", label: "Hole", title: "Select holes" },
  { key: "body", label: "Body", title: "Select the body" },
];

export interface SelectedFeature {
  entity_type: string;
  entity_id: string;
  label: string;
}

interface Props {
  mesh: PreviewMesh | null;
  features: FeatureInfo[];
  /** Backend semantic selectable geometry (Phase 5/6). */
  selectableFaces?: SelectableFace[];
  selectableHoles?: SelectableHole[];
  selectableEdges?: SelectableEdge[];
  selectableBodies?: SelectableBody[];
  onSelect: (f: SelectedFeature | null) => void;
  /** Design / part id, embedded in the mesh-face selection object. */
  objectId?: string | null;
  /** Notifies the page of the current mesh-face selection (for the right panel). */
  onFaceSelect?: (sel: MeshFaceSelection | null) => void;
  /** Applies a localized face edit via the backend; resolves with the outcome. */
  onApplyFaceEdit?: (payload: FaceEditPayload) => Promise<{ ok: boolean; message: string }>;
  materialColor?: string;
}

export default function Studio3D({
  mesh,
  features,
  selectableFaces,
  selectableHoles,
  selectableEdges,
  selectableBodies,
  onSelect,
  objectId,
  onFaceSelect,
  onApplyFaceEdit,
  materialColor,
}: Props) {
  const viewerRef = useRef<ViewerHandle>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const [activeView, setActiveView] = useState<ViewName>("iso");
  const [projection, setProjection] = useState<"perspective" | "orthographic">("perspective");
  const [showGrid, setShowGrid] = useState(true);
  const [showAxes, setShowAxes] = useState(true);
  const [displayMode, setDisplayMode] = useState<DisplayMode>("shaded");
  const [measureMode, setMeasureMode] = useState(false);
  const [dark, setDark] = useState(true);
  const [circleMode, setCircleMode] = useState(false);
  const [selectionMode, setSelectionMode] = useState<SelectionMode>("auto");

  // Follow the OS light/dark preference so the viewer chrome matches the app.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => setDark(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);
  const [circle, setCircle] = useState<{ x: number; y: number; r: number } | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const [marker, setMarker] = useState<{ x: number; y: number; label: string } | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [debug, setDebug] = useState(false);
  const [faceSel, setFaceSel] = useState<MeshFaceSelection | null>(null);
  const [faceEditBusy, setFaceEditBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showToast(message: string) {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setToast(message);
    toastTimer.current = setTimeout(() => setToast(null), 4200);
  }
  useEffect(
    () => () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    },
    []
  );

  // Click-to-select faces is the default click behavior; it stands down while
  // the ruler or circle-edit overlay owns the pointer (they stay exclusive).
  const selectEnabled = !measureMode && !circleMode;

  function clearFace() {
    setFaceSel(null);
    onFaceSelect?.(null);
    viewerRef.current?.clearSelection();
  }

  function handleFacePick(hit: FaceHit | null) {
    if (!hit) {
      setFaceSel(null);
      onFaceSelect?.(null);
      return;
    }
    const sel = buildMeshFaceSelection(hit, objectId ?? null);
    setFaceSel(sel);
    onFaceSelect?.(sel);
    setNotFound(false);
    // Bridge to the existing circle-edit backend: the mapped bbox face id is a
    // valid feature the constrained edit pipeline already understands.
    if (sel.feature_id) {
      onSelect({
        entity_type: "face",
        entity_id: sel.feature_id,
        label: sel.feature_label ?? "face",
      });
    }
  }

  // A new part must never inherit the previous part's face selection.
  useEffect(() => {
    setFaceSel(null);
    onFaceSelect?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mesh]);

  // Phase 4: "Apply edit" sends the localized face-edit payload to the backend,
  // which regenerates real CAD. On success the viewer refreshes with the new
  // geometry; failures surface the backend reason and leave the model unchanged.
  async function handleFaceEditApply(instruction: string, quickAction: string | null) {
    if (!faceSel) return;
    const payload = buildFaceEditPayload(faceSel, instruction, quickAction);
    // eslint-disable-next-line no-console
    console.info("[LunaiCAD] Face edit payload:", payload);
    if (!onApplyFaceEdit) {
      showToast("Face edit payload ready. Backend support coming next.");
      clearFace();
      return;
    }
    setFaceEditBusy(true);
    try {
      const res = await onApplyFaceEdit(payload);
      if (res.ok) {
        showToast(res.message || "Edit applied.");
        clearFace(); // new geometry arrives via the mesh prop; drop the selection
      } else {
        showToast(res.message || "Could not apply this edit safely.");
      }
    } finally {
      setFaceEditBusy(false);
    }
  }

  const facePopup =
    selectEnabled && faceSel ? (
      <FaceEditPopup
        selection={faceSel}
        busy={faceEditBusy}
        onApply={handleFaceEditApply}
        onClose={clearFace}
      />
    ) : null;

  function localPos(e: React.MouseEvent) {
    const rect = overlayRef.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function onDown(e: React.MouseEvent) {
    if (!circleMode) return;
    const p = localPos(e);
    dragStart.current = p;
    setCircle({ x: p.x, y: p.y, r: 0 });
  }
  function onMove(e: React.MouseEvent) {
    if (!circleMode || !dragStart.current) return;
    const p = localPos(e);
    const r = Math.hypot(p.x - dragStart.current.x, p.y - dragStart.current.y);
    setCircle({ x: dragStart.current.x, y: dragStart.current.y, r });
  }
  function pickFeature(circ: { x: number; y: number; r: number }) {
    if (!viewerRef.current) return null;
    const projected = viewerRef.current.projectPoints(features.map((f) => f.anchor));
    // "body" is the whole part — not a circle-selectable feature.
    const FALLBACK_PX = 120;
    let inside: { f: FeatureInfo; score: number; sx: number; sy: number } | null = null;
    let nearest: { f: FeatureInfo; d: number; sx: number; sy: number } | null = null;
    projected.forEach((pt, i) => {
      const f = features[i];
      if (!pt || !f || f.type === "body") return;
      const d = Math.hypot(pt[0] - circ.x, pt[1] - circ.y);
      // Prefer concrete features (holes/flanges/bolt patterns) over generic faces.
      const penalty = f.type === "face" ? 40 : f.type === "edge" ? 20 : 0;
      if (d <= circ.r + 10 && (!inside || d + penalty < inside.score)) {
        inside = { f, score: d + penalty, sx: pt[0], sy: pt[1] };
      }
      if (!nearest || d < nearest.d) nearest = { f, d, sx: pt[0], sy: pt[1] };
    });
    if (inside) return inside as { f: FeatureInfo; sx: number; sy: number };
    // Fallback: nearest editable feature within a threshold of the circle center.
    if (nearest && (nearest as { d: number }).d <= FALLBACK_PX) {
      return nearest as { f: FeatureInfo; sx: number; sy: number };
    }
    return null;
  }

  function onUp() {
    if (!circleMode || !circle || !viewerRef.current) {
      dragStart.current = null;
      return;
    }
    dragStart.current = null;
    const hit = pickFeature(circle);
    setCircle(null);
    if (hit) {
      setNotFound(false);
      setMarker({ x: hit.sx, y: hit.sy, label: hit.f.label });
      onSelect({ entity_type: hit.f.type, entity_id: hit.f.id, label: hit.f.label });
    } else {
      setMarker(null);
      setNotFound(true);
      onSelect(null);
    }
  }

  return (
    <div className="relative h-full w-full">
      <Viewer3D
        ref={viewerRef}
        mesh={mesh}
        materialColor={materialColor}
        showGrid={showGrid}
        showAxes={showAxes}
        orthographic={projection === "orthographic"}
        dark={dark}
        displayMode={displayMode}
        measureMode={measureMode}
        className="absolute inset-0 h-full w-full overflow-hidden rounded-xl border border-edge bg-viewport"
        selectEnabled={selectEnabled}
        selectionMode={selectionMode}
        selectableFaces={selectableFaces}
        selectableHoles={selectableHoles}
        selectableEdges={selectableEdges}
        selectableBodies={selectableBodies}
        onFacePick={handleFacePick}
        faceOverlay={facePopup}
      />

      {/* Overlay captures circle gestures only in circle mode. */}
      <div
        ref={overlayRef}
        className={`absolute inset-0 ${circleMode ? "cursor-crosshair" : "pointer-events-none"}`}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={() => (dragStart.current = null)}
      >
        {circle && (
          <svg className="h-full w-full">
            <circle
              cx={circle.x}
              cy={circle.y}
              r={Math.max(circle.r, 1)}
              fill="rgba(214,170,77,0.16)"
              stroke="#d6aa4d"
              strokeWidth={2}
            />
          </svg>
        )}
        {marker && !circle && (
          <div
            className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded bg-accent px-1.5 py-0.5 text-[10px] font-semibold text-on-accent"
            style={{ left: marker.x, top: marker.y }}
          >
            {marker.label}
            <span className="absolute left-1/2 top-full h-2 w-2 -translate-x-1/2 -translate-y-1 rotate-45 bg-accent" />
          </div>
        )}
        {/* Dev-only: show projected feature anchors to debug selection. */}
        {debug &&
          viewerRef.current
            ?.projectPoints(features.map((f) => f.anchor))
            .map((pt, i) =>
              pt && features[i] && features[i]!.type !== "body" ? (
                <div
                  key={features[i]!.id}
                  className="pointer-events-none absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-emerald-400"
                  style={{ left: pt[0], top: pt[1] }}
                  title={features[i]!.id}
                />
              ) : null
            )}
      </div>

      {/* Selection-mode control (top-left) — clears the current pick on change. */}
      <div className="pointer-events-none absolute left-3 top-3 z-10">
        <div className="pointer-events-auto inline-flex items-center gap-0.5 rounded-lg border border-[color:var(--glass-border)] bg-panel/85 p-0.5 shadow-glass backdrop-blur-xl">
          {SELECT_MODES.map((m) => (
            <button
              key={m.key}
              title={m.title}
              onClick={() => {
                setSelectionMode(m.key);
                clearFace();
              }}
              className={`rounded-md px-2 py-1 text-[11px] transition-colors ${
                selectionMode === m.key && !measureMode && !circleMode
                  ? "bg-accent/15 text-accent"
                  : "text-slate-400 hover:text-slate-100"
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {/* Floating toolbar — keeps all vertical space for the model. */}
      <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex flex-wrap items-start justify-end gap-2">
        <div className="pointer-events-auto flex flex-wrap items-center justify-end gap-1.5">
          <ViewToolbar
            active={activeView}
            onSelect={(v) => {
              setActiveView(v);
              viewerRef.current?.setView(v);
            }}
            onHome={() => {
              setActiveView("iso");
              viewerRef.current?.setView("iso");
            }}
            onCapturePng={() => viewerRef.current?.capturePng()}
            projection={projection}
            onToggleProjection={() =>
              setProjection((p) => (p === "perspective" ? "orthographic" : "perspective"))
            }
            showGrid={showGrid}
            onToggleGrid={() => setShowGrid((g) => !g)}
            showAxes={showAxes}
            onToggleAxes={() => setShowAxes((a) => !a)}
            displayMode={displayMode}
            onSelectDisplayMode={setDisplayMode}
            measureMode={measureMode}
            onToggleMeasure={() => {
              setMeasureMode((m) => !m);
              setCircleMode(false); // measure and circle-edit are exclusive
              clearFace(); // face-select stands down while measuring
            }}
          />
          {measureMode && (
            <button
              className="rounded-lg border border-[color:var(--glass-border)] bg-panel/85 px-2.5 py-1.5 text-xs text-slate-300 shadow-glass backdrop-blur-xl transition-colors hover:text-slate-100"
              onClick={() => viewerRef.current?.clearMeasurements()}
              title="Clear all measurements"
            >
              Clear
            </button>
          )}
          {process.env.NODE_ENV !== "production" && (
            <button
              className={`rounded-lg border px-2 py-1.5 text-xs shadow-glass backdrop-blur-xl ${
                debug
                  ? "border-accent/60 bg-accent/15 text-accent"
                  : "border-[color:var(--glass-border)] bg-panel/85 text-slate-400 hover:text-slate-100"
              }`}
              onClick={() => setDebug((d) => !d)}
              title="Dev: show feature anchors"
            >
              ⌖
            </button>
          )}
          <button
            className={`rounded-lg border px-2.5 py-1.5 text-xs shadow-glass backdrop-blur-xl transition-colors ${
              circleMode
                ? "border-accent/60 bg-accent/15 text-accent"
                : "border-[color:var(--glass-border)] bg-panel/85 text-slate-300 hover:text-slate-100"
            }`}
            onClick={() => {
              setCircleMode((m) => !m);
              setMeasureMode(false); // measure and circle-edit are exclusive
              setCircle(null);
              setNotFound(false);
              clearFace(); // face-select stands down while circling
            }}
            title="Circle Edit — draw a circle over a feature to select it"
          >
            ◯ Circle{circleMode ? " · on" : ""}
          </button>
        </div>
      </div>

      {/* Floating status hints — bottom-left, out of the way. */}
      {(circleMode || notFound || measureMode) && (
        <div className="pointer-events-none absolute bottom-3 left-3 z-10 max-w-[18rem]">
          {notFound && (
            <p className="rounded-lg border border-amber-500/30 bg-panel/90 px-3 py-1.5 text-[11px] text-amber-300 shadow-glass backdrop-blur-xl">
              No editable feature there — try circling a hole, edge or flange.
            </p>
          )}
          {measureMode && (
            <p className="rounded-lg border border-[color:var(--glass-border)] bg-panel/90 px-3 py-1.5 text-[11px] text-slate-300 shadow-glass backdrop-blur-xl">
              Measure on — click two points on the model; labels show mm.
            </p>
          )}
          {circleMode && !notFound && (
            <p className="rounded-lg border border-[color:var(--glass-border)] bg-panel/90 px-3 py-1.5 text-[11px] text-slate-300 shadow-glass backdrop-blur-xl">
              Circle Edit on — drag over a hole, edge, flange or face.
            </p>
          )}
        </div>
      )}

      {/* Non-blocking toast (bottom-center) — Phase 3 face-edit payload notice. */}
      {toast && (
        <div className="pointer-events-none absolute inset-x-0 bottom-4 z-20 flex justify-center px-4">
          <div className="max-w-sm rounded-lg border border-[color:var(--glass-border-strong)] bg-panel/95 px-3.5 py-2 text-center text-xs text-slate-100 shadow-glass backdrop-blur-xl">
            {toast}
          </div>
        </div>
      )}
    </div>
  );
}
