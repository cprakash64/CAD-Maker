"use client";

import type { DimensionReport, PrintReadiness, ValidationStatus } from "@/lib/types";

// Minimal, non-engineer-friendly "Validation & print readiness" panel driven by
// the additive backend fields. Every field is treated as optional so missing or
// null data never crashes the page — we just show less.

const AXIS_LABEL: Record<string, string> = {
  bbox_x: "Width (X)",
  bbox_y: "Depth (Y)",
  bbox_z: "Height (Z)",
  hole_count: "Hole count",
};

function label(name: string): string {
  return AXIS_LABEL[name] ?? name.replace(/_/g, " ");
}

function fmt(n: number | null | undefined, digits = 1): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function StatusRow({ ok, label: text }: { ok: boolean | undefined; label: string }) {
  // `undefined` => not reported (neutral), not a failure.
  const known = typeof ok === "boolean";
  return (
    <div className="flex items-center gap-2">
      <span className={!known ? "text-slate-500" : ok ? "text-emerald-400" : "text-amber-400"}>
        {!known ? "–" : ok ? "✓" : "⚠"}
      </span>
      <span className="text-slate-300">{text}</span>
    </div>
  );
}

interface Props {
  report?: DimensionReport | null;
  printReadiness?: PrintReadiness | null;
  withinTolerance?: boolean | null;
  validationStatus?: ValidationStatus | null;
  criticalFailures?: string[];
  assumptions?: string[];
}

