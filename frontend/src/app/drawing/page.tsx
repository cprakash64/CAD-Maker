"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  DRAWING_ASSUMPTIONS_THRESHOLD,
  DRAWING_CONFIDENCE_THRESHOLD,
  type DrawingInterpretation,
  type ProviderStatus,
} from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { usePartPrompt } from "@/components/PartPromptOverlay";

const TEMPLATES = [
  "rectangular_bracket", "l_bracket", "enclosure", "spacer", "pipe_clamp",
  "drill_jig", "handle", "adapter_plate", "flanged_pipe_branch",
  "simple_gear_or_pulley", "inline_4_crankshaft",
];

export default function DrawingToCadPage() {
  const router = useRouter();
  const { user, loading } = useRequireAuth();
  const partPrompt = usePartPrompt();
  const [file, setFile] = useState<File | null>(null);
  const [hint, setHint] = useState("");
  const [override, setOverride] = useState("");
  const [interp, setInterp] = useState<DrawingInterpretation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<ProviderStatus | null>(null);

  useEffect(() => {
    api.providerStatus().then(setStatus).catch(() => {});
  }, []);

  // PRIMARY flow: interpret + generate in ONE action. Only stops at the
  // interpretation panel when the image isn't a generatable mechanical drawing.
  async function generateNow() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setInterp(null);
    try {
      const res = await api.generateFromDrawing(file, hint || undefined);
      if (res.generated && res.design) {
        router.push(`/studio/${res.design.id}`);
        return;
      }
      setInterp(res.interpretation);
      setError(
        "Couldn't auto-generate from this image — see the interpretation below. " +
          "Add a correction hint describing the part and try again."
      );
    } catch (e) {
      setError(
        e instanceof ApiError
          ? `${e.message}${e.endpoint ? ` [${e.status || "network"} · ${e.endpoint}]` : ""}`
          : `Generation failed: ${String(e)}`
      );
    } finally {
      setBusy(false);
    }
  }

  // Secondary: interpret only (transparency / review before generating).
  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setInterp(null);
    try {
      setInterp(await api.interpretDrawing(file, hint || undefined));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Interpretation failed");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!interp) return;
    setBusy(true);
    try {
      const chosen = { ...interp };
      if (override) chosen.suggested_object_type = override;
      const design = await api.confirmDrawing(chosen);
      router.push(`/studio/${design.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Generation failed");
      setBusy(false);
    }
  }

  if (loading || !user) return <div className="py-10 text-slate-400">Loading…</div>;

  const imageBlocked = !!status && !status.drawing_to_cad_enabled;
  const conf = interp?.overall_confidence ?? 0;
  const mechanical =
    !!interp && !!interp.suggested_object_type && !interp.unsupported_reason;
  // Fully specified + high confidence: generate without caveats.
  const actionable =
    !!interp &&
    (interp.actionable ??
      (mechanical &&
        interp.clarification_questions.length === 0 &&
        interp.missing_critical_dimensions.length === 0 &&
        conf >= DRAWING_CONFIDENCE_THRESHOLD));
  // ASSUMPTION-FIRST: a recognized mechanical drawing generates with inferred
  // defaults even when clarification questions are open.
  const generatableWithAssumptions =
    !!interp &&
    !actionable &&
    (interp.generate_with_assumptions_available ??
      (mechanical && conf >= DRAWING_ASSUMPTIONS_THRESHOLD));
  // Override lets the user pick a template when the model was unsure but they know.
  const canConfirmWithOverride = !!interp && !!override;

  return (
    <div className="page max-w-3xl space-y-5 lg:max-w-4xl">
      <div className="space-y-1.5">
        <span className="label block">Drawing → CAD</span>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-50 sm:text-3xl">
          Drawing-to-CAD Assist
        </h1>
        <p className="mt-1 max-w-2xl text-sm leading-relaxed text-slate-400">
          Extracts geometry and dimensions from a 2D drawing when possible. You
          confirm the detected type and assumptions before any CAD is generated —
          it is assistance, not exact conversion.
        </p>
        {status && (
          <p className="mt-1.5 inline-flex items-center gap-2 text-xs text-slate-500">
            Provider
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border border-edge bg-raised/50 px-2 py-0.5 ${
                status.image_understanding ? "text-accent" : "text-amber-300"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  status.image_understanding ? "bg-accent" : "bg-amber-400"
                }`}
              />
              {status.status_label}
            </span>
          </p>
        )}
      </div>

      {/* Unified flow: the Spotlight prompt overlay handles text + image in one
          surface. This is the recommended quick path; the detailed reviewer
          below stays available for confirming assumptions. */}
      <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-slate-100">Quick image prompt</h2>
          <p className="mt-0.5 text-xs leading-relaxed text-slate-400">
            Attach a sketch or drawing and describe it in one place — same engine,
            fewer steps.
          </p>
        </div>
        <button
          className="btn-primary shrink-0"
          onClick={() => partPrompt.open(undefined, { image: true })}
        >
          Open image prompt
        </button>
      </div>

      <div className="flex items-center gap-3">
        <span className="h-px flex-1 bg-edge" />
        <span className="label">Detailed reviewer</span>
        <span className="h-px flex-1 bg-edge" />
      </div>

      {imageBlocked && (
        <div className="rounded-md border border-red-500/50 bg-red-500/10 p-4 text-sm text-red-100">
          <p className="font-semibold">Image understanding is unavailable.</p>
          <p className="mt-1">
            The current provider (<code>{status?.provider}</code>) cannot read
            drawings. Set <code>LLM_PROVIDER=openai</code> with an API key to enable
            Drawing-to-CAD{status?.mock_allowed ? ", or set DEV_ALLOW_MOCK_DRAWING=true to use the text-hint workaround in development" : ""}.
          </p>
        </div>
      )}

      <div className={`card space-y-3 p-4 ${imageBlocked ? "opacity-50" : ""}`}>
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-sm"
          disabled={imageBlocked}
        />
        <label className="block text-xs text-slate-400">
          Correct interpretation {status && !status.image_understanding ? "(required in mock mode)" : "(optional, guides the model)"}
          <input
            className="input mt-1"
            placeholder="e.g. This is a flanged pipe branch with 12 holes per flange, 90mm main pipe"
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            disabled={imageBlocked}
          />
        </label>
        <div className="flex items-center gap-3">
          <button
            className="btn-primary"
            disabled={!file || busy || imageBlocked}
            onClick={generateNow}
          >
            {busy ? "Generating…" : "Generate CAD from drawing"}
          </button>
          <button
            className="btn-ghost text-xs"
            disabled={!file || busy || imageBlocked}
            onClick={upload}
          >
            Interpret only
          </button>
        </div>
        <p className="text-xs text-slate-400">
          Generation infers missing dimensions and lists every assumption on the
          design page. It only stops to ask when the image isn&apos;t a readable
          mechanical drawing.
        </p>
      </div>

      {error && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">
          {error}
        </div>
      )}

      {interp && (
        <div className="card space-y-3 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-300">
            Extracted interpretation
          </h2>

          <div className="flex items-center gap-2 text-sm">
            <span>Detected:</span>
            <span className="text-accent">
              {interp.detected_object_type ?? interp.suggested_object_type ?? "— unknown —"}
            </span>
            <span
              className={`rounded-full px-2 py-0.5 text-xs ${
                conf >= DRAWING_CONFIDENCE_THRESHOLD
                  ? "bg-emerald-500/20 text-emerald-200"
                  : "bg-amber-500/20 text-amber-200"
              }`}
            >
              confidence {(conf * 100).toFixed(0)}%
            </span>
          </div>

          {interp.provider_error && (
            <div className="rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-100">
              Provider error: {interp.provider_error}
            </div>
          )}

          {interp.interpretation_rationale && (
            <details className="text-xs text-slate-300">
              <summary className="cursor-pointer text-slate-400">Why this interpretation?</summary>
              <p className="mt-1">{interp.interpretation_rationale}</p>
            </details>
          )}

          <div className="text-xs text-slate-300">
            <div>Views: {interp.views.map((v) => v.view_type).join(", ") || "—"}</div>
            {Object.keys(interp.overall_dimensions).length > 0 && (
              <div>
                Dimensions:{" "}
                {Object.entries(interp.overall_dimensions).map(([k, v]) => `${k} ${v}`).join(", ")}
              </div>
            )}
            {interp.holes.length > 0 && (
              <div>
                Holes/bolts:{" "}
                {interp.holes.map((h) => h.callout ?? `${h.count}× ø${h.diameter ?? "?"}`).join(", ")}
              </div>
            )}
          </div>

          {interp.assumptions.length > 0 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-100">
              <div className="font-medium">Assumptions (verify):</div>
              <ul className="mt-1 list-disc pl-4">
                {interp.assumptions.map((a, i) => (
                  <li key={i}>{a.field}: {a.assumption}</li>
                ))}
              </ul>
            </div>
          )}

          {interp.missing_critical_dimensions.length > 0 && (
            <p className="text-xs text-red-200">
              Missing critical dimensions: {interp.missing_critical_dimensions.join(", ")}
            </p>
          )}

          {interp.clarification_questions.length > 0 && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-100">
              <div className="font-medium">
                {generatableWithAssumptions
                  ? "Open questions — safe defaults will be assumed:"
                  : "Needs clarification:"}
              </div>
              <ul className="mt-1 list-disc pl-4">
                {interp.clarification_questions.map((q, i) => <li key={i}>{q.question}</li>)}
              </ul>
              <p className="mt-2">
                {generatableWithAssumptions
                  ? "You can generate now — each open question becomes a listed assumption with a warning — or answer them in the “Correct interpretation” box and interpret again."
                  : "Describe the part in the “Correct interpretation” box above and interpret again."}
              </p>
            </div>
          )}

          {interp.unsupported_reason && (
            <p className="text-xs text-red-200">{interp.unsupported_reason}</p>
          )}

          {/* Template override (only when the user is confident) */}
          <label className="block text-xs text-slate-400">
            Override template (optional)
            <select
              className="input mt-1"
              value={override}
              onChange={(e) => setOverride(e.target.value)}
            >
              <option value="">— use detected —</option>
              {TEMPLATES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>

          {actionable || canConfirmWithOverride ? (
            <button className="btn-primary" disabled={busy} onClick={confirm}>
              Confirm &amp; generate CAD
            </button>
          ) : generatableWithAssumptions ? (
            <button className="btn-primary" disabled={busy} onClick={confirm}>
              {busy ? "Generating…" : "Generate CAD with assumptions"}
            </button>
          ) : (
            <button className="btn-ghost cursor-not-allowed opacity-60" disabled>
              Needs clarification — can&apos;t generate safely yet
            </button>
          )}
        </div>
      )}
    </div>
  );
}
