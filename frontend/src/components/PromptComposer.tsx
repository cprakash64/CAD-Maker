"use client";

import type { ReactNode, RefObject } from "react";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  busy?: boolean;
  /** Disable the submit action (e.g. empty prompt). */
  disabled?: boolean;
  placeholder?: string;
  submitLabel?: string;
  /** Small helper line under the textarea. */
  hint?: ReactNode;
  /** Optional content rendered in the footer, left of the submit button. */
  footerLeft?: ReactNode;
  /** Header eyebrow label. */
  label?: string;
  inputRef?: RefObject<HTMLTextAreaElement>;
  rows?: number;
}

/**
 * The precise engineering "command surface" used to describe a part. A calm
 * smoked-glass card with a borderless textarea, ⌘↵ to submit, and a single
 * brass action. Behaviour is owned by the parent via `onSubmit`.
 */
export default function PromptComposer({
  value,
  onChange,
  onSubmit,
  busy = false,
  disabled = false,
  placeholder = "Example: 80mm wall bracket, 5mm thick, two M6 mounting holes, rounded corners.",
  submitLabel = "Generate part",
  hint,
  footerLeft,
  label = "Specification",
  inputRef,
  rows = 5,
}: Props) {
  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between border-b border-edge px-4 py-2.5">
        <span className="label text-slate-400">{label}</span>
        <span className="stat text-[10px] text-slate-600">⌘↵ to generate</span>
      </div>
      <div className="p-4">
        <textarea
          ref={inputRef}
          rows={rows}
          className="w-full resize-y border-0 bg-transparent p-0 font-sans text-[15px] leading-relaxed text-slate-100 placeholder:text-slate-600 outline-none"
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSubmit();
          }}
          disabled={busy}
        />
        {hint && (
          <p className="mt-3 flex items-start gap-2 border-t border-edge/60 pt-3 text-xs leading-relaxed text-slate-500">
            <span className="mt-0.5 text-accent">▸</span>
            <span>{hint}</span>
          </p>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-edge bg-raised/30 px-4 py-3">
        <div className="min-w-0 text-[11px] text-slate-600">
          {footerLeft ?? <span className="stat">{value.trim().length} chars</span>}
        </div>
        <button
          className="btn-primary"
          onClick={onSubmit}
          disabled={busy || disabled || !value.trim()}
        >
          {busy ? "Generating…" : submitLabel}
        </button>
      </div>
    </div>
  );
}
