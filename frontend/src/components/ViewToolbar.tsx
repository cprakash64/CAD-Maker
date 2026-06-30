"use client";

import { useEffect, useRef, useState } from "react";

export type ViewName =
  | "top"
  | "bottom"
  | "front"
  | "back"
  | "left"
  | "right"
  | "iso"
  | "fit";

export type DisplayMode = "shaded" | "edges" | "wireframe" | "technical";

const PRESETS: { name: ViewName; label: string }[] = [
  { name: "iso", label: "Isometric" },
  { name: "front", label: "Front" },
  { name: "back", label: "Back" },
  { name: "top", label: "Top" },
  { name: "bottom", label: "Bottom" },
  { name: "left", label: "Left" },
  { name: "right", label: "Right" },
];

const DISPLAY_MODES: { name: DisplayMode; label: string }[] = [
  { name: "shaded", label: "Shaded" },
  { name: "edges", label: "Shaded + edges" },
  { name: "wireframe", label: "Wireframe" },
  { name: "technical", label: "Technical" },
];

interface Props {
  active?: ViewName;
  onSelect: (view: ViewName) => void;
  onHome: () => void;
  onCapturePng: () => void;
  projection: "perspective" | "orthographic";
  onToggleProjection: () => void;
  showGrid: boolean;
  onToggleGrid: () => void;
  showAxes: boolean;
  onToggleAxes: () => void;
  displayMode: DisplayMode;
  onSelectDisplayMode: (m: DisplayMode) => void;
  measureMode: boolean;
  onToggleMeasure: () => void;
}

/* Small glass toolbar that floats over the viewer — a compact, professional
   CAD-style control set: view presets, home/fit, projection, grid/axes, PNG. */
export default function ViewToolbar({
  active,
  onSelect,
  onHome,
  onCapturePng,
  projection,
  onToggleProjection,
  showGrid,
  onToggleGrid,
  showAxes,
  onToggleAxes,
  displayMode,
  onSelectDisplayMode,
  measureMode,
  onToggleMeasure,
}: Props) {
  const activeLabel = PRESETS.find((p) => p.name === active)?.label ?? "View";
  const modeLabel = DISPLAY_MODES.find((m) => m.name === displayMode)?.label ?? "Shaded";

  return (
    <div className="flex items-center gap-1 rounded-xl border border-[color:var(--glass-border)] bg-panel/85 p-1 shadow-glass backdrop-blur-xl">
      <Dropdown label={activeLabel} title="View presets">
        {(close) => (
          <div className="grid grid-cols-1 gap-0.5">
            {PRESETS.map((p) => (
              <button
                key={p.name}
                role="menuitemradio"
                aria-checked={active === p.name}
                onClick={() => {
                  onSelect(p.name);
                  close();
                }}
                className={`flex items-center justify-between gap-6 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors ${
                  active === p.name
                    ? "bg-raised text-slate-50"
                    : "text-slate-300 hover:bg-raised/70 hover:text-slate-100"
                }`}
              >
                {p.label}
                {active === p.name && <span className="text-accent">✓</span>}
              </button>
            ))}
          </div>
        )}
      </Dropdown>

      <IconBtn title="Home — fit to view" onClick={onHome} label="Home">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path d="M2 7.5 8 2.5l6 5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M3.5 6.6V13h9V6.6" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
        </svg>
      </IconBtn>

      <span className="mx-0.5 h-5 w-px bg-edge" />

      {/* Display mode — shaded / edges / wireframe / technical. */}
      <Dropdown label={modeLabel} title="Display mode">
        {(close) => (
          <div className="grid grid-cols-1 gap-0.5">
            {DISPLAY_MODES.map((m) => (
              <button
                key={m.name}
                role="menuitemradio"
                aria-checked={displayMode === m.name}
                onClick={() => {
                  onSelectDisplayMode(m.name);
                  close();
                }}
                className={`flex items-center justify-between gap-6 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors ${
                  displayMode === m.name
                    ? "bg-raised text-slate-50"
                    : "text-slate-300 hover:bg-raised/70 hover:text-slate-100"
                }`}
              >
                {m.label}
                {displayMode === m.name && <span className="text-accent">✓</span>}
              </button>
            ))}
          </div>
        )}
      </Dropdown>

      <IconBtn title="Measure — click two points" active={measureMode} onClick={onToggleMeasure} label="Measure">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
          <path d="M2.5 9.5 9.5 2.5l4 4-7 7-4-4Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
          <path d="M5 7l1.2 1.2M7 5l1.2 1.2M9 6.5l1 1" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" />
        </svg>
      </IconBtn>

      {/* Advanced toggles — inline on >=sm, tucked into a menu on small screens. */}
      <span className="mx-0.5 hidden h-5 w-px bg-edge sm:block" />
      <div className="hidden items-center gap-1 sm:flex">
        <Toggle
          title={projection === "orthographic" ? "Orthographic (click for perspective)" : "Perspective (click for orthographic)"}
          on={projection === "orthographic"}
          onClick={onToggleProjection}
        >
          {projection === "orthographic" ? "Ortho" : "Persp"}
        </Toggle>
        <IconBtn title="Toggle grid" active={showGrid} onClick={onToggleGrid} label="Grid">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
            <rect x="2" y="2" width="12" height="12" rx="1" stroke="currentColor" strokeWidth="1.2" />
            <path d="M6 2v12M10 2v12M2 6h12M2 10h12" stroke="currentColor" strokeWidth="1" />
          </svg>
        </IconBtn>
        <IconBtn title="Toggle axes / orientation gizmo" active={showAxes} onClick={onToggleAxes} label="Axes">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path d="M8 14V5M8 5 5 8M8 5l3 3M8 14 3 11M8 14l5-3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </IconBtn>
        <IconBtn title="Export view as PNG" onClick={onCapturePng} label="PNG">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
            <path d="M8 2.5v7M5 7l3 3 3-3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M3 12.5h10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
        </IconBtn>
      </div>

      {/* Small-screen overflow menu for advanced toggles. */}
      <div className="sm:hidden">
        <Dropdown label="⋯" title="More controls" compact align="right">
          {() => (
            <div className="grid gap-0.5">
              <MenuRow onClick={onToggleProjection}>
                Projection · <span className="text-slate-400">{projection === "orthographic" ? "Ortho" : "Persp"}</span>
              </MenuRow>
              <MenuRow onClick={onToggleGrid} checked={showGrid}>Grid</MenuRow>
              <MenuRow onClick={onToggleAxes} checked={showAxes}>Axes</MenuRow>
              <MenuRow onClick={onCapturePng}>Export PNG</MenuRow>
            </div>
          )}
        </Dropdown>
      </div>
    </div>
  );
}

