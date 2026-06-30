"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { ONBOARDING_EXAMPLES } from "@/lib/examples";
import MockModeBanner from "@/components/MockModeBanner";
import { SectionHeader } from "@/components/ui/SectionHeader";

function NewDesignInner() {
  const router = useRouter();
  const params = useSearchParams();
  const { user, loading } = useRequireAuth();
  const [prompt, setPrompt] = useState(params.get("prompt") ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Ref guard: blocks a second POST from a fast double-click before React has
  // re-rendered the disabled button (state updates are async).
  const submitting = useRef(false);

  async function submit() {
    if (submitting.current || !prompt.trim()) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      const design = await api.createDesign(prompt.trim());
      // Navigating away; keep `submitting` latched so nothing re-fires.
      router.push(`/studio/${design.id}`);
    } catch (e) {
      // Surface the exact backend reason (validation / 503 detail), never a vague
      // "something went wrong". Re-enable so the user can retry.
      setError(e instanceof ApiError ? e.message : `Generation failed: ${String(e)}`);
      setBusy(false);
      submitting.current = false;
    }
  }

  if (loading || !user) {
    return <div className="page text-slate-400">Loading…</div>;
  }

  return (
    <div className="page max-w-3xl">
      <div className="space-y-6">
        <SectionHeader
          eyebrow="New part"
          title="Describe what you need"
          description="Write it the way you'd brief a machinist. Dimensions, hole callouts, thickness and material — include what you know; defaults fill the rest."
        />

        <MockModeBanner />

        {/* Command surface */}
        <div className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-edge px-4 py-2.5">
            <span className="label text-slate-400">Specification</span>
            <span className="stat text-[10px] text-slate-600">⌘↵ to generate</span>
          </div>
          <div className="p-4">
            <textarea
              className="min-h-[160px] w-full resize-y border-0 bg-transparent p-0 font-sans text-[15px] leading-relaxed text-slate-100 placeholder:text-slate-600 outline-none"
              placeholder="e.g. Wall-mounted bracket for a 25 mm pipe with two M6 screw holes, 5 mm thick, 80 mm wide."
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
              }}
            />
            <p className="mt-3 flex items-start gap-2 border-t border-edge/60 pt-3 text-xs leading-relaxed text-slate-500">
              <span className="mt-0.5 text-accent">▸</span>
              <span>
                Include key dimensions, hole sizes (e.g.{" "}
                <code className="stat text-slate-300">M6</code>), thickness and
                material where you know them. Missing details are filled with
                documented defaults and shown back to you.
              </span>
            </p>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-edge bg-raised/30 px-4 py-3">
            <span className="stat text-[11px] text-slate-600">
              {prompt.trim().length} chars
            </span>
            <button
              className="btn-primary"
              onClick={submit}
              disabled={busy || !prompt.trim()}
            >
              {busy ? "Generating…" : "Generate part"}
            </button>
          </div>
        </div>

        {error && <div className="banner-danger">{error}</div>}

        {/* Presets */}
        <div className="space-y-3 pt-2">
          <h2 className="label">Or start from a preset</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {ONBOARDING_EXAMPLES.map((ex, i) => (
              <button
                key={ex.label}
                className="card lift group p-4 text-left"
                onClick={() => setPrompt(ex.prompt)}
              >
                <div className="flex items-center justify-between">
                  <span className="stat text-[10px] uppercase tracking-[0.16em] text-slate-500">
                    {`PRESET·${String(i + 1).padStart(2, "0")}`}
                  </span>
                  <span className="text-slate-600 transition-colors group-hover:text-accent">
                    ↵
                  </span>
                </div>
                <div className="mt-2 text-sm font-semibold text-slate-100">{ex.label}</div>
                <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-400">
                  {ex.prompt}
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Shared New Design page body, rendered by the canonical `/designs/new` route
 * and the `/new` alias. Wrapped in Suspense for `useSearchParams`. */
export default function NewDesign() {
  return (
    <Suspense fallback={<div className="page text-slate-400">Loading…</div>}>
      <NewDesignInner />
    </Suspense>
  );
}
