"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import ChecksPanel from "@/components/ChecksPanel";
import CircleEditPanel from "@/components/CircleEditPanel";
import FeedbackWidget from "@/components/FeedbackWidget";
import HoleTable from "@/components/HoleTable";
import MockModeBanner from "@/components/MockModeBanner";
import ModifyBox from "@/components/ModifyBox";
import ParameterSidebar from "@/components/ParameterSidebar";
import ValidationPanel from "@/components/ValidationPanel";
import type { SelectedFeature } from "@/components/Studio3D";
import { api, ApiError, getToken } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { Design, Hole } from "@/lib/types";

// Dynamic + ssr:false keeps Three.js off the server; Studio3D imports Viewer3D
// directly so refs (view toolbar + circle projection) forward correctly.
const Studio3D = dynamic(() => import("@/components/Studio3D"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[460px] items-center justify-center rounded-xl border border-edge bg-[#0a0f1f] text-slate-400">
      Loading viewer…
    </div>
  ),
});

const DEV = process.env.NODE_ENV !== "production";

async function downloadExport(designId: string, fmt: string) {
  // Owner-checked endpoint requires the bearer token, so fetch as a blob.
  const res = await fetch(api.downloadUrl(designId, fmt), {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (!res.ok) throw new ApiError("Download failed", res.status);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `part.${fmt}`;
  a.click();
  URL.revokeObjectURL(url);
}

const ROUTE_LABELS: Record<string, string> = {
  cad_plan: "Feature-graph CAD",
  precision_template: "Precision template",
  feature_graph: "Flexible CAD graph",
  scad_generator: "SCAD generator",
  clarification: "Needs clarification",
};

export default function StudioPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const { user, loading: authLoading } = useRequireAuth();
  const [design, setDesign] = useState<Design | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFeature, setSelectedFeature] = useState<SelectedFeature | null>(null);

  useEffect(() => {
    if (!user) return;
    api
      .getDesign(id)
      .then(setDesign)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [id, user]);

  const regenerate = useCallback(
    async (next: Record<string, number>, holes?: Hole[]) => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        setDesign(await api.regenerate(id, next, holes));
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Regeneration failed");
      } finally {
        setBusy(false);
      }
    },
    [id]
  );

  const applyHoles = useCallback(
    async (holes: Hole[]) => {
      if (!design) return;
      await regenerate(design.editable_parameters, holes);
    },
    [design, regenerate]
  );

  const circleEdit = useCallback(
    async (instruction: string) => {
      if (!selectedFeature) return;
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const updated = await api.circleEdit(id, {
          selected: {
            entity_type: selectedFeature.entity_type,
            entity_id: selectedFeature.entity_id,
            label: selectedFeature.label,
          },
          instruction,
        });
        setDesign(updated);
        if (updated.clarification_question) setNotice(updated.clarification_question);
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Edit failed");
      } finally {
        setBusy(false);
      }
    },
    [id, selectedFeature]
  );

  const downloadPackage = useCallback(async () => {
    try {
      const res = await fetch(api.packageUrl(id), {
        headers: { Authorization: `Bearer ${getToken() ?? ""}` },
      });
      if (!res.ok) throw new Error();
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${design?.object_type ?? "part"}_package.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("Package download failed");
    }
  }, [id, design]);

  const modify = useCallback(
    async (prompt: string) => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const updated = await api.modify(id, prompt);
        setDesign(updated);
        if (updated.clarification_question && updated.spec) {
          setNotice(updated.clarification_question);
        }
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Edit failed");
      } finally {
        setBusy(false);
      }
    },
    [id]
  );

  const generateWithDefaults = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setDesign(await api.generateWithDefaults(id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Generation failed");
    } finally {
      setBusy(false);
    }
  }, [id]);

  if (authLoading || !user) return <div className="py-10 text-slate-400">Loading…</div>;
  if (error && !design) {
    return (
      <div className="rounded-md border border-red-500/40 bg-red-500/10 p-4 text-red-200">
        {error}
      </div>
    );
  }
  if (!design) return <div className="py-10 text-slate-400">Loading design…</div>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link href="/dashboard" className="text-xs text-slate-400 hover:underline">
            ← Dashboard
          </Link>
          <h1 className="mt-1 truncate text-xl font-bold">{design.prompt}</h1>
          <p className="text-xs text-slate-400">
            {design.object_type ?? "unparsed"}
            {design.bounding_box_mm &&
              ` · ${design.bounding_box_mm.x} × ${design.bounding_box_mm.y} × ${design.bounding_box_mm.z} mm`}
            {DEV && design.provider && ` · provider: ${design.provider}`}
            {DEV && design.generation_ms != null && ` · ${design.generation_ms}ms`}
          </p>
        </div>
        <div className="flex gap-2">
          {design.exports.map((e) => (
            <button
              key={e.fmt}
              className="btn-ghost"
              onClick={() => downloadExport(id, e.fmt).catch(() => setError("Download failed"))}
            >
              ↓ {e.fmt.toUpperCase()}
            </button>
          ))}
          {design.spec && (
            <button className="btn-primary" onClick={downloadPackage}>
              ↓ CAD Package
            </button>
          )}
        </div>
      </div>

      <MockModeBanner />

      {design.needs_clarification && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-4 text-amber-100">
          <p className="font-medium">A bit more info needed</p>
          {design.clarification_questions.length > 0 ? (
            <ul className="mt-1 space-y-1 text-sm">
              {design.clarification_questions.map((q, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-amber-300">•</span>
                  <span>{q}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm">{design.clarification_question}</p>
          )}
          {design.clarification_questions.length === 0 &&
            design.missing_required.length > 0 && (
              <p className="mt-1 text-xs text-amber-200/90">
                Missing: {design.missing_required.join(", ")}
              </p>
            )}
          <div className="mt-3 flex gap-2">
            {design.can_generate_with_defaults && (
              <button className="btn-primary" onClick={generateWithDefaults} disabled={busy}>
                Generate with defaults
              </button>
            )}
            <Link
              href={`/new?prompt=${encodeURIComponent(design.prompt + " ")}`}
              className="btn-ghost"
            >
              Refine prompt
            </Link>
          </div>
        </div>
      )}

      {/* Route + auto-repair + export-format transparency (v0.4-GEN). */}
      {!design.needs_clarification && design.route && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="rounded-full border border-slate-600 bg-slate-800 px-2 py-0.5 text-slate-200">
            Route: {ROUTE_LABELS[design.route] ?? design.route}
          </span>
          {design.auto_repaired && (
            <span className="rounded-full border border-emerald-500/50 bg-emerald-500/10 px-2 py-0.5 text-emerald-200">
              Auto-repaired generation{design.repair_attempts > 0 ? ` (${design.repair_attempts}×)` : ""}
            </span>
          )}
          {design.semantic_passed !== null && (
            <span
              className={`rounded-full border px-2 py-0.5 ${
                !design.semantic_passed
                  ? "border-red-500/50 bg-red-500/10 text-red-200"
                  : design.warnings.length > 0
                    ? "border-amber-500/50 bg-amber-500/10 text-amber-200"
                    : "border-emerald-500/50 bg-emerald-500/10 text-emerald-200"
              }`}
            >
              {!design.semantic_passed
                ? "Checks failed"
                : design.warnings.length > 0
                  ? `Generated with ${design.warnings.length} warning${design.warnings.length > 1 ? "s" : ""}`
                  : "Checks passed"}
            </span>
          )}
        </div>
      )}

      {/* Assumption-first: non-blocking warnings on a compiled model. */}
      {!design.needs_clarification && design.warnings.length > 0 && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
          <p className="font-medium">Generated with warnings</p>
          <p className="mt-0.5 text-xs text-amber-200/90">
            These are advisory only — the model compiled and STL/STEP are ready to download.
          </p>
          <ul className="mt-2 space-y-1 text-xs">
            {design.warnings.map((w, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="text-amber-300">⚠</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Feature-level audit: requested mechanical features vs. the model. */}
      {!design.needs_clarification && design.feature_audit.length > 0 && (
        <details
          className="rounded-md border border-slate-700 bg-slate-900/60 p-3 text-sm"
          open={design.feature_audit_passed === false}
        >
          <summary className="cursor-pointer font-medium text-slate-200">
            Feature audit — {design.feature_audit.filter((i) => i.satisfied).length}/
            {design.feature_audit.length} requested features verified
          </summary>
          <ul className="mt-2 space-y-1">
            {design.feature_audit.map((i, idx) => (
              <li key={`${i.feature_id}-${idx}`} className="flex items-center gap-2 text-xs">
                <span className={i.satisfied ? "text-emerald-400" : "text-amber-400"}>
                  {i.satisfied ? "✓" : "⚠"}
                </span>
                <code className="text-slate-400">{i.feature_id}</code>
                <span className="text-slate-300">
                  {i.forbidden ? `must NOT have: ${i.requirement}` : i.requirement}
                </span>
                <span className="text-slate-500">{i.detail}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* Semantic verification detail (v0.5-GEN2). */}
      {!design.needs_clarification && design.semantic_checks.length > 0 && (
        <details className="rounded-md border border-slate-700 bg-slate-900/60 p-3 text-sm">
          <summary className="cursor-pointer font-medium text-slate-200">
            Semantic verification — {design.semantic_checks.filter((c) => c.passed).length}/
            {design.semantic_checks.length} checks passed
          </summary>
          <ul className="mt-2 space-y-1">
            {design.semantic_checks.map((c) => {
              const tone = c.passed
                ? "text-emerald-400"
                : c.severity === "error"
                  ? "text-red-400"
                  : "text-amber-400";
              return (
                <li key={c.name} className="flex items-center gap-2 text-xs">
                  <span className={tone}>{c.passed ? "✓" : c.severity === "error" ? "✗" : "⚠"}</span>
                  <span className="text-slate-300">{c.name.replace(/_/g, " ")}</span>
                  {!c.passed && c.expected && (
                    <span className="text-slate-500">
                      expected {c.expected}, got {c.actual}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}

      {/* Flexible CAD graph provenance. */}
      {!design.needs_clarification && design.feature_graph_ops.length > 0 && (
        <div className="rounded-md border border-violet-500/40 bg-violet-500/10 p-3 text-sm text-violet-100">
          <span className="font-medium">Generated by flexible CAD graph. </span>
          Built from {design.feature_graph_ops.length} operations:{" "}
          <span className="text-violet-200/90">{design.feature_graph_ops.join(" → ")}</span>
        </div>
      )}

      {/* Generate-first transparency: we built it using documented defaults. */}
      {!design.needs_clarification && design.default_assumptions.length > 0 && (
        <div className="rounded-md border border-sky-500/40 bg-sky-500/10 p-3 text-sm text-sky-100">
          <span className="font-medium">Generated with defaults. </span>
          {design.default_assumptions.join(" · ")}{" "}
          <Link
            href={`/new?prompt=${encodeURIComponent(design.prompt + " ")}`}
            className="underline"
          >
            Refine prompt
          </Link>
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
          {notice}
        </div>
      )}

      {!design.needs_clarification && (
        <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
          <div className="space-y-4">
            <Studio3D
              mesh={design.preview}
              features={design.features ?? []}
              onSelect={setSelectedFeature}
            />
            {design.explanation && (
              <div className="card p-4">
                <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-300">
                  What was generated
                </h2>
                <p className="text-sm leading-relaxed text-slate-200">
                  {design.explanation}
                </p>
              </div>
            )}
            <ValidationPanel
              report={design.dimension_report}
              printReadiness={design.print_readiness}
              withinTolerance={design.dimensions_within_tolerance}
              assumptions={design.assumptions ?? []}
            />
            <ChecksPanel checks={design.checks} />
            <FeedbackWidget designId={id} existing={design.my_feedback} />
          </div>

          <div className="space-y-4">
            {/* Plain-English edit + parameter editing apply to template-spec
                designs. Feature-graph (CadPlan) parts are re-edited by prompt. */}
            {design.spec && <ModifyBox onSubmit={modify} busy={busy} />}
            <CircleEditPanel selected={selectedFeature} onApply={circleEdit} busy={busy} />
            {Object.keys(design.editable_parameters).length > 0 && (
              <ParameterSidebar
                parameters={design.editable_parameters}
                onRegenerate={(d) => regenerate(d)}
                busy={busy}
              />
            )}
            {design.spec && (
              <HoleTable
                holes={design.spec?.holes ?? []}
                onApply={applyHoles}
                busy={busy}
              />
            )}

            <div className="card p-4 text-xs text-slate-400">
              <div className="mb-1 font-semibold text-slate-300">Details</div>
              <div>Type: {design.object_type ?? "—"}</div>
              <div>Material: {design.spec?.material ?? "—"}</div>
              <div>Method: {design.spec?.manufacturing_method ?? "—"}</div>
              <div>Units: {design.spec?.units ?? "mm"}</div>
              {design.preview && <div>Mesh: {design.preview.triangle_count} triangles</div>}
              <div className="mt-1 break-all">Hash: {design.spec_hash}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
