"use client";

import { useEffect, useState } from "react";

interface Props {
  parameters: Record<string, number>;
  onRegenerate: (next: Record<string, number>) => Promise<void>;
  busy: boolean;
}

function prettyLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function ParameterSidebar({
  parameters,
  onRegenerate,
  busy,
}: Props) {
  const [values, setValues] = useState<Record<string, number>>(parameters);
  const [dirty, setDirty] = useState(false);

  // Sync when a regeneration returns new canonical values.
  useEffect(() => {
    setValues(parameters);
    setDirty(false);
  }, [parameters]);

  const keys = Object.keys(values);

  return (
    <div className="card p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Parameters (mm)
      </h2>
      <div className="space-y-3">
        {keys.map((key) => (
          <label key={key} className="block">
            <span className="mb-1 block text-xs text-slate-400">
              {prettyLabel(key)}
            </span>
            <input
              type="number"
              step="0.1"
              className="input"
              value={Number.isFinite(values[key]) ? values[key] : 0}
              onChange={(e) => {
                const v = parseFloat(e.target.value);
                setValues((prev) => ({ ...prev, [key]: Number.isNaN(v) ? 0 : v }));
                setDirty(true);
              }}
            />
          </label>
        ))}
      </div>
      <button
        className="btn-primary mt-4 w-full"
        disabled={busy || !dirty}
        onClick={() => onRegenerate(values)}
      >
        {busy ? "Regenerating…" : dirty ? "Apply changes" : "No changes"}
      </button>
      <p className="mt-2 text-xs text-slate-500">
        Changes regenerate the model deterministically — no AI re-prompt.
      </p>
    </div>
  );
}
