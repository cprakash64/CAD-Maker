"use client";

import { useState } from "react";
import type { SelectedFeature } from "./Studio3D";

interface Props {
  selected: SelectedFeature | null;
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

export default function CircleEditPanel({ selected, onApply, busy }: Props) {
  const [instruction, setInstruction] = useState("");

  async function apply() {
    if (!instruction.trim() || busy || !selected) return;
    await onApply(instruction.trim());
    setInstruction("");
  }

  return (
    <div className="card p-4">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Circle-to-edit
      </h2>
      {selected ? (
        <p className="mb-2 text-xs text-emerald-300">
          Selected: <span className="font-mono">{selected.entity_id}</span> ({selected.label})
        </p>
      ) : (
        <p className="mb-2 text-xs text-slate-400">
          Turn on “Circle Edit”, draw a circle over a feature (hole, edge, flange,
          face), then describe the change.
        </p>
      )}
      <input
        className="input"
        placeholder={
          selected ? EXAMPLES[selected.entity_type] ?? "describe the change" : "select a feature first"
        }
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void apply();
        }}
        disabled={busy || !selected}
      />
      <button
        className="btn-primary mt-2 w-full"
        disabled={busy || !selected || !instruction.trim()}
        onClick={apply}
      >
        {busy ? "Applying…" : "Apply to selection"}
      </button>
    </div>
  );
}