function IconBtn({
  title,
  label,
  active,
  onClick,
  children,
}: {
  title: string;
  label: string;
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={`grid h-7 w-7 place-items-center rounded-lg transition-colors ${
        active
          ? "bg-accent/15 text-accent"
          : "text-slate-400 hover:bg-raised/70 hover:text-slate-100"
      }`}
    >
      {children}
    </button>
  );
}

function Toggle({
  title,
  on,
  onClick,
  children,
}: {
  title: string;
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={on}
      onClick={onClick}
      className={`rounded-lg px-2 py-1.5 text-[11px] font-medium transition-colors ${
        on ? "bg-accent/15 text-accent" : "text-slate-400 hover:bg-raised/70 hover:text-slate-100"
      }`}
    >
      {children}
    </button>
  );
}

function MenuRow({
  onClick,
  checked,
  children,
}: {
  onClick: () => void;
  checked?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="flex items-center justify-between gap-6 rounded-md px-2.5 py-1.5 text-left text-xs text-slate-300 transition-colors hover:bg-raised/70 hover:text-slate-100"
    >
      <span>{children}</span>
      {checked !== undefined && (
        <span className={checked ? "text-accent" : "text-slate-600"}>{checked ? "✓" : "○"}</span>
      )}
    </button>
  );
}

function Dropdown({
  label,
  title,
  compact,
  align = "left",
  children,
}: {
  label: string;
  title: string;
  compact?: boolean;
  align?: "left" | "right";
  children: (close: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        title={title}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={`flex items-center gap-1 rounded-lg text-xs font-medium transition-colors ${
          compact ? "h-7 w-7 justify-center" : "px-2.5 py-1.5"
        } ${open ? "bg-raised text-slate-50" : "text-slate-200 hover:bg-raised/70"}`}
      >
        {label}
        {!compact && (
          <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden className="opacity-70">
            <path d="M2 3.5 5 6.5l3-3" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </button>
      {open && (
        <div
          role="menu"
          className={`absolute top-full z-20 mt-1.5 min-w-[9rem] rounded-xl border border-[color:var(--glass-border)] bg-panel/95 p-1 shadow-lift backdrop-blur-xl ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}
