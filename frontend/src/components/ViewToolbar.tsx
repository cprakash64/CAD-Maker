"use client";

export type ViewName = "top" | "front" | "right" | "left" | "iso" | "fit";

const VIEWS: { name: ViewName; label: string }[] = [
  { name: "top", label: "Top" },
  { name: "front", label: "Front" },
  { name: "right", label: "Right" },
  { name: "left", label: "Left" },
  { name: "iso", label: "Iso" },
  { name: "fit", label: "Fit" },
];

interface Props {
  onSelect: (view: ViewName) => void;
  onCapturePng: () => void;
  active?: ViewName;
}

export default function ViewToolbar({ onSelect, onCapturePng, active }: Props) {
  return (
    <div
      className="flex flex-wrap items-center gap-0.5 rounded-md border border-edge bg-raised/80 p-0.5"
      role="toolbar"
      aria-label="View controls"
    >
      {VIEWS.map((v) => (
        <button
          key={v.name}
          onClick={() => onSelect(v.name)}
          aria-pressed={active === v.name}
          className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
            active === v.name
              ? "bg-accent text-white"
              : "text-slate-400 hover:bg-edge hover:text-slate-200"
          }`}
        >
          {v.label}
        </button>
      ))}
      <span className="mx-1 h-4 w-px bg-edge" />
      <button
        onClick={onCapturePng}
        className="rounded px-2.5 py-1 text-xs font-medium text-slate-400 hover:bg-edge hover:text-slate-200"
        title="Download the current 3D view as a PNG"
      >
        ↓ PNG
      </button>
    </div>
  );
}
