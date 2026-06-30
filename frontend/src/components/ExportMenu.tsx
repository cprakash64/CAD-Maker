"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  /** Available export formats from the backend, e.g. ["stl", "step"]. */
  formats: string[];
  /** Validation failed — exports are not manufacturable. */
  blocked: boolean;
  /** Concept-only design — label exports honestly (not fully manufacturable). */
  concept?: boolean;
  hasPackage: boolean;
  onDownload: (fmt: string) => void;
  onPackage: () => void;
}

function DownloadIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
      <path d="M8 2.5v7M5 7l3 3 3-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M3 12.5h10" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

/** Primary top-bar Export action: a premium glass popover with STL / STEP /
 *  CAD-package downloads. Click-outside + Esc close; keyboard accessible. */
export default function ExportMenu({
  formats,
  blocked,
  concept,
  hasPackage,
  onDownload,
  onPackage,
}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const firstItemRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    const t = setTimeout(() => firstItemRef.current?.focus(), 20);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      clearTimeout(t);
    };
  }, [open]);

  const has = (f: string) => formats.map((x) => x.toLowerCase()).includes(f);
  const usable = !blocked && formats.length > 0;
  const word = concept ? "concept " : "";

  function run(fn: () => void) {
    fn();
    setOpen(false);
  }

  return (
    <div ref={ref} className="relative">
      <button
        ref={btnRef}
        type="button"
        className="btn-ghost btn-sm"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <DownloadIcon />
        Export
        <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden className="opacity-70">
          <path d="M2 3.5 5 6.5l3-3" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Export"
          className="absolute right-0 top-full z-50 mt-2 w-60 overflow-hidden rounded-xl border border-[color:var(--glass-border-strong)] bg-panel/95 p-1.5 shadow-lift backdrop-blur-xl"
        >
          {usable ? (
            <>
              {has("step") && (
                <Item innerRef={firstItemRef} onClick={() => run(() => onDownload("step"))} hint="Parametric B-rep for CAD">
                  Export {word}STEP
                </Item>
              )}
              {has("stl") && (
                <Item
                  innerRef={has("step") ? undefined : firstItemRef}
                  onClick={() => run(() => onDownload("stl"))}
                  hint="Mesh for 3D printing"
                >
                  Export {word}STL
                </Item>
              )}
              {/* Any other formats the backend reports. */}
              {formats
                .filter((f) => !["step", "stl"].includes(f.toLowerCase()))
                .map((f) => (
                  <Item key={f} onClick={() => run(() => onDownload(f))}>
                    Export {word}{f.toUpperCase()}
                  </Item>
                ))}
              {hasPackage && (
                <>
                  <div className="my-1 border-t border-edge/70" />
                  <Item onClick={() => run(onPackage)} hint="STEP + STL + report + drawings">
                    CAD package (.zip)
                  </Item>
                </>
              )}
              {concept && (
                <p className="px-2.5 pb-1 pt-1.5 text-[11px] leading-snug text-slate-500">
                  Concept geometry — verify before manufacturing.
                </p>
              )}
            </>
          ) : (
            <div className="px-2.5 py-2">
              <p className="text-xs font-medium text-slate-300">Export unavailable</p>
              <p className="mt-1 text-[11px] leading-snug text-slate-500">
                {blocked
                  ? "This design failed validation and can’t be exported as a manufacturable file."
                  : "Generate a valid part first."}
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Item({
  children,
  hint,
  onClick,
  innerRef,
}: {
  children: React.ReactNode;
  hint?: string;
  onClick: () => void;
  innerRef?: React.RefObject<HTMLButtonElement>;
}) {
  return (
    <button
      ref={innerRef}
      role="menuitem"
      onClick={onClick}
      className="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left text-sm text-slate-200 transition-colors hover:bg-raised/70 hover:text-slate-50 focus-visible:bg-raised/70"
    >
      <span className="font-medium">{children}</span>
      {hint && <span className="shrink-0 text-[10px] text-slate-500">{hint}</span>}
    </button>
  );
}
