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
  assembly: "Concept assembly",
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

  // Critical-failure designs stay inspectable, but manufacturable exports are
  // blocked (and the backend returns 409 even if a button were clicked).
  const exportBlocked = design.validation_status === "critical_failure";
  const isAssembly = design.design_mode === "assembly";
  const assemblyComponents = design.dimension_report?.components ?? [];
  const assemblyEnvelope = design.bounding_box_mm;

  const vstatus = design.validation_status;
  const vmeta =
    vstatus === "critical_failure"
      ? { cls: "badge-fail", text: "Failed validation" }
      : vstatus === "warning"
        ? { cls: "badge-review", text: "Review" }
        : vstatus === "pass"
          ? { cls: "badge-pass", text: "Validated" }
          : null;

  return (
    <div className="space-y-4">
      {/* --- Top toolbar: identity + status + actions ----------------------- */}
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-edge pb-4">
        <div className="min-w-0">
          <Link href="/dashboard" className="text-xs text-slate-500 hover:text-slate-300">
            ← Designs
          </Link>
          <h1 className="mt-1 max-w-2xl truncate text-lg font-semibold text-slate-50">
            {design.prompt}
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span className="text-slate-400">{design.object_type ?? "unparsed"}</span>
            {design.bounding_box_mm && (
              <span className="stat text-slate-400">
                {design.bounding_box_mm.x} × {design.bounding_box_mm.y} ×{" "}
                {design.bounding_box_mm.z} mm
              </span>
            )}
            {design.route && (
              <span className="badge-neutral">
                {ROUTE_LABELS[design.route] ?? design.route}
              </span>
            )}
            {vmeta && <span className={vmeta.cls}>{vmeta.text}</span>}
            {design.auto_repaired && (
              <span className="badge-neutral">
                Auto-repaired{design.repair_attempts > 0 ? ` ×${design.repair_attempts}` : ""}
              </span>
            )}
            {DEV && design.provider && <span>· {design.provider}</span>}
            {DEV && design.generation_ms != null && (
              <span className="stat">· {design.generation_ms}ms</span>
            )}
          </div>
        </div>

        {!design.needs_clarification && !design.needs_decomposition &&
          (exportBlocked ? (
            <div className="banner-danger max-w-xs px-3 py-2 text-xs">
              <span className="font-semibold">Export blocked.</span> This design failed
              validation and can’t be exported as a manufacturable file.
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {design.exports.map((e) => (
                <button
                  key={e.fmt}
                  className="btn-ghost btn-sm"
                  onClick={() =>
                    downloadExport(id, e.fmt).catch(() => setError("Download failed"))
                  }
                >
                  ↓ {e.fmt.toUpperCase()}
                </button>
              ))}
              {design.spec && (
                <button className="btn-primary btn-sm" onClick={downloadPackage}>
                  ↓ CAD Package
                </button>
              )}
            </div>
          ))}
      </div>

      <MockModeBanner />

      {design.needs_decomposition && (
        <div className="card p-5">
          <div className="mb-2 flex items-center gap-2">
            <span className="badge-review">Complex assembly</span>
            <h2 className="text-sm font-semibold text-slate-100">
              This is a complex assembly — generate it one part at a time
            </h2>
          </div>
          <p className="text-sm leading-relaxed text-slate-400">
            {design.decomposition?.reason ??
              design.explanation ??
              "This describes a large multi-part assembly, which is beyond single-part generation. Break it into individual components and generate them one by one."}
          </p>

          {(design.decomposition?.components?.length ?? 0) > 0 && (
            <div className="mt-4">
              <h3 className="label mb-1.5">Suggested components</h3>
              <div className="flex flex-wrap gap-1.5">
                {design.decomposition!.components!.map((c) => (
                  <span key={c} className="badge-neutral">{c}</span>
                ))}
              </div>
            </div>
          )}

          {design.decomposition?.recommended_first && (
            <p className="mt-4 text-sm text-slate-300">
              <span className="label">Start here:</span>{" "}
              {design.decomposition.recommended_first}
            </p>
          )}

          {(design.decomposition?.examples?.length ?? 0) > 0 && (
            <div className="mt-4">
              <h3 className="label mb-1.5">Try one of these smaller parts</h3>
              <div className="grid gap-2 sm:grid-cols-2">
                {design.decomposition!.examples!.map((ex) => (
                  <Link
                    key={ex}
                    href={`/new?prompt=${encodeURIComponent(ex)}`}
                    className="surface p-3 text-left text-xs text-slate-300 transition-colors hover:border-accent/60"
                  >
                    {ex}
                  </Link>
                ))}
              </div>
            </div>
          )}

          <div className="mt-4">
            <Link href="/designs/new" className="btn-ghost btn-sm">
              New single part
            </Link>
          </div>
        </div>
      )}

      {design.needs_clarification && (
        <div className="banner-warn p-4">
          <p className="font-semibold">More information needed</p>
          {design.clarification_questions.length > 0 ? (
            <ul className="mt-1.5 space-y-1 text-sm">
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
              <button className="btn-primary btn-sm" onClick={generateWithDefaults} disabled={busy}>
                Generate with defaults
              </button>
            )}
            <Link
              href={`/new?prompt=${encodeURIComponent(design.prompt + " ")}`}
              className="btn-ghost btn-sm"
            >
              Refine prompt
            </Link>
          </div>
        </div>
      )}

      {error && <div className="banner-danger">{error}</div>}
      {notice && <div className="banner-warn">{notice}</div>}

      {!design.needs_clarification && !design.needs_decomposition && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
          {/* --- Centerpiece: CAD viewport + build narrative -------------- */}
          <div className="space-y-4">
            {isAssembly && (
              <div className="card p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="badge-neutral">Assembly</span>
                  <h2 className="text-sm font-semibold text-slate-100">
                    Simplified assembly generated
                  </h2>
                </div>
                <p className="text-xs leading-relaxed text-amber-200/90">
                  Concept CAD — a geometric first pass, <strong>not structurally
                  certified</strong> (no FEA / load analysis). Refine individual
                  parts before manufacturing.
                </p>
                {assemblyEnvelope && (
                  <p className="mt-2 text-xs text-slate-400">
                    Envelope:{" "}
                    <span className="stat text-slate-200">
                      {assemblyEnvelope.x} × {assemblyEnvelope.y} × {assemblyEnvelope.z} mm
                    </span>{" "}
                    · {assemblyComponents.length} components
                  </p>
                )}
                {assemblyComponents.length > 0 && (
                  <div className="mt-3">
                    <h3 className="label mb-1.5">Components</h3>
                    <div className="flex flex-wrap gap-1.5">
                      {assemblyComponents.map((c) => (
                        <span key={c.id} className="badge-neutral" title={c.section}>
                          {c.id.replace(/_/g, " ")}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
            <Studio3D
              mesh={design.preview}
              features={design.features ?? []}
              onSelect={setSelectedFeature}
            />

            {design.explanation && (
              <div className="card p-4">
                <h2 className="label mb-2">What was generated</h2>
                <p className="text-sm leading-relaxed text-slate-300">
                  {design.explanation}
                </p>
                {design.default_assumptions.length > 0 && (
                  <p className="mt-3 border-t border-edge pt-3 text-xs text-slate-500">
                    Built using documented defaults: {design.default_assumptions.join(" · ")}.{" "}
                    <Link
                      href={`/new?prompt=${encodeURIComponent(design.prompt + " ")}`}
                      className="text-accent hover:underline"
                    >
                      Refine prompt
                    </Link>
                  </p>
                )}
                {design.feature_graph_ops.length > 0 && (
                  <p className="mt-2 text-xs text-slate-500">
                    Composed from {design.feature_graph_ops.length} parametric operations.
                  </p>
                )}
              </div>
            )}

            {/* Verification detail (audit + semantic checks), collapsed. */}
            {(design.feature_audit.length > 0 || design.semantic_checks.length > 0) && (
              <details
                className="card p-4 text-sm"
                open={design.feature_audit_passed === false || design.semantic_passed === false}
              >
                <summary className="label cursor-pointer">Verification detail</summary>
                {design.feature_audit.length > 0 && (
                  <ul className="mt-3 space-y-1">
                    {design.feature_audit.map((i, idx) => (
                      <li key={`${i.feature_id}-${idx}`} className="flex items-center gap-2 text-xs">
                        <span className={i.satisfied ? "text-emerald-400" : "text-amber-400"}>
                          {i.satisfied ? "✓" : "⚠"}
                        </span>
                        <code className="stat text-slate-500">{i.feature_id}</code>
                        <span className="text-slate-300">
                          {i.forbidden ? `must NOT have: ${i.requirement}` : i.requirement}
                        </span>
                        <span className="text-slate-500">{i.detail}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {design.semantic_checks.length > 0 && (
                  <ul className="mt-3 space-y-1 border-t border-edge pt-3">
                    {design.semantic_checks.map((c) => {
                      const isCritical = c.severity === "error" || c.severity === "critical";
                      const tone = c.passed
                        ? "text-emerald-400"
                        : isCritical
                          ? "text-red-400"
                          : "text-amber-400";
                      return (
                        <li key={c.name} className="flex items-center gap-2 text-xs">
                          <span className={tone}>{c.passed ? "✓" : isCritical ? "✗" : "⚠"}</span>
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
                )}
              </details>
            )}

            <FeedbackWidget designId={id} existing={design.my_feedback} />
          </div>

          {/* --- Validation + properties + controls ---------------------- */}
          <div className="space-y-4">
            <ValidationPanel
              report={design.dimension_report}
              printReadiness={design.print_readiness}
              withinTolerance={design.dimensions_within_tolerance}
              validationStatus={design.validation_status}
              criticalFailures={design.validation_critical_failures}
              assumptions={design.assumptions ?? []}
              designMode={design.design_mode}
            />
            <ChecksPanel checks={design.checks} />

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
              <HoleTable holes={design.spec?.holes ?? []} onApply={applyHoles} busy={busy} />
            )}

            <div className="card p-4">
              <h2 className="label mb-2">Properties</h2>
              <dl className="space-y-1.5 text-xs">
                <Prop k="Type" v={design.object_type ?? "—"} />
                <Prop k="Material" v={design.spec?.material ?? "—"} />
                <Prop k="Method" v={design.spec?.manufacturing_method ?? "—"} />
                <Prop k="Units" v={design.spec?.units ?? "mm"} />
                {design.preview && (
                  <Prop k="Mesh" v={`${design.preview.triangle_count} triangles`} />
                )}
                <Prop k="Hash" v={design.spec_hash ?? "—"} mono />
              </dl>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Prop({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className={`truncate text-right text-slate-300 ${mono ? "stat" : ""}`}>{v}</dd>
    </div>
  );
}
