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
      className="flex flex-wrap items-center gap-1 rounded-lg border border-edge bg-panel/70 p-1"
      role="toolbar"
      aria-label="View controls"
    >
      {VIEWS.map((v) => (
        <button
          key={v.name}
          onClick={() => onSelect(v.name)}
          aria-pressed={active === v.name}
          className={`rounded px-2.5 py-1 text-xs ${
            active === v.name
              ? "bg-accent text-white"
              : "text-slate-300 hover:bg-edge"
          }`}
        >
          {v.label}
        </button>
      ))}
      <span className="mx-1 h-4 w-px bg-edge" />
      <button
        onClick={onCapturePng}
        className="rounded px-2.5 py-1 text-xs text-slate-300 hover:bg-edge"
        title="Download the current 3D view as a PNG"
      >
        ↓ PNG
      </button>
    </div>
  );
}
