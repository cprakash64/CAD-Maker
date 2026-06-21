"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";

export interface ExampleChip {
  label: string;
  prompt: string;
}

export const EXAMPLE_CHIPS: ExampleChip[] = [
  { label: "Round flange, 6 bolt holes",
    prompt: "A round flange, 80mm outer diameter, 10mm thick, with 6 M6 bolt holes on a 60mm bolt circle" },
  { label: "L-bracket, M6 holes",
    prompt: "An L bracket with 60mm legs, 5mm thick, 40mm wide, and two M6 holes on each face" },
  { label: "Hex standoff, M4 bore",
    prompt: "A hex standoff, 10mm across flats, 20mm long, with an M4 through bore" },
  { label: "Enclosure, 2.5mm walls",
    prompt: "An electronics enclosure 100 x 60 x 40 mm with 2.5mm walls and four M3 mounting bosses" },
  { label: "Mounting plate, 6 holes",
    prompt: "A rectangular mounting plate 120 x 80 x 6 mm with six M6 holes" },
];

interface Props {
  open: boolean;
  onClose: () => void;
}

/** Professional generation dialog: prompt + examples + optional image import.
 *  Blurs/dims the workspace; Esc / outside-click close (only when idle). */
export default function NewPartModal({ open, onClose }: Props) {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submitting = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Esc closes (when idle); focus the prompt on open; reset on close.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting.current) onClose();
    };
    document.addEventListener("keydown", onKey);
    const t = setTimeout(() => textareaRef.current?.focus(), 30);
    return () => {
      document.removeEventListener("keydown", onKey);
      clearTimeout(t);
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!open) {
      setError(null);
      setBusy(false);
      submitting.current = false;
    }
  }, [open]);

  if (!open) return null;

  async function generate() {
    if (submitting.current) return;
    if (file) return importImage();
    if (!prompt.trim()) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const design = await api.createDesign(prompt.trim());
      // Studio handles every result state (model / assembly / decomposition /
      // clarification), so we always route there on success.
      router.push(`/studio/${design.id}`);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : `Generation failed: ${String(e)}`);
      setBusy(false);
      submitting.current = false;
    }
  }

  async function importImage() {
    if (!file || submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const res = await api.generateFromDrawing(file, prompt.trim() || undefined);
      if (res.generated && res.design) {
        router.push(`/studio/${res.design.id}`);
        onClose();
        return;
      }
      // Couldn't auto-generate — hand off to the full Drawing → CAD workspace.
      setError("Couldn't auto-generate from this image. Opening Drawing → CAD to review…");
      setTimeout(() => {
        router.push("/drawing");
        onClose();
      }, 900);
    } catch (e) {
      setError(
        (e instanceof ApiError ? e.message : String(e)) +
          " — use the Drawing → CAD workspace for image import."
      );
      setBusy(false);
      submitting.current = false;
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-[12vh] backdrop-blur-sm"
      onMouseDown={() => {
        if (!submitting.current) onClose();
      }}
    >
      <div
        className="w-full max-w-2xl overflow-hidden rounded-xl border border-edge bg-panel shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="New part"
      >
        <div className="flex items-center justify-between border-b border-edge px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-100">New part</span>
            <span className="label">Describe or import</span>
          </div>
          <kbd className="rounded border border-edge bg-raised px-1.5 py-0.5 text-[10px] text-slate-500">
            Esc
          </kbd>
        </div>

        <div className="p-4">
          <textarea
            ref={textareaRef}
            className="input min-h-[120px] resize-y leading-relaxed"
            placeholder="Describe the part — e.g. a rectangular mounting plate 120 x 80 x 6 mm with six M6 holes. Paste a long spec if you have one."
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={busy}
          />

          <div className="mt-3 flex flex-wrap gap-1.5">
            {EXAMPLE_CHIPS.map((ex) => (
              <button
                key={ex.label}
                type="button"
                className="chip"
                disabled={busy}
                onClick={() => setPrompt(ex.prompt)}
              >
                {ex.label}
              </button>
            ))}
          </div>

          {/* Image import (wired to the existing Drawing → CAD flow). */}
          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-edge pt-3">
            <label className="chip cursor-pointer">
              {file ? "Change image" : "Attach drawing/image"}
              <input
                type="file"
                accept="image/*"
                className="hidden"
                disabled={busy}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            {file && (
              <span className="text-xs text-slate-400">
                {file.name}
                <button
                  type="button"
                  className="ml-2 text-slate-500 hover:text-slate-300"
                  onClick={() => setFile(null)}
                  disabled={busy}
                >
                  ✕
                </button>
              </span>
            )}
            <span className="text-[11px] text-slate-500">
              Drawing/image import uses the Drawing → CAD interpreter.
            </span>
          </div>

          {error && <div className="banner-danger mt-3">{error}</div>}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-edge px-4 py-3">
          <button className="btn-ghost btn-sm" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn-primary"
            onClick={generate}
            disabled={busy || (!prompt.trim() && !file)}
          >
            {busy ? "Generating…" : file ? "Generate from image" : "Generate CAD"}
          </button>
        </div>
      </div>
    </div>
  );
}
