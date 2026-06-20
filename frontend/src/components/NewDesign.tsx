"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
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

  async function submit() {
    if (!prompt.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const design = await api.createDesign(prompt.trim());
      router.push(`/studio/${design.id}`);
    } catch (e) {
      // Surface the exact backend reason (validation detail), never a vague
      // "something went wrong".
      setError(e instanceof ApiError ? e.message : `Generation failed: ${String(e)}`);
      setBusy(false);
    }
  }

  if (loading || !user) {
    return <div className="py-10 text-slate-400">Loading…</div>;
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5 py-8">
      <h1 className="text-2xl font-bold">New design</h1>
      <MockModeBanner />
      <p className="text-slate-300">
        Describe the mechanical part you need. Include key dimensions, hole sizes
        (e.g. <code>M6</code>), thickness and material if you know them.
      </p>
      <textarea
        className="input min-h-[140px] resize-y"
        placeholder="e.g. Wall-mounted bracket for a 25 mm pipe with two M6 screw holes, 5 mm thick, 80 mm wide."
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
      />
      {error && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">
          {error}
        </div>
      )}
      <button className="btn-primary" onClick={submit} disabled={busy || !prompt.trim()}>
        {busy ? "Generating…" : "Generate part"}
      </button>

      <div className="space-y-2 pt-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
          Or start from an example
        </h2>
        <div className="grid gap-2 sm:grid-cols-2">
          {ONBOARDING_EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              className="card p-3 text-left hover:border-accent"
              onClick={() => setPrompt(ex.prompt)}
            >
              <div className="text-sm font-medium text-accent">{ex.label}</div>
              <div className="mt-1 text-xs text-slate-300">{ex.prompt}</div>
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
