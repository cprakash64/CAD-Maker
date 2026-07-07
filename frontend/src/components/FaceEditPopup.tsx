"use client";

import { useEffect, useRef, useState } from "react";
import {
  actionsForOperations,
  faceKindSubtitle,
  filterQuickActions,
  fmtArea,
  type EditAction,
  type MeshFaceSelection,
} from "@/lib/selection";

interface Props {
  selection: MeshFaceSelection;
  /** True while the backend is applying the edit. */
  busy?: boolean;
  /** Apply the edit for the current text + chosen quick action. */
  onApply: (instruction: string, quickAction: string | null) => void;
  onClose: () => void;
}

/**
 * Floating contextual editor for a selected CAD face. Positioning/tracking is
 * handled by drei's <Html> in Viewer3D (anchored to the face centroid, so it
 * stays connected as the camera moves) — this component owns only the popup UI
 * and edit dispatch. Styled to match LunaiCAD's dark/gold glass surfaces.
 */
export default function FaceEditPopup({ selection, busy = false, onApply, onClose }: Props) {
  const [instruction, setInstruction] = useState("");
  const [quickAction, setQuickAction] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Reset + focus whenever a different face is selected.
  useEffect(() => {
    setInstruction("");
    setQuickAction(null);
    inputRef.current?.focus();
  }, [selection.timestamp]);

  function pickAction(a: EditAction) {
    setInstruction(a.prompt);
    setQuickAction(a.key);
    inputRef.current?.focus();
  }

  function apply() {
    const value = instruction.trim();
    if (!value || busy) return;
    onApply(value, quickAction);
  }

  // Chips: faces use the fixed quick-action set (filtered by backend advice);
  // edges/holes/bodies render actions straight from their advertised operations.
  const kind = selection.selection_kind ?? "face";
  const chips: EditAction[] =
    kind === "face"
      ? filterQuickActions(selection.allowed_operations)
      : actionsForOperations(selection.allowed_operations);
  const areaText = fmtArea(selection.area_mm2);
  const title = `Edit this ${kind}`;
  // Prefer the backend's semantic label; otherwise fall back to a kind subtitle.
  const subtitle =
    selection.backend_label ??
    (kind === "face" ? faceKindSubtitle(selection.face_kind) : `${kind} selected`);

  return (
    <div
      // Sit above the anchor point; keyboard events stay inside the popup so
      // they never reach the viewer's orbit controls.
      className="pointer-events-auto w-64 -translate-x-1/2 -translate-y-[calc(100%+16px)] overflow-hidden rounded-xl border border-[color:var(--glass-border-strong)] bg-panel/95 shadow-glass backdrop-blur-xl"
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Escape") onClose();
      }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 border-b border-edge px-3.5 pb-2.5 pt-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold capitalize text-slate-100">{title}</h3>
          <p className="mt-0.5 truncate text-[11px] text-accent/90">
            {subtitle}
            {areaText ? <span className="text-slate-500"> · {areaText}</span> : null}
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="-mr-1 grid h-6 w-6 shrink-0 place-items-center rounded-md text-slate-500 transition-colors hover:bg-raised hover:text-slate-200"
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
            <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      <div className="space-y-2.5 p-3.5">
        <input
          ref={inputRef}
          className="input text-xs"
          placeholder="Describe your edit…"
          value={instruction}
          onChange={(e) => {
            setInstruction(e.target.value);
            setQuickAction(null); // free-typing clears the chosen chip
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") apply();
          }}
          disabled={busy}
        />

        {/* Quick action chips — or a calm empty state when nothing is supported. */}
        {chips.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {chips.map((a) => (
              <button
                key={a.key}
                type="button"
                onClick={() => pickAction(a)}
                title={a.prompt}
                disabled={busy}
                className={`rounded-md border px-2 py-0.5 text-[11px] transition-colors disabled:opacity-50 ${
                  quickAction === a.key
                    ? "border-accent/70 bg-accent/15 text-accent"
                    : "border-edge text-slate-300 hover:border-accent/50 hover:text-slate-100"
                }`}
              >
                {a.label}
              </button>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-slate-400">
            No safe parametric edits are available for this selection yet.
          </p>
        )}

        {/* Actions */}
        <div className="flex items-center gap-2 pt-0.5">
          <button
            className="btn-primary flex-1 py-1.5 text-xs"
            onClick={apply}
            disabled={busy || !instruction.trim()}
          >
            {busy ? "Applying edit…" : "Apply edit"}
          </button>
          <button className="btn-ghost btn-sm text-xs" onClick={onClose} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
