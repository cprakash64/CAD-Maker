"use client";

import { useRef, useState } from "react";
import ViewToolbar, { type ViewName } from "./ViewToolbar";
// Import directly (NOT next/dynamic) so React refs forward to Viewer3D — the
// previous dynamic() wrapper dropped the ref, so view buttons and circle-edit
// projection never reached the viewer. Studio3D itself is dynamically imported
// (ssr:false) by the studio page, which keeps Three.js off the server.
import Viewer3D, { type PickedEntity, type ViewerHandle } from "./Viewer3D";
import type { FeatureInfo, PreviewMesh } from "@/lib/types";

export interface SelectedFeature {
  entity_type: string;
  entity_id: string;
  label: string;
}

interface Props {
  mesh: PreviewMesh | null;
  features: FeatureInfo[];
  onSelect: (f: SelectedFeature | null) => void;
  materialColor?: string;
  viewerClassName?: string;
}

export default function Studio3D({ mesh, features, onSelect, materialColor, viewerClassName }: Props) {
  const viewerRef = useRef<ViewerHandle>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const [activeView, setActiveView] = useState<ViewName>("iso");
  const [circleMode, setCircleMode] = useState(false);
  const [circle, setCircle] = useState<{ x: number; y: number; r: number } | null>(null);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const [marker, setMarker] = useState<{ x: number; y: number; label: string } | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [debug, setDebug] = useState(false);

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
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ViewToolbar
          active={activeView}
          onSelect={(v) => {
            setActiveView(v);
            viewerRef.current?.setView(v);
          }}
          onCapturePng={() => viewerRef.current?.capturePng()}
        />
        <div className="flex items-center gap-1">
          {process.env.NODE_ENV !== "production" && (
            <button
              className={`rounded px-2 py-1 text-xs ${
                debug ? "bg-emerald-600 text-white" : "border border-edge text-slate-400"
              }`}
              onClick={() => setDebug((d) => !d)}
              title="Dev: show feature anchors"
            >
              ⌖ anchors
            </button>
          )}
          <button
            className={`rounded px-2.5 py-1 text-xs ${
              circleMode ? "bg-accent text-white" : "border border-edge text-slate-300"
            }`}
            onClick={() => {
              setCircleMode((m) => !m);
              setCircle(null);
              setNotFound(false);
            }}
            title="Draw a circle over a feature to select it"
          >
            ◯ Circle Edit{circleMode ? " (on)" : ""}
          </button>
        </div>
      </div>

      <div className="relative">
        <Viewer3D
          ref={viewerRef}
          mesh={mesh}
          materialColor={materialColor}
          className={viewerClassName}
          onPick={(p: PickedEntity) => onSelect({ entity_type: p.type, entity_id: p.id, label: p.label })}
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
                fill="rgba(91,140,255,0.15)"
                stroke="#5b8cff"
                strokeWidth={2}
              />
            </svg>
          )}
          {marker && !circle && (
            <div
              className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded bg-accent px-1.5 py-0.5 text-[10px] text-white"
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
      </div>

      {notFound && (
        <p className="text-[11px] text-amber-300">
          No editable feature found in that area — try circling a hole, edge or flange.
        </p>
      )}
      {circleMode && (
        <p className="text-[11px] text-slate-400">
          Circle Edit on — drag a circle over a hole, edge, flange or face to select it.
        </p>
      )}
    </div>
  );
}
