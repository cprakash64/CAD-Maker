import Link from "next/link";
import type { Design } from "@/lib/types";

export default function ClarificationCard({
  design,
  busy,
  onGenerateDefaults,
}: {
  design: Design;
  busy: boolean;
  onGenerateDefaults: () => void;
}) {
  return (
    <div className="banner-warn p-4">
      <p className="font-semibold">More information needed</p>
      {design.clarification_questions.length > 0 ? (
        <ul className="mt-1.5 space-y-1 text-sm">
          {design.clarification_questions.map((q, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="text-amber-300">•</span>
              <span>{q}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm">{design.clarification_question}</p>
      )}
      {design.clarification_questions.length === 0 && design.missing_required.length > 0 && (
        <p className="mt-1 text-xs text-amber-200/90">
          Missing: {design.missing_required.join(", ")}
        </p>
      )}
      {design.clarification_options.length > 0 && (
        <div className="mt-3">
          <p className="label mb-1.5 text-amber-200/90">
            Pick a ready-to-generate part:
          </p>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {design.clarification_options.map((opt) => (
              <Link
                key={opt.label}
                href={`/new?prompt=${encodeURIComponent(opt.prompt)}`}
                title={opt.prompt}
                className="card flex flex-col gap-0.5 p-2.5 text-left transition hover:border-amber-400/60"
              >
                <span className="text-sm font-medium text-slate-100">{opt.label}</span>
                <span className="line-clamp-2 text-xs text-slate-400">{opt.prompt}</span>
              </Link>
            ))}
          </div>
        </div>
      )}
      <div className="mt-3 flex gap-2">
        {design.can_generate_with_defaults && (
          <button className="btn-primary btn-sm" onClick={onGenerateDefaults} disabled={busy}>
            Generate with defaults
          </button>
        )}
        <Link
          href={`/new?prompt=${encodeURIComponent(design.prompt + " ")}`}
          className="btn-ghost btn-sm"
        >
          Refine prompt
        </Link>
      </div>
    </div>
  );
}
