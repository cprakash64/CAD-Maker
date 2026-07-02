"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import CircleEditPanel from "@/components/CircleEditPanel";
import FeedbackWidget from "@/components/FeedbackWidget";
import HoleTable from "@/components/HoleTable";
import ModifyBox from "@/components/ModifyBox";
import ParameterSidebar from "@/components/ParameterSidebar";
import type { SelectedFeature } from "@/components/Studio3D";
import { usePartPrompt } from "@/components/PartPromptOverlay";
import ExportMenu from "@/components/ExportMenu";
import { api, ApiError, getToken } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import type {
  Design,
  DimensionMeasured,
  DesignSummary,
  Hole,
  ObjectIntelligence,
  PartFamilyContract,
  PartFamilyDetail,
  StandardPart,
} from "@/lib/types";

const Studio3D = dynamic(() => import("@/components/Studio3D"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[420px] items-center justify-center rounded-lg border border-edge bg-viewport text-slate-500">
      Loading viewer…
    </div>
  ),
});

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
  const partPrompt = usePartPrompt();
  const { user, loading: authLoading } = useRequireAuth();
  const [design, setDesign] = useState<Design | null>(null);
  const [designs, setDesigns] = useState<DesignSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedFeature, setSelectedFeature] = useState<SelectedFeature | null>(null);
  const [materialColor, setMaterialColor] = useState<string | undefined>(undefined);
  const [materialName, setMaterialName] = useState<string | null>(null);
  const [railOpen, setRailOpen] = useState(true);

  // Restore the parts-rail collapse state for the session (after mount to keep
  // SSR/CSR markup identical and avoid hydration mismatch).
  useEffect(() => {
    if (sessionStorage.getItem("cm.railOpen") === "0") setRailOpen(false);
  }, []);
  function toggleRail() {
    setRailOpen((o) => {
      const next = !o;
      sessionStorage.setItem("cm.railOpen", next ? "1" : "0");
      return next;
    });
  }

  useEffect(() => {
    if (!user) return;
    setMaterialColor(undefined);
    setMaterialName(null);
    api.getDesign(id).then(setDesign).catch((e) =>
      setError(e instanceof ApiError ? e.message : String(e))
    );
  }, [id, user]);

  // Sidebar rail list: fetch once per part (on mount / when navigating to a
  // different design id), NOT on every `design` state mutation — depending on the
  // whole `design` object refired GET /api/designs on each regenerate/modify
  // (the duplicate-request storm). The rail only needs the list, not live edits.
  useEffect(() => {
    if (!user) return;
    api.listDesigns().then(setDesigns).catch(() => {});
  }, [user, id]);

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

  // Expectation-control status badge (backend single source of truth): a concept
  // assembly never shows a plain manufacturing PASS.
  const pres = design.presentation;
  const toneClass: Record<string, string> = {
    pass: "badge-pass",
    review: "badge-review",
    fail: "badge-fail",
  };
  const vmeta = pres?.status_badge
    ? {
        cls: toneClass[pres.status_tone ?? "review"] ?? "badge-neutral",
        text: pres.status_badge,
        detail: pres.status_detail,
      }
    : null;

  const partName =
    design.title ??
    (design.object_type
      ? design.object_type.replace(/_/g, " ")
      : design.prompt.slice(0, 40));

  return (
    <div className="flex flex-col lg:h-[calc(100dvh-52px)]">
      {/* --- Persistent beta disclaimer ----------------------------------- */}
      <div className="shrink-0 border-b border-edge bg-amber-500/5 px-4 py-1 text-center text-[11px] text-amber-200/80">
        {pres?.beta_notice ??
          "Beta CAD output. Always verify dimensions and engineering requirements before manufacturing."}
      </div>

      {/* --- 3-column workspace (collapsible parts rail) ------------------ */}
      <div
        className={`grid min-h-0 flex-1 gap-px bg-edge transition-[grid-template-columns] duration-200 ease-premium ${
          railOpen
            ? "lg:grid-cols-[210px_minmax(0,1fr)_360px] xl:grid-cols-[230px_minmax(0,1fr)_380px]"
            : "lg:grid-cols-[44px_minmax(0,1fr)_360px] xl:grid-cols-[44px_minmax(0,1fr)_380px]"
        }`}
      >
        {/* Left: parts rail — hidden on small (top nav already covers Workspace) */}
        <aside className="hidden flex-col overflow-hidden bg-panel lg:flex lg:overflow-y-auto">
          {railOpen ? (
            <div className="flex-1 space-y-1 px-2 py-3">
              <div className="flex items-center justify-between gap-1 px-1 pb-1.5">
                <span className="label">Parts</span>
                <div className="flex items-center gap-1.5">
                  {designs.length > 0 && (
                    <span className="text-[11px] text-slate-600">{designs.length}</span>
                  )}
                  <button
                    onClick={toggleRail}
                    title="Hide parts"
                    aria-label="Hide parts"
                    className="grid h-5 w-5 place-items-center rounded text-slate-500 transition-colors hover:bg-raised hover:text-slate-200"
                  >
                    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden>
                      <path d="M9.5 3.5 5 8l4.5 4.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                </div>
              </div>
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
                      {d.title ?? (d.object_type ? d.object_type.replace(/_/g, " ") : d.prompt)}
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
          ) : (
            /* Collapsed: a slim, intentional reveal handle (no dead strip). */
            <button
              onClick={toggleRail}
              title="Show parts"
              aria-label="Show parts"
              className="flex h-full w-full flex-col items-center gap-3 py-3 text-slate-500 transition-colors hover:bg-raised/60 hover:text-slate-200"
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M6.5 3.5 11 8l-4.5 4.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              <span className="label [writing-mode:vertical-rl]">Parts</span>
            </button>
          )}
        </aside>

        {/* Center: part identity strip + full-height CAD viewer */}
        <section className="flex min-h-0 flex-col bg-ink">
          {(notice || error) && (
            <div className="shrink-0 space-y-2 px-4 pt-3">
              {notice && <div className="banner-info">{notice}</div>}
              {error && <div className="banner-danger">{error}</div>}
            </div>
          )}

          {hasModel ? (
            <>
              {/* Compact identity strip (replaces the old breadcrumb row). */}
              <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                <div className="flex min-w-0 items-center gap-2.5">
                  <span className="truncate text-sm font-semibold text-slate-100">{partName}</span>
                  {vmeta && (
                    <span className={vmeta.cls} title={vmeta.detail ?? undefined}>
                      {vmeta.text}
                    </span>
                  )}
                  {design.bounding_box_mm && (
                    <span className="stat hidden whitespace-nowrap text-xs text-slate-500 sm:inline">
                      {design.bounding_box_mm.x} × {design.bounding_box_mm.y} ×{" "}
                      {design.bounding_box_mm.z} mm
                    </span>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button className="btn-ghost btn-sm" onClick={share}>Share</button>
                  <ExportMenu
                    formats={design.exports.map((e) => e.fmt)}
                    blocked={exportBlocked}
                    concept={pres?.is_concept}
                    hasPackage={design.exports.length > 0}
                    onDownload={(fmt) =>
                      downloadExport(id, fmt).catch(() => setError("Download failed"))
                    }
                    onPackage={downloadPackage}
                  />
                  <button className="btn-primary btn-sm" onClick={() => partPrompt.open()}>
                    New part
                  </button>
                </div>
              </div>

              {/* Viewer fills the remaining space (taller on big screens).
                  Explicit height on small screens, flex-fill on large. */}
              <div className="relative h-[56dvh] px-3 pb-3 lg:h-auto lg:min-h-0 lg:flex-1 lg:px-4 lg:pb-4">
                <Studio3D
                  mesh={design.preview}
                  features={design.features ?? []}
                  onSelect={setSelectedFeature}
                  materialColor={materialColor}
                />
              </div>
            </>
          ) : (
            <div className="flex-1 space-y-4 overflow-y-auto p-4">
              {design.needs_decomposition && <DecompositionCard design={design} />}
              {design.needs_clarification && (
                <ClarificationCard
                  design={design}
                  busy={busy}
                  onGenerateDefaults={generateWithDefaults}
                />
              )}
            </div>
          )}
        </section>

        {/* Right: inspector — Measured · Parameters · Material · Export */}
        <aside className="space-y-5 bg-panel p-4 lg:overflow-y-auto">
          {hasModel ? (
            <>
              {isAssembly && <AssemblyCaveat design={design} />}
              {design.object_intelligence && (
                <ObjectIntelligenceCard oi={design.object_intelligence} />
              )}
              {design.part_family_contract && (
                <AssumptionsMatchCard contract={design.part_family_contract} design={design} />
              )}
              {design.standard_part && <StandardPartBadge part={design.standard_part} />}
              {design.part_family_detail && (
                <PartFamilyDetailCard detail={design.part_family_detail} />
              )}
              {vmeta?.detail && pres?.is_concept && (
                <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-200/90">
                  {vmeta.detail}
                </p>
              )}

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
                {/* A parameter-driven bore (gear / pulley / standoff) is shown as
                    a read-only feature; the manual hole editor is hidden so it
                    can never contradict the measured count with "No holes yet". */}
                {(pres?.parametric_holes?.length ?? 0) > 0 ? (
                  <div className="card p-3">
                    <h3 className="label mb-1.5">Holes / features</h3>
                    {pres!.parametric_holes.map((h, i) => (
                      <p key={i} className="text-xs text-slate-300">
                        {h.label}: Ø{h.diameter_mm}mm {h.through ? "through" : "blind"}
                      </p>
                    ))}
                    <p className="mt-1 text-[11px] text-slate-500">
                      Edit the bore in Parameters above.
                    </p>
                  </div>
                ) : (
                  design.spec &&
                  pres?.manual_hole_editing !== false && (
                    <HoleTable holes={design.spec?.holes ?? []} onApply={applyHoles} busy={busy} />
                  )
                )}
                {!design.spec && Object.keys(design.editable_parameters).length === 0 && (
                  <p className="text-xs text-slate-500">
                    This part regenerates from its description. Use “New part” to refine it.
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

              <Section title="Export readiness">
                <ExportPanel
                  design={design}
                  blocked={exportBlocked}
                  onDownload={(fmt) =>
                    downloadExport(id, fmt).catch(() => setError("Download failed"))
                  }
                  onPackage={downloadPackage}
                />
              </Section>

              {/* Secondary detail — collapsed so it never crowds the viewer. */}
              {design.explanation && (
                <details className="card p-4">
                  <summary className="label cursor-pointer select-none">Details</summary>
                  <p className="mt-3 text-sm leading-relaxed text-slate-300">
                    {design.explanation}
                  </p>
                </details>
              )}
              {(design.feature_audit.length > 0 || design.semantic_checks.length > 0) && (
                <VerificationDetail design={design} />
              )}
              <FeedbackWidget designId={id} existing={design.my_feedback} />
            </>
          ) : (
            <p className="text-sm text-slate-500">
              No model yet — resolve the prompt to inspect a part.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}

/* ----------------------------- subcomponents ----------------------------- */

function Section({
  title,
  badge,
  children,
}: {
  title: string;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="label">{title}</h2>
        {badge}
      </div>
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
  const throughCount = measured?.through_hole_count;
  const threadedCount = measured?.threaded_hole_count;
  const holeLabel = design.design_mode === "assembly" ? "Components" : "Holes";
  const isAssembly = design.design_mode === "assembly";
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
        {!isAssembly && typeof throughCount === "number" && (
          <Row k="Through holes" v={String(throughCount)} mono />
        )}
        {!isAssembly && typeof threadedCount === "number" && threadedCount > 0 && (
          <Row k="Threaded holes" v={String(threadedCount)} mono />
        )}
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
  const pres = design.presentation;
  const labels = pres?.export_labels;
  const fmtLabel = (fmt: string) =>
    labels?.[fmt as "stl" | "step"] ?? `Export ${fmt.toUpperCase()}`;
  return (
    <div className="card space-y-2 p-4">
      {pres?.export_kind === "concept" && (
        <span className="badge-review">Concept export</span>
      )}
      <div className="grid grid-cols-2 gap-2">
        {design.exports.map((e) => (
          <button key={e.fmt} className="btn-ghost btn-sm" onClick={() => onDownload(e.fmt)}>
            ↓ {fmtLabel(e.fmt)}
          </button>
        ))}
      </div>
      <button className="btn-primary w-full" onClick={onPackage} disabled={design.exports.length === 0}>
        ↓ {labels?.package ?? "CAD Package"}
      </button>
      {design.exports.length === 0 && (
        <p className="text-[11px] text-slate-500">No exports available for this design.</p>
      )}
      {pres?.export_notice && (
        <p className="text-[11px] leading-relaxed text-amber-200/90">{pres.export_notice}</p>
      )}
      {pres?.beta_notice && (
        <p className="text-[11px] leading-relaxed text-slate-500">{pres.beta_notice}</p>
      )}
    </div>
  );
}

function humanFamily(f: string | null): string {
  return f ? f.replace(/_/g, " ") : "—";
}

function AssumptionsMatchCard({
  contract,
  design,
}: {
  contract: PartFamilyContract;
  design: Design;
}) {
  const status = contract.generation_honesty_status;
  // A perfectly exact build with nothing assumed needs no card.
  const noteworthy =
    status !== "exact" ||
    contract.missing_inputs.length > 0 ||
    contract.unsupported_features.length > 0 ||
    contract.substituted_features.length > 0;
  if (!noteworthy) return null;

  const badge =
    status === "exact"
      ? { cls: "badge-pass", text: "Exact match" }
      : status === "partial"
        ? { cls: "badge-review", text: "Review · assumptions made" }
        : status === "substituted"
          ? { cls: "badge-review", text: "Review · substituted" }
          : { cls: "badge-fail", text: "Unsupported" };

  const req = [humanFamily(contract.requested_family), contract.requested_variant]
    .filter(Boolean)
    .join(" · ");
  const gen =
    status === "unsupported"
      ? "not generated"
      : [humanFamily(contract.resolved_family), contract.resolved_variant]
          .filter(Boolean)
          .join(" · ");

  return (
    <div className="card space-y-2 p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="label">Assumptions &amp; Match</h3>
        <span className={badge.cls}>{badge.text}</span>
      </div>
      {contract.reason && (
        <p className="text-xs leading-relaxed text-amber-200/90">{contract.reason}</p>
      )}
      <dl className="space-y-1 text-[11px]">
        <Row k="Requested" v={req || "—"} />
        <Row k="Generated" v={gen || "—"} />
      </dl>
      {contract.missing_inputs.length > 0 && (
        <div>
          <p className="label mb-1">Assumed</p>
          <ul className="list-disc pl-4 text-[11px] text-slate-400">
            {contract.missing_inputs.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      {contract.unsupported_features.length > 0 && (
        <div>
          <p className="label mb-1">Not modeled</p>
          <ul className="list-disc pl-4 text-[11px] text-amber-200/90">
            {contract.unsupported_features.map((u, i) => (
              <li key={i}>{u}</li>
            ))}
          </ul>
        </div>
      )}
      {design.clarification_options && design.clarification_options.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-1">
          {design.clarification_options.map((o, i) => (
            <button
              key={i}
              className="chip"
              onClick={() => sendPromptFallback(o.prompt)}
              title={o.prompt}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Submit a fallback prompt as a new generation (used by unsupported-variant cards).
function sendPromptFallback(prompt: string) {
  if (typeof window !== "undefined") {
    window.location.href = `/?prompt=${encodeURIComponent(prompt)}`;
  }
}

function threadModeLabel(rep?: string): { text: string; cls: string } {
  if (rep === "modeled") return { text: "Modeled", cls: "badge-pass" };
  if (rep === "failed_to_model_fallback_cosmetic")
    return { text: "Failed → cosmetic", cls: "badge-review" };
  return { text: "Cosmetic", cls: "badge-review" };
}

const OI_SOURCE_LABEL: Record<string, string> = {
  local_verified: "Local verified preset",
  official_source_extracted: "Official datasheet / drawing",
  web_source_extracted: "Web source (credible)",
  user_provided: "User-provided dimensions",
  gpt_estimated: "GPT estimate",
  unknown: "Unknown — needs dimensions",
};

function ObjectIntelligenceCard({ oi }: { oi: ObjectIntelligence }) {
  const passy = oi.status === "pass";
  const badgeCls = passy ? "badge-pass" : "badge-review";
  const dims = oi.dimensions_used ?? {};
  const dimKeys = Object.keys(dims).slice(0, 6);
  return (
    <div className="card space-y-2 p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="label">Source &amp; confidence</h3>
        <span className={badgeCls}>{(oi.match_status ?? oi.status ?? "review").toUpperCase()}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <Row k="Object detected" v={oi.object_detected} />
        <Row k="Source type" v={OI_SOURCE_LABEL[oi.source_type] ?? oi.source_type} />
        {oi.confidence_score != null && (
          <Row k="Confidence" v={`${Math.round((oi.confidence_score ?? 0) * 100)}%`} mono />
        )}
        {oi.generated_family && (
          <Row k="Generated family" v={oi.generated_family.replace(/_/g, " ")} />
        )}
        {oi.category && <Row k="Category" v={oi.category.replace(/_/g, " ")} />}
      </dl>
      {dimKeys.length > 0 && (
        <div className="text-[11px] text-slate-400">
          <span className="font-medium">Dimensions used: </span>
          {dimKeys.map((k) => `${k.replace(/_/g, " ")} ${dims[k]}`).join(", ")}
        </div>
      )}
      {(oi.standards?.length ?? 0) > 0 && (
        <div className="text-[11px] text-slate-400">
          <span className="font-medium">Standards / reference: </span>
          {(oi.standards ?? []).join("; ")}
        </div>
      )}
      {(oi.source_urls?.length ?? 0) > 0 && (
        <div className="text-[11px] text-slate-400">
          <span className="font-medium">Reference: </span>
          {(oi.source_urls ?? []).join("; ")}
        </div>
      )}
      {(oi.assumptions?.length ?? 0) > 0 && (
        <ul className="list-disc pl-4 text-[11px] text-slate-400">
          {(oi.assumptions ?? []).map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      )}
      {oi.feature_contract && (oi.feature_contract.requested_features.length > 0) && (
        <div className="text-[11px] text-slate-400">
          <span className="font-medium">Required features: </span>
          {oi.feature_contract.requested_features.map((f) => {
            const ok = oi.feature_contract!.generated_features.includes(f);
            return (
              <span key={f} className={ok ? "text-emerald-300" : "text-amber-300"}>
                {f.replace(/_/g, " ")}
                {ok ? " ✓" : " ✗"}{"  "}
              </span>
            );
          })}
        </div>
      )}
      {oi.why && (
        <p className={`${passy ? "banner-success" : "banner-warn"} text-[11px] leading-relaxed`}>
          {passy ? "PASS: " : "REVIEW: "}
          {oi.why}
        </p>
      )}
    </div>
  );
}

const TREAD_STYLE_LABELS: Record<string, string> = {
  slick: "Slick",
  street: "Street",
  all_terrain: "All-terrain",
  off_road: "Off-road",
};

// Readable tread label (never the raw internal code), with an "(assumed)" hint when
// the style fell back to the street default.
function treadStyleText(detail: PartFamilyDetail): string {
  const label =
    detail.tread_style_label ??
    (detail.tread_style ? TREAD_STYLE_LABELS[detail.tread_style] ?? detail.tread_style : null);
  if (!label) return "—";
  return detail.tread_style_source === "assumed" ? `${label} (assumed)` : label;
}

function PartFamilyDetailCard({ detail }: { detail: PartFamilyDetail }) {
  const fam = detail.family;
  if (fam === "bolt" || fam === "threaded_rod") {
    const mode = threadModeLabel(detail.thread_representation);
    return (
      <div className="card space-y-2 p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="label">Thread</h3>
          <span className={mode.cls}>Thread: {mode.text}</span>
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row k="Thread" v={detail.thread_label ?? detail.thread ?? "—"} mono />
          {detail.threaded_length_mm != null && (
            <Row k="Threaded length" v={`${detail.threaded_length_mm} mm`} mono />
          )}
          {detail.length_mm != null && <Row k="Length" v={`${detail.length_mm} mm`} mono />}
          <Row
            k="External thread modeled"
            v={detail.external_thread_modeled ? "Yes" : "No"}
          />
          {fam === "bolt" && detail.head_across_flats_mm != null && (
            <Row k="Hex head A/F" v={`${detail.head_across_flats_mm} mm`} mono />
          )}
        </dl>
        {detail.fit_warning && (
          <p className="banner-warn text-[11px] leading-relaxed">{detail.fit_warning}</p>
        )}
      </div>
    );
  }
  if (fam === "shaft_coupler") {
    return (
      <div className="card space-y-2 p-4">
        <h3 className="label">Shaft coupler</h3>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row k="Axial bores" v="1 stepped bore" />
          <Row k="Bore A" v={`Ø${detail.bore_1_mm} mm`} mono />
          <Row k="Bore B" v={`Ø${detail.bore_2_mm} mm`} mono />
          <Row k="Set-screw thread" v={detail.set_screw_thread ?? "—"} mono />
          <Row k="Radial set-screw holes" v={String(detail.radial_set_screw_holes ?? detail.set_screw_count ?? 0)} mono />
          <Row k="Threaded holes" v={String(detail.threaded_holes ?? 0)} mono />
          {detail.set_screw_tap_drill_mm != null && (
            <Row k="Tap-drill" v={`Ø${detail.set_screw_tap_drill_mm} mm`} mono />
          )}
          <Row k="Set-screw mode" v={detail.set_screw_thread_mode ?? "—"} />
        </dl>
        {detail.set_screw_thread_mode === "cosmetic" && (
          <p className="banner-warn text-[11px] leading-relaxed">
            Cosmetic set-screw threads — hole geometry present (tap-drill core +
            thread-relief rings), thread fit not validated.
          </p>
        )}
      </div>
    );
  }
  if (fam === "tire") {
    return (
      <div className="card space-y-2 p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="label">Tire</h3>
          <span className="badge-review">Rim: No</span>
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row k="Outer Ø" v={`${detail.outer_diameter_mm} mm (${detail.od_source})`} mono />
          <Row k="Inner Ø" v={`${detail.inner_diameter_mm} mm (${detail.id_source})`} mono />
          <Row k="Width" v={`${detail.width_mm} mm (${detail.width_source})`} mono />
          <Row k="Tread style" v={treadStyleText(detail)} />
          <Row k="Hollow body" v={detail.hollow ? "Yes" : "No"} />
          <Row k="Rim included" v={detail.rim_included ? "Yes" : "No"} />
          <Row k="Material" v={detail.material_hint ?? "rubber"} />
        </dl>
        <p className="banner-warn text-[11px] leading-relaxed">
          Rubber tire only — hollow body, no rim/hub/spokes. Assumed dimensions are
          marked; provide inner diameter and width for a validated (PASS) fit.
        </p>
      </div>
    );
  }
  if (fam === "rim") {
    return (
      <div className="card space-y-2 p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="label">Wheel rim</h3>
          <span className="badge-review">Tire: No</span>
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row k="Outer Ø" v={`${detail.outer_diameter_mm} mm`} mono />
          <Row k="Width" v={`${detail.width_mm} mm`} mono />
          <Row k="Centre bore" v={`Ø${detail.center_bore_mm} mm`} mono />
          <Row k="Spokes" v={detail.spoke_style ?? "5-spoke"} />
          <Row k="Hex hub" v={detail.hex_hub ? "Yes" : "No"} />
          <Row k="Lug holes" v={String(detail.lug_count ?? 0)} mono />
          <Row k="Tire included" v={detail.tire_included ? "Yes" : "No"} />
        </dl>
      </div>
    );
  }
  if (fam === "wheel_assembly") {
    return (
      <div className="card space-y-2 p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="label">Wheel assembly</h3>
          <span className="badge-review">Tire + rim</span>
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row k="Tire OD" v={`${detail.tire_outer_diameter_mm} mm`} mono />
          <Row k="Tire ID" v={`${detail.tire_inner_diameter_mm} mm`} mono />
          <Row k="Width" v={`${detail.width_mm} mm`} mono />
          <Row k="Rim OD" v={`${detail.rim_diameter_mm} mm`} mono />
          <Row k="Centre bore" v={`Ø${detail.center_bore_mm} mm`} mono />
          <Row k="Spokes" v={detail.spoke_style ?? "5-spoke"} />
          <Row k="Tread style" v={treadStyleText(detail)} />
        </dl>
      </div>
    );
  }
  if (fam === "device_enclosure") {
    const ports = detail.port_cutouts ?? [];
    const pretty = (s: string) => s.replace(/_/g, " ").toUpperCase();
    return (
      <div className="card space-y-2 p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="label">Device preset</h3>
          <span className="badge-review">{detail.match_status ?? "approximate"}</span>
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row k="Device" v={detail.device_name ?? detail.device ?? "—"} />
          <Row k="Board preset" v={detail.board_preset_source ?? detail.device ?? "—"} />
          <Row k="Mounting posts" v={String(detail.mounting_posts ?? 0)} mono />
          <Row k="micro-HDMI cutouts" v={String(detail.micro_hdmi_count ?? 0)} mono />
          {detail.ports_through_hole_verified != null && (
            <Row
              k="Ports through-hole"
              v={detail.ports_through_hole_verified ? "Verified open" : "BLOCKED"}
            />
          )}
          <Row k="Lid type" v={detail.lid_type ?? "removable"} />
          {detail.wall_thickness_mm != null && (
            <Row k="Wall thickness" v={`${detail.wall_thickness_mm} mm`} mono />
          )}
          <Row k="Logo feature" v={(detail.logo_feature_status ?? "—").replace(/_/g, " ")} />
        </dl>
        {ports.length > 0 && (
          <div className="text-[11px] text-muted">
            <span className="font-medium">Port cutouts: </span>
            {ports.map(pretty).join(", ")}
          </div>
        )}
        {(detail.blocked_ports?.length ?? 0) > 0 ? (
          <p className="banner-warn text-[11px] leading-relaxed">
            FAILED: these port cutouts are not full through-holes —{" "}
            {(detail.blocked_ports ?? []).map(pretty).join(", ")}.
          </p>
        ) : (
          <p className="banner-warn text-[11px] leading-relaxed">
            Connector positions/clearances are from the official mechanical drawing to a
            tolerance — review the fit against the real board. All port cutouts are
            verified through-holes to the cavity.
          </p>
        )}
      </div>
    );
  }
  if (fam === "timing_pulley_gt2") {
    return (
      <div className="card space-y-2 p-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="label">GT2 timing pulley</h3>
          <span className="badge-pass">Not a spur gear</span>
        </div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Row k="Teeth" v={String(detail.teeth ?? "—")} mono />
          <Row k="Belt pitch" v={`${detail.pitch_mm ?? 2} mm`} mono />
          {detail.pitch_diameter_mm != null && (
            <Row k="Pitch Ø" v={`${detail.pitch_diameter_mm} mm`} mono />
          )}
          <Row k="Belt width" v={`${detail.belt_width_mm} mm`} mono />
          <Row k="Bore" v={`Ø${detail.bore_mm} mm`} mono />
          <Row k="Flanges" v={detail.has_flanges ? "Yes" : "No"} />
        </dl>
      </div>
    );
  }
  return null;
}

function StandardPartBadge({ part }: { part: StandardPart }) {
  const modeled = part.internal_thread_modeled === true;
  return (
    <div className="card space-y-2 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="badge-accent">{part.badge}</span>
        {modeled ? (
          <span className="badge-pass" title="Geometry audit">Modeled internal thread</span>
        ) : (
          <span className="badge-review" title="Geometry audit">Cosmetic thread only</span>
        )}
        {part.hex_six_sided === false && (
          <span className="badge-review" title="Geometry audit">Shape check failed</span>
        )}
      </div>
      <p className="text-xs leading-relaxed text-slate-400">{part.assumed_message}</p>
      {!modeled && (
        <p className="banner-warn text-[11px] leading-relaxed">
          Cosmetic thread only — not suitable for thread-fit validation or 3D-printed
          functional threads.
        </p>
      )}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-slate-400">
        {part.across_flats_mm != null && (
          <Row k="Across flats" v={`${part.across_flats_mm} mm`} mono />
        )}
        {part.across_corners_mm != null && (
          <Row k="Across corners" v={`${part.across_corners_mm} mm`} mono />
        )}
        {part.height_mm != null && <Row k="Height" v={`${part.height_mm} mm`} mono />}
        {part.minor_diameter_mm != null && (
          <Row k="Minor Ø" v={`Ø${part.minor_diameter_mm} mm`} mono />
        )}
        {part.thread_depth_mm != null && (
          <Row k="Thread depth" v={`${part.thread_depth_mm} mm`} mono />
        )}
        <Row k="Thread" v={modeled ? "modeled (helical)" : "cosmetic"} />
      </dl>
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
      {design.clarification_options.length > 0 && (
        <div className="mt-3">
          <p className="label mb-1.5 text-amber-200/90">
            Pick a ready-to-generate part:
          </p>
          <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {design.clarification_options.map((opt) => (
              <Link
                key={opt.label}
                href={`/new?prompt=${encodeURIComponent(opt.prompt)}`}
                title={opt.prompt}
                className="card flex flex-col gap-0.5 p-2.5 text-left transition hover:border-amber-400/60"
              >
                <span className="text-sm font-medium text-slate-100">{opt.label}</span>
                <span className="line-clamp-2 text-xs text-slate-400">{opt.prompt}</span>
              </Link>
            ))}
          </div>
        </div>
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

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className={`truncate text-right text-slate-300 ${mono ? "stat" : ""}`}>{v}</dd>
    </div>
  );
}
