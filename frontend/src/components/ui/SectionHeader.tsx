import type { ReactNode } from "react";

/**
 * Page / section heading with Apple-like rhythm: a small uppercase eyebrow over
 * a tight title, with optional supporting copy and trailing action slot.
 */
export function SectionHeader({
  eyebrow,
  title,
  description,
  action,
  className = "",
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex flex-wrap items-end justify-between gap-4 ${className}`}>
      <div className="space-y-1.5">
        {eyebrow && <span className="label block">{eyebrow}</span>}
        <h2 className="text-balance text-xl font-semibold tracking-tight text-slate-50 sm:text-2xl">
          {title}
        </h2>
        {description && (
          <p className="max-w-2xl text-sm leading-relaxed text-slate-400">
            {description}
          </p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export default SectionHeader;
