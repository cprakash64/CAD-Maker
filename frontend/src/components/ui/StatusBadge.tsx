type Status = "ready" | "draft" | "needs_info" | "pass" | "warning" | "fail";

const META: Record<Status, { cls: string; label: string; dot: string }> = {
  ready: { cls: "badge-pass", label: "Ready", dot: "bg-accent" },
  pass: { cls: "badge-pass", label: "Pass", dot: "bg-accent" },
  draft: { cls: "badge-neutral", label: "Draft", dot: "bg-slate-500" },
  needs_info: { cls: "badge-review", label: "Needs info", dot: "bg-amber-400" },
  warning: { cls: "badge-review", label: "Warning", dot: "bg-amber-400" },
  fail: { cls: "badge-fail", label: "Failed", dot: "bg-danger" },
};

/**
 * Elegant, subdued status pill. Always pairs a colored dot with a text label so
 * status is never conveyed by color alone (accessibility).
 */
export function StatusBadge({
  status,
  label,
  className = "",
}: {
  status: Status;
  label?: string;
  className?: string;
}) {
  const m = META[status];
  return (
    <span className={`${m.cls} ${className}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} aria-hidden />
      {label ?? m.label}
    </span>
  );
}

export default StatusBadge;