export default function ValidationPanel({
  report,
  printReadiness,
  withinTolerance,
  validationStatus,
  criticalFailures,
  assumptions = [],
}: Props) {
  const pr: PrintReadiness = printReadiness ?? report?.print_readiness ?? {};
  const measured = report?.measured ?? {};
  const comparisons = report?.comparisons ?? [];
  const issues = pr.issues ?? [];
  const tol = report?.tolerance ?? {};
  const notes = report?.notes ?? [];
  const within = report?.within_tolerance ?? withinTolerance ?? null;
  // Prefer the backend's authoritative status + list; fall back to the report.
  const status = validationStatus ?? report?.validation?.status ?? null;
  const critical = criticalFailures ?? report?.validation?.critical_failures ?? [];

  // Nothing useful to show -> render nothing (no empty card).
  const hasReport = !!report;
  if (!hasReport && assumptions.length === 0) return null;

  // Overall state. Critical failures dominate (distinct, red). Otherwise:
  //  review  -> non-critical issue worth a look
  //  pass    -> dimensions matched a requested target AND geometry looks healthy
  //  unknown -> built & looks printable, but no requested target to compare to
  const isFalse = (v: boolean | undefined) => v === false;
  const hasCritical = status === "critical_failure" || critical.length > 0;
  const warningish =
    status === "warning" || pr.printable === false || within === false ||
    isFalse(pr.watertight) || isFalse(pr.manifold) || issues.length > 0;

  const state: "fail" | "pass" | "review" | "unknown" = hasCritical
    ? "fail"
    : warningish
      ? "review"
      : within === true || status === "pass"
        ? "pass"
        : "unknown";

  const STATE_META = {
    fail: { cls: "border-red-500/60 bg-red-500/15 text-red-100", text: "Failed validation" },
    pass: { cls: "border-emerald-500/50 bg-emerald-500/10 text-emerald-200", text: "Looks good" },
    review: { cls: "border-amber-500/50 bg-amber-500/10 text-amber-200", text: "Review suggested" },
    unknown: { cls: "border-slate-600 bg-slate-800 text-slate-300", text: "Built — not compared to a target" },
  }[state];

  const toleranceNote =
    typeof tol.length_tolerance_mm === "number"
      ? `Dimensions are checked to ±${fmt(tol.length_tolerance_mm, 2)} mm or ${
          typeof tol.length_tolerance_frac === "number"
            ? (tol.length_tolerance_frac * 100).toFixed(0)
            : "—"
        }%, whichever is larger.`
      : null;

  return (
    <div className="card p-4">
      <h2 className="mb-3 flex items-center justify-between text-sm font-semibold uppercase tracking-wide text-slate-300">
        Validation &amp; print readiness
        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-normal normal-case ${STATE_META.cls}`}>
          {STATE_META.text}
        </span>
      </h2>

      {/* Critical failures — visually distinct from warnings (red, top of panel). */}
      {critical.length > 0 && (
        <div className="mb-3 rounded-md border border-red-500/60 bg-red-500/15 p-2">
          <div className="mb-1 text-xs font-semibold text-red-100">
            Critical issues — not safe to manufacture as-is
          </div>
          <ul className="space-y-1 text-xs text-red-100/90">
            {critical.map((c, i) => (
              <li key={i} className="flex gap-1.5">
                <span>✕</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Requested vs generated dimensions (only when a comparison exists). */}
      {comparisons.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs font-medium text-slate-400">Requested vs generated</div>
          <div className="space-y-1 text-xs">
            {comparisons.map((c, i) => {
              const unit = c.name.startsWith("bbox") ? " mm" : "";
              return (
                <div key={`${c.name}-${i}`} className="flex items-center gap-2">
                  <span className={c.within === false ? "text-amber-400" : "text-emerald-400"}>
                    {c.within === false ? "⚠" : "✓"}
                  </span>
                  <span className="w-24 shrink-0 text-slate-300">{label(c.name)}</span>
                  <span className="text-slate-400">
                    asked {fmt(c.requested_mm, c.name.startsWith("bbox") ? 1 : 0)}
                    {unit} → got {fmt(c.measured_mm, c.name.startsWith("bbox") ? 1 : 0)}
                    {unit}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* No requested target, but we can still show the generated size. */}
      {comparisons.length === 0 && measured.bbox_mm && (
        <div className="mb-3 text-xs text-slate-400">
          Generated size: {fmt(measured.bbox_mm.x)} × {fmt(measured.bbox_mm.y)} ×{" "}
          {fmt(measured.bbox_mm.z)} mm
          <span className="block text-slate-500">No requested target to compare against.</span>
        </div>
      )}

      {/* Geometry health (only when a report exists). */}
      {hasReport && (
        <div className="mb-3 space-y-1 text-xs">
          <StatusRow ok={pr.watertight} label="Sealed surface (watertight)" />
          <StatusRow ok={pr.manifold} label="Clean surface (manifold)" />
          <StatusRow ok={pr.single_body} label="One solid body" />
          <StatusRow ok={pr.positive_volume} label="Has solid volume" />
          <div className="flex items-center gap-2">
            <span className="text-slate-500">•</span>
            <span className="text-slate-300">
              Holes cut: {typeof measured.hole_count === "number" ? measured.hole_count : "—"}
              {typeof measured.volume_mm3 === "number" &&
                ` · Volume: ${fmt(measured.volume_mm3, 0)} mm³`}
            </span>
          </div>
        </div>
      )}

      {/* Print-readiness warnings. */}
      {issues.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs font-medium text-amber-300">Warnings</div>
          <ul className="space-y-1 text-xs text-amber-200/90">
            {issues.map((w, i) => (
              <li key={i} className="flex gap-1.5">
                <span>⚠</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Assumptions / defaults the generator applied. */}
      {assumptions.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-xs font-medium text-slate-400">Assumptions &amp; defaults</div>
          <ul className="space-y-1 text-xs text-slate-300">
            {assumptions.map((a, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="text-accent">•</span>
                <span>{a}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Tolerance + honesty note (no overclaiming). */}
      {hasReport && (
        <div className="border-t border-edge pt-2 text-[11px] leading-relaxed text-slate-500">
          {toleranceNote && <p>{toleranceNote}</p>}
          {notes.map((n, i) => (
            <p key={i}>{n}</p>
          ))}
          <p>
            Measured from the generated 3D model. Always double-check critical
            dimensions before manufacturing.
          </p>
        </div>
      )}
    </div>
  );
}
