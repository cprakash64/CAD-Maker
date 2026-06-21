"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import ChecksPanel from "@/components/ChecksPanel";
import CircleEditPanel from "@/components/CircleEditPanel";
import FeedbackWidget from "@/components/FeedbackWidget";
import HoleTable from "@/components/HoleTable";
import ModifyBox from "@/components/ModifyBox";
import NewPartModal, { EXAMPLE_CHIPS } from "@/components/NewPartModal";
import ParameterSidebar from "@/components/ParameterSidebar";
import ValidationPanel from "@/components/ValidationPanel";
import type { SelectedFeature } from "@/components/Studio3D";
import { api, ApiError, getToken } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type { Design, DimensionMeasured, DesignSummary, Hole } from "@/lib/types";

const Studio3D = dynamic(() => import("@/components/Studio3D"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[420px] items-center justify-center rounded-lg border border-edge bg-viewport text-slate-500">
      Loading viewer…
    </div>
  ),
});

const ROUTE_LABELS: Record<string, string> = {
  cad_plan: "Feature-graph CAD",
  precision_template: "Precision template",
  feature_graph: "Flexible CAD graph",
  scad_generator: "SCAD generator",
  clarification: "Needs clarification",
  assembly: "Concept assembly",
  needs_decomposition: "Decompose",
};

// Visual-only materials (changes viewer appearance, not mass/metadata).
const MATERIALS: { name: string; color: string }[] = [
  { name: "Aluminum", color: "#b9bfc6" },
  { name: "Steel", color: "#8a9099" },
  { name: "Brass", color: "#c2974a" },
  { name: "PLA", color: "#d8d2c4" },
  { name: "ABS", color: "#3c4046" },
];

