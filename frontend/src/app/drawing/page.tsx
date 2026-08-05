"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  ApiError,
  type DrawingToCadResult,
  type ProviderStatus,
} from "@/lib/api";
import {
  DrawingJobError,
  stepIndexForStage,
  successfulDesignId,
  type DrawingJobStage,
} from "@/lib/drawingJob";
import { useRequireAuth } from "@/lib/auth";
import type { Design } from "@/lib/types";

const ACCEPT = ".png,.jpg,.jpeg,.webp,.pdf,.svg,.dxf";
const ACCEPT_LABEL = "PNG · JPG · PDF · SVG · DXF";
const VECTOR_TYPES = new Set(["svg", "dxf"]);

const FAMILIES = [
  "adapter_plate", "bracket", "enclosure", "flange", "clamp", "spacer",
  "gear", "jig", "wheel", "generic_extruded_part",
];

// Uploading is client-side (index 0); the rest mirror the backend job stages.
const STEPS = [
  "Uploading",
  "Reading drawing",
  "Extracting dimensions",
  "Building CAD",
  "Validating",
  "Exporting",
] as const;

type Phase = "idle" | "uploading" | "processing" | "opening" | "failed" | "review";

export default function DrawingToCadPage() {
  const router = useRouter();
  const { user, loading } = useRequireAuth();
  const [file, setFile] = useState<File | null>(null);
  const [notes, setNotes] = useState("");
  const [units, setUnits] = useState<"mm" | "inch">("mm");
  const [thickness, setThickness] = useState("");
  const [family, setFamily] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [uploadPct, setUploadPct] = useState(0);
  const [activeStep, setActiveStep] = useState(0);
  const [stageLabel, setStageLabel] = useState<string | null>(null);
  const [failure, setFailure] = useState<{
    message: string;
    questions: string[];
  } | null>(null);
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [reviewDesign, setReviewDesign] = useState<Design | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.providerStatus().then(setStatus).catch(() => {});
  }, []);

  const pickFile = useCallback((f: File | null) => {
    setFile(f);
    setFailure(null);
    setReviewDesign(null);
    setPhase("idle");
    setActiveStep(0);
  }, []);

  async function generate() {
    if (!file) return;
    setPhase("uploading");
    setUploadPct(0);
    setActiveStep(0);
    setFailure(null);
    setStageLabel("Sending your drawing…");
    try {
      const res: DrawingToCadResult = await api.drawingToCad(file, {
        notes: notes.trim() || undefined,
        units,
        thicknessMm: thickness ? Number(thickness) : undefined,
        family: family || undefined,
        onProgress: (pct) => {
          setUploadPct(pct);
          if (pct >= 100) {
            setPhase("processing");
            setActiveStep(1);
            setStageLabel("Reading the drawing…");
          }
        },
        onStage: (stage: DrawingJobStage) => {
          const idx = stepIndexForStage(stage);
          if (idx > 0) setActiveStep(Math.min(idx, STEPS.length - 1));
          setStageLabel(stageText(stage));
        },
      });
      const designId = successfulDesignId(res);
      if (designId && res.design) {
        setActiveStep(STEPS.length);
        if (res.design.drawing_review_required) {
          // Mandatory interpretation review: an uncertain drawing (low
          // fidelity, or a critical dimension the drawing never showed)
          // must be reviewed by a human before opening the studio — never
          // silently auto-opened as if it were a confident, precise read.
          setPhase("review");
          setReviewDesign(res.design);
          return;
        }
        // Confident read: open the generated part directly in the 3D studio.
        setPhase("opening");
        router.push(`/studio/${designId}`);
        return;
      }
      setPhase("failed");
      setFailure({
        message:
          res.message ??
          "Couldn't generate a model from this drawing. Add a note describing " +
            "the part and its key dimensions, then retry.",
        questions: res.analysis?.clarification_questions ?? [],
      });
    } catch (e) {
      setPhase("failed");
      if (e instanceof DrawingJobError) {
        setFailure({ message: e.message, questions: [] });
      } else if (e instanceof ApiError) {
        setFailure({
          message: `${e.message}${e.endpoint ? ` [${e.status || "network"}]` : ""}`,
          questions: [],
        });
      } else {
        setFailure({ message: `Generation failed: ${String(e)}`, questions: [] });
      }
    }
  }

  if (loading || !user) return <div className="py-10 text-slate-400">Loading…</div>;

  const ext = file?.name.includes(".")
    ? file.name.split(".").pop()!.toLowerCase()
    : "";
  const isVector = VECTOR_TYPES.has(ext === "jpg" ? "jpeg" : ext);
  const visionBlocked = !!status && !status.drawing_to_cad_enabled;
  const blockedForThisFile = visionBlocked && !!file && !isVector;
  const busy = phase === "uploading" || phase === "processing" || phase === "opening";

  return (
    <div className="page max-w-3xl space-y-5 lg:max-w-4xl">
      <div className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="label block">Drawing → CAD</span>
          <span
            title="Drawing → CAD is a beta workflow: it covers clean dimensioned
single-part drawings, simple orthographic views, vector DXF/SVG profiles, and
limited raster drawings with readable dimensions. Complex assemblies,
GD&T-heavy drawings, poor scans, missing dimensions, and ambiguous hidden
geometry are not yet supported."
            className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-200"
          >
            Beta
          </span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-50 sm:text-3xl">
          Turn a 2D drawing into a 3D part
        </h1>
        <p className="mt-1 max-w-2xl text-sm leading-relaxed text-slate-400">
          Upload a mechanical drawing and LunaiCAD builds a parametric,
          validated 3D model — dimensions from the drawing are the source of
          truth, anything missing is inferred and listed as an assumption.
          This workflow is in beta: results on complex or unclear drawings
          should be treated as a starting point, not a final, verified part.
        </p>
      </div>

      {/* Progress stepper (job-driven) */}
      <ol className="flex flex-wrap items-center gap-2 text-xs">
        {STEPS.map((s, i) => {
          const state =
            phase === "opening" || phase === "review" || i < activeStep
              ? "done"
              : i === activeStep && phase !== "idle" && phase !== "failed"
                ? "active"
                : "todo";
          return (
            <li key={s} className="flex items-center gap-2">
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full border text-[10px] ${
                  state === "done"
                    ? "border-accent bg-accent/20 text-accent"
                    : state === "active"
                      ? "border-accent text-accent"
                      : "border-edge text-slate-500"
                }`}
              >
                {state === "done" ? "✓" : state === "active" ? (
                  <span className="h-2 w-2 animate-pulse rounded-full bg-accent" />
                ) : (
                  i + 1
                )}
              </span>
              <span className={state === "todo" ? "text-slate-500" : "text-slate-200"}>
                {s}
              </span>
              {i < STEPS.length - 1 && <span className="h-px w-4 bg-edge" />}
            </li>
          );
        })}
      </ol>

      {/* Upload card */}
      <div className="card space-y-4 p-5">
        <div
          className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center transition ${
            dragOver ? "border-accent bg-accent/5" : "border-edge bg-raised/40"
          }`}
          onClick={() => fileInput.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            pickFile(e.dataTransfer.files?.[0] ?? null);
          }}
        >
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <>
              <span className="text-sm font-medium text-slate-100">{file.name}</span>
              <span className="text-xs text-slate-400">
                {(file.size / 1024).toFixed(0)} KB ·{" "}
                {isVector
                  ? "vector drawing — parsed exactly, no AI required"
                  : "will be interpreted by the vision model"}
              </span>
              <button
                className="btn-ghost text-xs"
                onClick={(e) => {
                  e.stopPropagation();
                  pickFile(null);
                }}
              >
                Remove
              </button>
            </>
          ) : (
            <>
              <span className="text-sm text-slate-200">
                Drop a drawing here, or click to browse
              </span>
              <span className="text-xs text-slate-500">{ACCEPT_LABEL} · max 20 MB</span>
            </>
          )}
        </div>

        {blockedForThisFile && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
            The current provider (<code>{status?.provider}</code>) can&apos;t read
            image drawings — dimensions and labels won&apos;t be interpreted. A
            clean single-outline drawing can still be traced deterministically;
            SVG and DXF files are always parsed exactly.
          </div>
        )}

        <label className="block text-xs text-slate-400">
          Notes (optional)
          <input
            className="input mt-1"
            placeholder="Add any notes, missing dimensions, material thickness, or intended use."
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={busy}
          />
        </label>

        <button
          className="text-xs text-slate-400 underline-offset-2 hover:text-slate-200 hover:underline"
          onClick={() => setAdvanced((a) => !a)}
        >
          {advanced ? "Hide options" : "More options (units, thickness, part type)"}
        </button>
        {advanced && (
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block text-xs text-slate-400">
              Drawing units
              <select
                className="input mt-1"
                value={units}
                onChange={(e) => setUnits(e.target.value as "mm" | "inch")}
                disabled={busy}
              >
                <option value="mm">millimetres</option>
                <option value="inch">inches</option>
              </select>
            </label>
            <label className="block text-xs text-slate-400">
              Thickness / depth (mm)
              <input
                className="input mt-1"
                type="number"
                min="0.1"
                step="0.5"
                placeholder="e.g. 6"
                value={thickness}
                onChange={(e) => setThickness(e.target.value)}
                disabled={busy}
              />
            </label>
            <label className="block text-xs text-slate-400">
              Part type
              <select
                className="input mt-1"
                value={family}
                onChange={(e) => setFamily(e.target.value)}
                disabled={busy}
              >
                <option value="">— auto-detect —</option>
                {FAMILIES.map((f) => (
                  <option key={f} value={f}>
                    {f.replace(/_/g, " ")}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}

        <div className="flex items-center gap-3">
          <button
            className="btn-primary"
            disabled={!file || busy}
            onClick={generate}
          >
            {phase === "uploading"
              ? `Uploading… ${uploadPct}%`
              : phase === "processing"
                ? "Generating…"
                : phase === "opening"
                  ? "Opening your part…"
                  : "Generate 3D CAD"}
          </button>
          {busy && stageLabel && (
            <span className="text-xs text-slate-400">{stageLabel}</span>
          )}
        </div>
        {phase === "uploading" && (
          <div className="h-1 w-full overflow-hidden rounded bg-raised">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${uploadPct}%` }}
            />
          </div>
        )}
      </div>

      {/* Mandatory interpretation review — an uncertain drawing (low fidelity,
          or a critical dimension the drawing never showed) never auto-opens
          in the studio; the user must see and acknowledge what was assumed
          or left unresolved first (docs/drawing-to-cad-beta.md). */}
      {phase === "review" && reviewDesign && (
        <div className="card space-y-3 border-amber-500/40 p-4">
          <div className="flex items-center gap-2">
            <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-200">
              Beta · Review needed
            </span>
            <h2 className="text-sm font-semibold text-amber-100">
              Check this interpretation before you trust it
            </h2>
          </div>

          {reviewDesign.drawing_fidelity && (
            <p className="text-xs text-slate-300">
              Fidelity:{" "}
              <span className="font-medium text-amber-200">
                {reviewDesign.drawing_fidelity.drawing_fidelity_status}
              </span>
              {typeof reviewDesign.drawing_fidelity.source_drawing_confidence ===
                "number" && (
                <>
                  {" "}· confidence{" "}
                  {Math.round(
                    reviewDesign.drawing_fidelity.source_drawing_confidence * 100
                  )}
                  %
                </>
              )}
            </p>
          )}

          {!!reviewDesign.drawing_fidelity?.critical_unresolved?.length && (
            <div>
              <p className="text-xs font-medium text-red-200">
                Unresolved critical dimensions — the drawing never showed:
              </p>
              <ul className="list-disc pl-5 text-xs text-red-100/90">
                {reviewDesign.drawing_fidelity.critical_unresolved.map((c) => (
                  <li key={c}>{c.replace(/_/g, " ")}</li>
                ))}
              </ul>
            </div>
          )}

          {reviewDesign.download_blocked_reason && (
            <p className="rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-100">
              Export is blocked: {reviewDesign.download_blocked_reason} Add a
              thickness/depth override or re-upload a clearer drawing, then
              retry.
            </p>
          )}

          {reviewDesign.assumptions.length > 0 && (
            <div>
              <p className="text-xs font-medium text-slate-300">
                Assumptions made while reading this drawing:
              </p>
              <ul className="list-disc pl-5 text-xs text-slate-400">
                {reviewDesign.assumptions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <button
              className="btn-primary"
              onClick={() => router.push(`/studio/${reviewDesign.id}`)}
            >
              I&apos;ve reviewed it — open in studio
            </button>
            <button
              className="btn-ghost text-xs"
              onClick={() => pickFile(null)}
            >
              Upload a different drawing instead
            </button>
          </div>
        </div>
      )}

      {/* Failure card — the generating state always exits into either the
          studio redirect above or this card with a retry. */}
      {phase === "failed" && failure && (
        <div className="card space-y-3 border-red-500/40 p-4">
          <h2 className="text-sm font-semibold text-red-200">
            Couldn&apos;t generate a model from this drawing
          </h2>
          <p className="text-sm text-red-100/90">{failure.message}</p>
          {failure.questions.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-slate-300">
              {failure.questions.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ul>
          )}
          <p className="text-xs text-slate-400">
            Try adding a note describing the part and its key dimensions, or
            upload a clearer drawing (SVG/DXF give the most accurate results).
          </p>
          <button className="btn-primary" onClick={generate} disabled={!file || busy}>
            Retry
          </button>
        </div>
      )}
    </div>
  );
}

function stageText(stage: DrawingJobStage): string {
  switch (stage) {
    case "queued":
      return "Queued…";
    case "reading_drawing":
      return "Reading the drawing…";
    case "interpreting":
      return "Interpreting the drawing…";
    case "extracting_dimensions":
      return "Extracting dimensions and features…";
    case "fallback_generating":
      return "Building best-effort CAD from the drawing geometry…";
    case "building_cad":
      return "Building parametric CAD…";
    case "validating":
      return "Validating geometry against the drawing…";
    case "exporting":
      return "Exporting STEP/STL…";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
  }
}
