"use client";

import { useEffect, useState } from "react";
import type { Hole, HoleType } from "@/lib/types";

interface Props {
  holes: Hole[];
  onApply: (holes: Hole[]) => Promise<void>;
  busy: boolean;
}

const HOLE_TYPES: HoleType[] = ["simple", "counterbore", "countersink"];

function blankHole(): Hole {
  return { diameter: 6, x: 0, y: 0, hole_type: "simple" };
}

export default function HoleTable({ holes, onApply, busy }: Props) {
  const [rows, setRows] = useState<Hole[]>(holes);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setRows(holes);
    setDirty(false);
  }, [holes]);

  function update(i: number, patch: Partial<Hole>) {
    setRows((prev) => prev.map((h, idx) => (idx === i ? { ...h, ...patch } : h)));
    setDirty(true);
  }

  function num(v: string): number {
    const n = parseFloat(v);
    return Number.isNaN(n) ? 0 : n;
  }

  return (
    <div className="card p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-300">
        Holes
      </h2>
      {rows.length === 0 && (
        <p className="mb-2 text-xs text-slate-400">No holes yet.</p>
      )}
      <div className="space-y-3">
        {rows.map((h, i) => (
          <div key={i} className="rounded-md border border-edge p-2">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs text-slate-400">Hole {i + 1}</span>
              <button
                className="text-xs text-red-300 hover:underline"
                onClick={() => {
                  setRows((prev) => prev.filter((_, idx) => idx !== i));
                  setDirty(true);
                }}
              >
                remove
              </button>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <label className="text-xs text-slate-400">
                Ø
                <input
                  type="number"
                  step="0.1"
                  className="input mt-0.5 px-2 py-1"
                  value={h.diameter}
                  onChange={(e) => update(i, { diameter: num(e.target.value) })}
                />
              </label>
              <label className="text-xs text-slate-400">
                x
                <input
                  type="number"
                  step="0.5"
                  className="input mt-0.5 px-2 py-1"
                  value={h.x}
                  onChange={(e) => update(i, { x: num(e.target.value) })}
                />
              </label>
              <label className="text-xs text-slate-400">
                y
                <input
                  type="number"
                  step="0.5"
                  className="input mt-0.5 px-2 py-1"
                  value={h.y}
                  onChange={(e) => update(i, { y: num(e.target.value) })}
                />
              </label>
            </div>
            <label className="mt-2 block text-xs text-slate-400">
              Type
              <select
                className="input mt-0.5 px-2 py-1"
                value={h.hole_type}
                onChange={(e) =>
                  update(i, { hole_type: e.target.value as HoleType })
                }
              >
                {HOLE_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            {h.hole_type === "counterbore" && (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label className="text-xs text-slate-400">
                  C'bore Ø
                  <input
                    type="number"
                    step="0.1"
                    className="input mt-0.5 px-2 py-1"
                    value={h.counterbore_diameter ?? ""}
                    onChange={(e) =>
                      update(i, { counterbore_diameter: num(e.target.value) })
                    }
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Depth
                  <input
                    type="number"
                    step="0.1"
                    className="input mt-0.5 px-2 py-1"
                    value={h.counterbore_depth ?? ""}
                    onChange={(e) =>
                      update(i, { counterbore_depth: num(e.target.value) })
                    }
                  />
                </label>
              </div>
            )}
            {h.hole_type === "countersink" && (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <label className="text-xs text-slate-400">
                  C'sink Ø
                  <input
                    type="number"
                    step="0.1"
                    className="input mt-0.5 px-2 py-1"
                    value={h.countersink_diameter ?? ""}
                    onChange={(e) =>
                      update(i, { countersink_diameter: num(e.target.value) })
                    }
                  />
                </label>
                <label className="text-xs text-slate-400">
                  Angle
                  <input
                    type="number"
                    step="1"
                    className="input mt-0.5 px-2 py-1"
                    value={h.countersink_angle ?? 90}
                    onChange={(e) =>
                      update(i, { countersink_angle: num(e.target.value) })
                    }
                  />
                </label>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex gap-2">
        <button
          className="btn-ghost text-sm"
          onClick={() => {
            setRows((prev) => [...prev, blankHole()]);
            setDirty(true);
          }}
        >
          + Add hole
        </button>
        <button
          className="btn-primary text-sm"
          disabled={busy || !dirty}
          onClick={() => onApply(rows)}
        >
          {busy ? "…" : "Apply holes"}
        </button>
      </div>
    </div>
  );
}