async function downloadExport(designId: string, fmt: string) {
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

export default function StudioPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const router = useRouter();
  const { user, loading: authLoading } = useRequireAuth();
  const [design, setDesign] = useState<Design | null>(null);
  const [designs, setDesigns] = useState<DesignSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFeature, setSelectedFeature] = useState<SelectedFeature | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [materialColor, setMaterialColor] = useState<string | undefined>(undefined);
  const [materialName, setMaterialName] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    setMaterialColor(undefined);
    setMaterialName(null);
    api.getDesign(id).then(setDesign).catch((e) =>
      setError(e instanceof ApiError ? e.message : String(e))
    );
  }, [id, user]);

  useEffect(() => {
    if (!user) return;
    api.listDesigns().then(setDesigns).catch(() => {});
  }, [user, design]);

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
        if (updated.clarification_question && updated.spec) setNotice(updated.clarification_question);
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

  const share = useCallback(() => {
    if (typeof window === "undefined") return;
    const p = navigator.clipboard?.writeText(window.location.href);
    if (p) p.then(() => setNotice("Link copied to clipboard."), () => {});
  }, []);

  if (authLoading || !user) return <div className="page text-slate-400">Loading…</div>;
  if (error && !design) {
    return <div className="page"><div className="banner-danger">{error}</div></div>;
  }
  if (!design) return <div className="page text-slate-400">Loading design…</div>;

  const exportBlocked = design.validation_status === "critical_failure";
  const isAssembly = design.design_mode === "assembly";
  const measured = design.dimension_report?.measured ?? null;
  const hasModel = !design.needs_clarification && !design.needs_decomposition;

  const vmeta =
    design.validation_status === "critical_failure"
      ? { cls: "badge-fail", text: "Failed" }
      : design.validation_status === "warning"
        ? { cls: "badge-review", text: "Warning" }
        : design.validation_status === "pass"
          ? { cls: "badge-pass", text: "Pass" }
          : null;

  const partName = design.object_type
    ? design.object_type.replace(/_/g, " ")
    : design.prompt.slice(0, 40);

  return (
    <div className="flex flex-col lg:h-[calc(100vh-52px)]">
      {/* --- Workspace toolbar: breadcrumb + actions ----------------------- */}
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-edge bg-panel px-4 py-2">
        <div className="flex min-w-0 items-center gap-2 text-sm">
          <Link href="/dashboard" className="text-slate-500 hover:text-slate-300">
            Workspace
          </Link>
          <span className="text-slate-600">/</span>
          <span className="truncate font-medium text-slate-100">{partName}</span>
          <span className="badge-neutral hidden sm:inline-flex">v1</span>
          {vmeta && <span className={vmeta.cls}>{vmeta.text}</span>}
        </div>
        <div className="flex items-center gap-2">
          {design.route && (
            <span className="badge-neutral hidden md:inline-flex">
              {ROUTE_LABELS[design.route] ?? design.route}
            </span>
          )}
          <button className="btn-ghost btn-sm" onClick={share}>Share</button>
          <button className="btn-primary btn-sm" onClick={() => setModalOpen(true)}>
            + New part
          </button>
        </div>
      </div>

      {/* --- 3-column workspace ------------------------------------------- */}
      <div className="grid min-h-0 flex-1 gap-px bg-edge lg:grid-cols-[230px_minmax(0,1fr)_360px]">
        {/* Left: parts list */}
        <aside className="flex flex-col bg-panel lg:overflow-y-auto">
          <div className="p-3">
            <button className="btn-primary w-full" onClick={() => setModalOpen(true)}>
              + New part
            </button>
          </div>
          <div className="flex-1 space-y-1 px-2 pb-2">
            <div className="label px-1 pb-1">Parts</div>
            {designs.map((d) => (
              <Link
                key={d.id}
                href={`/studio/${d.id}`}
                className={`block rounded-md border px-2.5 py-2 text-xs transition-colors ${
                  d.id === id
                    ? "border-accent/50 bg-raised text-slate-100"
                    : "border-transparent text-slate-400 hover:border-edge hover:bg-raised/60"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">
                    {d.object_type ? d.object_type.replace(/_/g, " ") : d.prompt}
                  </span>
                  <StatusDot summary={d} />
                </div>
                <div className="mt-0.5 truncate text-[11px] text-slate-500">{d.prompt}</div>
              </Link>
            ))}
            {designs.length === 0 && (
              <p className="px-1 text-xs text-slate-500">No parts yet.</p>
            )}
          </div>
          <div className="border-t border-edge px-3 py-2 text-[11px] text-slate-500">
            {designs.length} part{designs.length === 1 ? "" : "s"} in workspace
          </div>
        </aside>

        {/* Center: viewport + states + composer */}
        <section className="flex min-h-0 flex-col bg-ink">
          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {notice && <div className="banner-info">{notice}</div>}
            {error && <div className="banner-danger">{error}</div>}

            {design.needs_decomposition && (
              <DecompositionCard design={design} />
            )}

            {design.needs_clarification && (
              <ClarificationCard
                design={design}
                busy={busy}
                onGenerateDefaults={generateWithDefaults}
              />
            )}

            {hasModel && (
              <>
                {isAssembly && <AssemblyCaveat design={design} />}
                {/* Title chip over the viewport. */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-100">{partName}</span>
                    {vmeta && <span className={vmeta.cls}>{vmeta.text}</span>}
                  </div>
                  {design.bounding_box_mm && (
                    <span className="stat text-xs text-slate-500">
                      {design.bounding_box_mm.x} × {design.bounding_box_mm.y} ×{" "}
                      {design.bounding_box_mm.z} mm
                    </span>
                  )}
                </div>
                <Studio3D
                  mesh={design.preview}
                  features={design.features ?? []}
                  onSelect={setSelectedFeature}
                  materialColor={materialColor}
                  viewerClassName="relative h-[440px] w-full overflow-hidden rounded-lg border border-edge bg-viewport xl:h-[560px]"
                />

                {design.explanation && (
                  <div className="card p-4">
                    <h2 className="label mb-2">What was generated</h2>
                    <p className="text-sm leading-relaxed text-slate-300">{design.explanation}</p>
                  </div>
                )}

                {(design.feature_audit.length > 0 || design.semantic_checks.length > 0) && (
                  <VerificationDetail design={design} />
                )}

                <FeedbackWidget designId={id} existing={design.my_feedback} />
              </>
            )}
          </div>

          {/* Bottom prompt composer (generates a NEW part). */}
          <BottomComposer onError={setError} />
        </section>

        {/* Right: inspector */}
        <aside className="space-y-4 bg-panel p-4 lg:overflow-y-auto">
          {hasModel ? (
            <>
              <Section title="Validation / Print readiness">
                <ValidationPanel
                  report={design.dimension_report}
                  printReadiness={design.print_readiness}
                  withinTolerance={design.dimensions_within_tolerance}
                  validationStatus={design.validation_status}
                  criticalFailures={design.validation_critical_failures}
                  assumptions={design.assumptions ?? []}
                  designMode={design.design_mode}
                />
                {design.checks.length > 0 && <ChecksPanel checks={design.checks} />}
              </Section>

              <Section title="Measured">
                <MeasuredPanel design={design} measured={measured} />
              </Section>

              <Section title="Parameters">
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
                {!design.spec && Object.keys(design.editable_parameters).length === 0 && (
                  <p className="text-xs text-slate-500">
                    This part regenerates from its description. Use “+ New part” to refine it.
                  </p>
                )}
              </Section>

              <Section title="Material">
                <MaterialChips
                  current={design.spec?.material ?? null}
                  selected={materialName}
                  onSelect={(name, color) => {
                    setMaterialName(name);
                    setMaterialColor(color);
                  }}
                />
              </Section>

              <Section title="Export">
                <ExportPanel
                  design={design}
                  blocked={exportBlocked}
                  onDownload={(fmt) =>
                    downloadExport(id, fmt).catch(() => setError("Download failed"))
                  }
                  onPackage={downloadPackage}
                />
              </Section>
            </>
          ) : (
            <p className="text-sm text-slate-500">
              No model yet — resolve the prompt on the left to inspect a part.
            </p>
          )}
        </aside>
      </div>

      <NewPartModal open={modalOpen} onClose={() => setModalOpen(false)} />
    </div>
  );
}

/* ----------------------------- subcomponents ----------------------------- */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <h2 className="label">{title}</h2>
      {children}
    </div>
  );
}

function StatusDot({ summary }: { summary: DesignSummary }) {
  const cls = summary.needs_clarification
    ? "bg-amber-400"
    : summary.export_ready
      ? "bg-accent"
      : "bg-slate-600";
  const title = summary.needs_clarification
    ? "Needs info"
    : summary.export_ready
      ? "Ready"
      : "Draft";
  return <span className={`h-2 w-2 shrink-0 rounded-full ${cls}`} title={title} />;
}

function MeasuredPanel({
  design,
  measured,
}: {
  design: Design;
  measured: DimensionMeasured | null;
}) {
  const bb = design.bounding_box_mm;
  const volume = measured?.volume_mm3;
  const holeCount = measured?.hole_count ?? measured?.component_count;
  const holeLabel = design.design_mode === "assembly" ? "Components" : "Holes";
  const tol = design.dimension_report?.tolerance?.length_tolerance_mm;
  return (
    <div className="card p-4">
      <dl className="space-y-1.5 text-xs">
        <Row k="Bounding box" v={bb ? `${bb.x} × ${bb.y} × ${bb.z} mm` : "—"} mono />
        <Row
          k="Volume"
          v={typeof volume === "number" ? `${volume.toFixed(0)} mm³` : "—"}
          mono
        />
        <Row k={holeLabel} v={typeof holeCount === "number" ? String(holeCount) : "—"} mono />
        <Row
          k="Tolerance"
          v={typeof tol === "number" ? `±${tol} mm` : design.design_mode === "assembly" ? "approx." : "—"}
          mono
        />
        {typeof design.dimensions_within_tolerance === "boolean" && (
          <Row
            k="Within tolerance"
            v={design.dimensions_within_tolerance ? "Yes" : "Drift"}
          />
        )}
      </dl>
    </div>
  );
}

function MaterialChips({
  current,
  selected,
  onSelect,
}: {
  current: string | null;
  selected: string | null;
  onSelect: (name: string, color: string) => void;
}) {
  return (
    <div className="card p-4">
      <div className="flex flex-wrap gap-1.5">
        {MATERIALS.map((m) => (
          <button
            key={m.name}
            type="button"
            onClick={() => onSelect(m.name, m.color)}
            className={`flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors ${
              selected === m.name
                ? "border-accent/60 bg-raised text-slate-100"
                : "border-edge text-slate-400 hover:bg-raised"
            }`}
          >
            <span className="h-3 w-3 rounded-sm border border-black/30" style={{ background: m.color }} />
            {m.name}
          </button>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-slate-500">
        Appearance only — does not change mass or material metadata
        {current ? ` (current: ${current})` : ""}.
      </p>
    </div>
  );
}

function ExportPanel({
  design,
  blocked,
  onDownload,
  onPackage,
}: {
  design: Design;
  blocked: boolean;
  onDownload: (fmt: string) => void;
  onPackage: () => void;
}) {
  if (blocked) {
    return (
      <div className="banner-danger text-xs">
        <span className="font-semibold">Export blocked.</span> This design failed
        validation and can’t be exported as a manufacturable file.
      </div>
    );
  }
  return (
    <div className="card space-y-2 p-4">
      <div className="grid grid-cols-2 gap-2">
        {design.exports.map((e) => (
          <button key={e.fmt} className="btn-ghost btn-sm" onClick={() => onDownload(e.fmt)}>
            ↓ {e.fmt.toUpperCase()}
          </button>
        ))}
      </div>
      <button className="btn-primary w-full" onClick={onPackage} disabled={design.exports.length === 0}>
        ↓ CAD Package
      </button>
      {design.exports.length === 0 && (
        <p className="text-[11px] text-slate-500">No exports available for this design.</p>
      )}
    </div>
  );
}

function AssemblyCaveat({ design }: { design: Design }) {
  const comps = design.dimension_report?.components ?? [];
  return (
    <div className="card p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="badge-neutral">Concept assembly</span>
        <h2 className="text-sm font-semibold text-slate-100">Simplified assembly generated</h2>
      </div>
      <p className="text-xs leading-relaxed text-amber-200/90">
        Concept CAD — a geometric first pass, <strong>not structurally certified</strong>{" "}
        (no FEA / load analysis). Refine individual parts before manufacturing.
      </p>
      {comps.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {comps.map((c) => (
            <span key={c.id} className="badge-neutral" title={c.section}>
              {c.id.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function DecompositionCard({ design }: { design: Design }) {
  return (
    <div className="card p-5">
      <div className="mb-2 flex items-center gap-2">
        <span className="badge-review">Complex assembly</span>
        <h2 className="text-sm font-semibold text-slate-100">
          Generate this one part at a time
        </h2>
      </div>
      <p className="text-sm leading-relaxed text-slate-400">
        {design.decomposition?.reason ??
          design.explanation ??
          "This describes a large multi-part assembly. Break it into individual components and generate them one by one."}
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
          <span className="label">Start here:</span> {design.decomposition.recommended_first}
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
    </div>
  );
}

function ClarificationCard({
  design,
  busy,
  onGenerateDefaults,
}: {
  design: Design;
  busy: boolean;
  onGenerateDefaults: () => void;
}) {
  return (
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
      {design.clarification_questions.length === 0 && design.missing_required.length > 0 && (
        <p className="mt-1 text-xs text-amber-200/90">
          Missing: {design.missing_required.join(", ")}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        {design.can_generate_with_defaults && (
          <button className="btn-primary btn-sm" onClick={onGenerateDefaults} disabled={busy}>
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
  );
}

function VerificationDetail({ design }: { design: Design }) {
  return (
    <details
      className="card p-4 text-sm"
      open={design.feature_audit_passed === false || design.semantic_passed === false}
    >
      <summary className="label cursor-pointer">Verification detail</summary>
      {design.feature_audit.length > 0 && (
        <ul className="mt-3 space-y-1">
          {design.feature_audit.map((i, idx) => (
            <li key={`${i.feature_id}-${idx}`} className="flex items-center gap-2 text-xs">
              <span className={i.satisfied ? "text-slate-300" : "text-amber-400"}>
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
            const tone = c.passed ? "text-slate-300" : isCritical ? "text-[#e6a39b]" : "text-amber-400";
            return (
              <li key={c.name} className="flex items-center gap-2 text-xs">
                <span className={tone}>{c.passed ? "✓" : isCritical ? "✗" : "⚠"}</span>
                <span className="text-slate-300">{c.name.replace(/_/g, " ")}</span>
                {!c.passed && c.expected && (
                  <span className="text-slate-500">expected {c.expected}, got {c.actual}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </details>
  );
}

function BottomComposer({ onError }: { onError: (m: string) => void }) {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);

  async function generate() {
    if (submitting.current || !prompt.trim()) return;
    submitting.current = true;
    setBusy(true);
    try {
      const design = await api.createDesign(prompt.trim());
      router.push(`/studio/${design.id}`);
    } catch (e) {
      onError(e instanceof ApiError ? e.message : `Generation failed: ${String(e)}`);
      setBusy(false);
      submitting.current = false;
    }
  }

  return (
    <div className="shrink-0 border-t border-edge bg-panel p-3">
      <div className="mb-2 flex flex-wrap gap-1.5">
        {EXAMPLE_CHIPS.map((ex) => (
          <button key={ex.label} type="button" className="chip" disabled={busy}
            onClick={() => setPrompt(ex.prompt)}>
            {ex.label}
          </button>
        ))}
      </div>
      <div className="flex items-end gap-2">
        <textarea
          className="input min-h-[44px] resize-none"
          rows={1}
          placeholder="Describe a new part to generate…"
          value={prompt}
          disabled={busy}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) generate();
          }}
        />
        <button className="btn-primary shrink-0" onClick={generate} disabled={busy || !prompt.trim()}>
          {busy ? "Generating…" : "Generate"}
        </button>
      </div>
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className={`truncate text-right text-slate-300 ${mono ? "stat" : ""}`}>{v}</dd>
    </div>
  );
}
