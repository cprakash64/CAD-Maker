"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { ONBOARDING_EXAMPLES } from "@/lib/examples";
import MockModeBanner from "@/components/MockModeBanner";

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
    return <div className="py-10 text-slate-400">Loading…</div>;
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5 py-8">
      <div>
        <span className="label">New part</span>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-50">
          Describe what you need
        </h1>
      </div>
      <MockModeBanner />

      <div className="card p-4">
        <label className="label mb-2 block">Specification</label>
        <textarea
          className="input min-h-[150px] resize-y leading-relaxed"
          placeholder="e.g. Wall-mounted bracket for a 25 mm pipe with two M6 screw holes, 5 mm thick, 80 mm wide."
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <p className="mt-2 text-xs text-slate-500">
          Include key dimensions, hole sizes (e.g. <code className="stat text-slate-300">M6</code>),
          thickness and material where you know them. Missing details are filled with
          documented defaults and shown back to you.
        </p>
        {error && <div className="banner-danger mt-3">{error}</div>}
        <div className="mt-3">
          <button
            className="btn-primary"
            onClick={submit}
            disabled={busy || !prompt.trim()}
          >
            {busy ? "Generating…" : "Generate part"}
          </button>
        </div>
      </div>

      <div className="space-y-2">
        <h2 className="label">Or start from an example</h2>
        <div className="grid gap-2 sm:grid-cols-2">
          {ONBOARDING_EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              className="surface p-3 text-left transition-colors hover:border-accent/60"
              onClick={() => setPrompt(ex.prompt)}
            >
              <div className="label text-slate-300">{ex.label}</div>
              <div className="mt-1 text-xs text-slate-400">{ex.prompt}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Shared New Design page body, rendered by the canonical `/designs/new` route
 * and the `/new` alias. Wrapped in Suspense for `useSearchParams`. */
export default function NewDesign() {
  return (
    <Suspense fallback={<div className="py-8 text-slate-400">Loading…</div>}>
      <NewDesignInner />
    </Suspense>
  );
}
