import type { VersionDiffEntry } from "@/lib/types";

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

function formatField(field: string): string {
  // "holes[0].diameter" -> "hole 1 diameter"; "dimensions.width" -> "width"
  return field
    .replace(/^dimensions\./, "")
    .replace(/^feature\[([^\]]+)\]\.?/, "feature $1 ")
    .replace(/holes\[(\d+)\]/, (_m, i) => `hole ${Number(i) + 1}`)
    .replace(/[._]/g, " ")
    .trim();
}

/**
 * "For every edit: identify the affected feature, show old and new values."
 * Renders the structured diff a just-applied edit produced (design.last_edit_diff /
 * a VersionSummary's diff) — never a free-text description standing in for it.
 */
export default function EditDiff({
  diff,
  title = "What changed",
}: {
  diff: VersionDiffEntry[];
  title?: string;
}) {
  if (!diff || diff.length === 0) return null;
  return (
    <div
      role="status"
      className="rounded-md border border-accent/30 bg-accent/5 p-3 text-[11px]"
    >
      <p className="label mb-1.5 text-slate-300">{title}</p>
      <ul className="space-y-1">
        {diff.map((d, i) => (
          <li key={`${d.field}-${i}`} className="flex flex-wrap items-baseline gap-x-1.5">
            <span className="capitalize text-slate-400">{formatField(d.field)}:</span>
            <span className="stat text-slate-500 line-through">{formatValue(d.old)}</span>
            <span aria-hidden className="text-slate-600">
              →
            </span>
            <span className="stat font-medium text-slate-100">{formatValue(d.new)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
