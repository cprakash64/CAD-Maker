"use client";

import { useState } from "react";
import type { SelectedFeature } from "./Studio3D";
import {
  faceKindLabel,
  fmtArea,
  fmtVec,
  type MeshFaceSelection,
} from "@/lib/selection";

interface Props {
  selected: SelectedFeature | null;
  /** Mesh-face selection from click-to-select (Phase 1). */
  faceSelection?: MeshFaceSelection | null;
  onApply: (instruction: string) => Promise<void>;
  busy: boolean;
}

const EXAMPLES: Record<string, string> = {
  hole: "make this 8 mm  ·  make this counterbored",
  edge: "round this edge 3mm  ·  chamfer this edge",
  face: "add a hole here  ·  add vents here",
  flange: "make this flange thicker",
  bolt_pattern: "increase the bolt holes to 18 mm",
  body: "round the edges",
};

export default function CircleEditPanel({ selected, faceSelection, onApply, busy }: Props) {
  const [instruction, setInstruction] = useState("");
  // A clicked mesh face still yields a selectable feature id via `selected`, so
  // Apply stays enabled whenever either selection is present.
  const canApply = !!selected || !!faceSelection;

  async function apply() {
    if (!instruction.trim() || busy || !canApply) return;
    await onApply(instruction.trim());
    setInstruction("");
  }

  return (
    <div className="card p-4">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Selected geometry
      </h2>
      {faceSelection ? (
        <div className="mb-2 space-y-0.5 text-xs">
          <p className="text-[color:#4c9ffe]">
            {/* Backend semantic label ("Top face of base plate") when a real CAD
                face matched; otherwise the mesh-derived kind. */}
            Selected:{" "}
            <span className={faceSelection.backend_label ? "" : "capitalize"}>
              {faceSelection.backend_label ?? faceKindLabel(faceSelection.face_kind)}
            </span>
            {fmtArea(faceSelection.area_mm2) ? (
              <span className="text-slate-400"> · {fmtArea(faceSelection.area_mm2)}</span>
            ) : null}
          </p>
          <p className="font-mono text-[11px] text-slate-500">
            normal {fmtVec(faceSelection.normal)}
          </p>
          <p className="font-mono text-[11px] text-slate-500">
            point {fmtVec(faceSelection.point)}
          </p>
        </div>
      ) : selected ? (
        <p className="mb-2 text-xs text-emerald-300">
          Selected: <span className="font-mono">{selected.entity_id}</span> ({selected.label})
        </p>
      ) : (
        <p className="mb-2 text-xs text-slate-400">
          Click a face on the model to select it — or turn on “Circle Edit” to draw
          over a feature (hole, edge, flange, face) — then describe the change.
        </p>
      )}
      <input
        className="input"
        placeholder={
          faceSelection
            ? "add a hole here · add slot here · add vents here"
            : selected
              ? EXAMPLES[selected.entity_type] ?? "describe the change"
              : "select a feature first"
        }
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void apply();
        }}
        disabled={busy || !canApply}
      />
      <button
        className="btn-primary mt-2 w-full"
        disabled={busy || !canApply || !instruction.trim()}
        onClick={apply}
      >
        {busy ? "Applying…" : "Apply to selection"}
      </button>
    </div>
  );
}
