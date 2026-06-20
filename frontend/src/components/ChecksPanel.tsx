"use client";

import type { Check } from "@/lib/types";

const STYLES: Record<Check["severity"], string> = {
  critical: "border-red-500/60 bg-red-500/15 text-red-100",
  error: "border-red-500/40 bg-red-500/10 text-red-200",
  warning: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  info: "border-edge bg-ink text-slate-300",
};

const ICON: Record<Check["severity"], string> = {
  critical: "✕",
  error: "✕",
  warning: "!",
  info: "i",
};

export default function ChecksPanel({ checks }: { checks: Check[] }) {
  const failing = checks.filter((c) => !c.passed);
  return (
    <div className="card p-4">
      <h2 className="mb-3 flex items-center justify-between text-sm font-semibold uppercase tracking-wide text-slate-300">
        Manufacturability
        <span className="text-xs font-normal text-slate-400">
          {failing.length === 0
            ? "all clear"
            : `${failing.length} to review`}
        </span>
      </h2>
      <div className="space-y-2">
        {checks.length === 0 && (
          <p className="text-sm text-slate-400">No checks run yet.</p>
        )}
        {checks.map((c, i) => (
          <div
            key={`${c.check}-${i}`}
            className={`flex gap-2 rounded-md border p-2 text-xs ${
              c.passed ? STYLES.info : STYLES[c.severity]
            }`}
          >
            <span className="font-bold">
              {c.passed ? "✓" : ICON[c.severity]}
            </span>
            <span>{c.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
