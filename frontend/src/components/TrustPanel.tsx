import { useState } from "react";
import { api } from "@/lib/api";
import type { Design } from "@/lib/types";
import { FEEDBACK_CATEGORIES } from "@/lib/types";

// Labels describe LunaiCAD's TEMPLATE maturity for this part family, never a
// claim about this specific design being ready to manufacture -- "Production
// ready" is on the banned-claim list (app/safety/language.py) precisely
// because it reads as the latter. See docs/legal/safety-and-engineering-
// disclaimer.md.
const CAPABILITY_LABEL: Record<string, { text: string; cls: string }> = {
  production_ready: { text: "High-confidence template", cls: "badge-pass" },
  validated_beta: { text: "Validated beta", cls: "badge-review" },
  experimental: { text: "Experimental", cls: "badge-review" },
  unsupported: { text: "Unsupported", cls: "badge-fail" },
};

// Mirrors app.safety.categories.CATEGORY_LABELS -- kept in sync manually
// (small, stable list); a mismatch here only affects display text, never
// enforcement (the backend is the sole source of truth for policy/blocking).
const SAFETY_CATEGORY_LABEL: Record<string, string> = {
  structural_load_bearing: "Structural / load-bearing component",
  lifting_equipment: "Lifting equipment",
  vehicle_steering_or_braking: "Vehicle steering or braking component",
  pressure_containing: "Pressure-containing part",
  medical_devices: "Medical device",
  child_safety_products: "Child-safety product",
  fire_safety_equipment: "Fire-safety equipment",
  mains_electrical_safety: "Mains-electrical-safety component",
  aerospace_flight_components: "Aerospace flight component",
  weapon_components: "Weapon component",
  other_high_consequence: "Other high-consequence application",
};

function categoryLabel(category: string): string {
  return SAFETY_CATEGORY_LABEL[category] ?? category;
}

const VALIDATION_LABEL: Record<string, { text: string; cls: string }> = {
  pass: { text: "Validation: pass", cls: "badge-pass" },
  warning: { text: "Validation: review", cls: "badge-review" },
  critical_failure: { text: "Validation: failed", cls: "badge-fail" },
};

/**
 * The single, consolidated "what LunaiCAD understood" surface: interpreted
 * intent, normalized units, capability maturity, assumptions, unresolved
 * questions, validation findings, printer-profile provenance, export
 * eligibility, and known limitations. Every value here comes straight off
 * the DesignDTO (design_service.product_contract_fields) -- this component
 * renders, it does not compute or infer. The one exception is the safety
 * acknowledgment button below, which records a user ACTION (explicit
 * consent), not a new inference.
 */
export default function TrustPanel({
  design,
  onAcknowledged,
}: {
  design: Design;
  onAcknowledged?: (design: Design) => void;
}) {
  const cap = design.capability_level ? CAPABILITY_LABEL[design.capability_level] : null;
  const val = design.validation_status ? VALIDATION_LABEL[design.validation_status] : null;
  const assumptions = design.assumptions ?? [];
  const questions = design.unanswered_questions ?? [];
  const limitations = design.limitations ?? [];
  const criticals = design.validation_critical_failures ?? [];
  const warnings = design.validation_warnings ?? [];
  const elig = design.export_eligibility;
  const safety = design.safety;
  const [acking, setAcking] = useState(false);
  const [ackError, setAckError] = useState<string | null>(null);

  const [reportOpen, setReportOpen] = useState(false);
  const [reportCategories, setReportCategories] = useState<string[]>([]);
  const [reportReason, setReportReason] = useState("");
  const [reportConsent, setReportConsent] = useState(false);
  const [printSuccess, setPrintSuccess] = useState<boolean | null>(null);
  const [fitSuccess, setFitSuccess] = useState<boolean | null>(null);
  const [reportSubmitting, setReportSubmitting] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportDone, setReportDone] = useState(false);

  const toggleCategory = (value: string) => {
    setReportCategories((cur) =>
      cur.includes(value) ? cur.filter((c) => c !== value) : [...cur, value]
    );
  };

  const submitReport = async () => {
    setReportSubmitting(true);
    setReportError(null);
    try {
      await api.reportBadResult(design.id, {
        categories: reportCategories,
        reason: reportReason,
        consent: reportConsent,
        print_success: printSuccess,
        fit_success: fitSuccess,
      });
      setReportDone(true);
    } catch (e) {
      setReportError(e instanceof Error ? e.message : "Could not submit the report.");
    } finally {
      setReportSubmitting(false);
    }
  };

  const acknowledge = async () => {
    setAcking(true);
    setAckError(null);
    try {
      const updated = await api.acknowledgeSafety(design.id);
      onAcknowledged?.(updated);
    } catch (e) {
      setAckError(e instanceof Error ? e.message : "Could not record acknowledgment.");
    } finally {
      setAcking(false);
    }
  };

  return (
    <section
      aria-label="What LunaiCAD understood"
      className="card space-y-3 border-edge/80 p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="label">Understanding</h2>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {cap && <span className={cap.cls}>{cap.text}</span>}
          {val && <span className={val.cls}>{val.text}</span>}
          {design.drawing_beta && (
            <span
              className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-200"
              title="Built from an uploaded drawing — a beta workflow"
            >
              Beta
            </span>
          )}
        </div>
      </div>

      {design.interpreted_intent && (
        <p className="text-sm leading-relaxed text-slate-200">
          <span className="text-slate-500">Understood as: </span>
          {design.interpreted_intent}
        </p>
      )}

      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
        <Row k="Units" v={(design.normalized_units ?? "mm").toUpperCase()} />
        {typeof design.confidence === "number" && (
          <Row k="Confidence" v={`${Math.round(design.confidence * 100)}%`} />
        )}
      </dl>

      {elig && (
        <div
          className={`rounded-md border px-2.5 py-2 text-[11px] leading-relaxed ${
            elig.eligible
              ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-200"
              : "border-amber-500/30 bg-amber-500/5 text-amber-200"
          }`}
        >
          <span className="font-semibold">
            {elig.eligible ? "Manufacturable export available." : "Manufacturable export blocked."}
          </span>
          {elig.reason && <span> {elig.reason}</span>}
          {elig.formats && (
            <span className="mt-1 block text-slate-400">
              STL {elig.formats.stl ? "✓" : "✗"} · STEP {elig.formats.step ? "✓" : "✗"} · GLB preview{" "}
              {elig.formats.glb ? "✓" : "✗"}
            </span>
          )}
        </div>
      )}

      {safety && safety.categories.length > 0 && (
        <div
          className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-[11px] leading-relaxed text-amber-200"
          role="alert"
        >
          <span className="font-semibold uppercase tracking-wide">
            Safety review: {safety.categories.map(categoryLabel).join(", ")}
          </span>
          {safety.message && <p className="mt-1">{safety.message}</p>}
          {safety.engineering_review_required && (
            <p className="mt-1 font-medium">
              Engineering review is required before manufacture.
            </p>
          )}
          {safety.policy === "require_acknowledgment" && !safety.acknowledged && (
            <div className="mt-2">
              <button
                type="button"
                onClick={acknowledge}
                disabled={acking}
                className="rounded border border-amber-400/50 bg-amber-500/10 px-2 py-1 text-[11px] font-semibold text-amber-100 hover:bg-amber-500/20 disabled:opacity-50"
              >
                {acking ? "Recording..." : "I understand — acknowledge and continue"}
              </button>
              {ackError && <p className="mt-1 text-[#e6a39b]">{ackError}</p>}
            </div>
          )}
          {safety.policy === "require_acknowledgment" && safety.acknowledged && (
            <p className="mt-1 text-emerald-300">Acknowledged — export unblocked.</p>
          )}
        </div>
      )}

      {questions.length > 0 && (
        <div>
          <p className="label mb-1 text-amber-300">Unresolved questions</p>
          <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-amber-200/90">
            {questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      )}

      {(criticals.length > 0 || warnings.length > 0) && (
        <div>
          <p className="label mb-1">Validation findings</p>
          <ul className="space-y-0.5 pl-0 text-[11px]">
            {criticals.map((c, i) => (
              <li key={`c-${i}`} className="flex gap-1.5 text-[#e6a39b]">
                <span aria-hidden>✗</span>
                <span>{c}</span>
              </li>
            ))}
            {warnings.map((w, i) => (
              <li key={`w-${i}`} className="flex gap-1.5 text-amber-300">
                <span aria-hidden>⚠</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {assumptions.length > 0 && (
        <details>
          <summary className="label cursor-pointer select-none">
            Assumptions ({assumptions.length})
          </summary>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[11px] text-slate-400">
            {assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </details>
      )}

      {limitations.length > 0 && (
        <details>
          <summary className="label cursor-pointer select-none">
            Known limitations ({limitations.length})
          </summary>
          <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[11px] text-slate-400">
            {limitations.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </details>
      )}

      {design.calibration_provenance && (
        <p className="text-[11px] leading-relaxed text-slate-500">
          {design.calibration_provenance}
        </p>
      )}

      <div className="border-t border-edge/60 pt-2">
        {!reportOpen && !reportDone && (
          <button
            type="button"
            onClick={() => setReportOpen(true)}
            className="text-[11px] font-medium text-slate-400 underline decoration-dotted hover:text-slate-200"
          >
            Report a problem with this design
          </button>
        )}
        {reportDone && (
          <p className="text-[11px] text-emerald-300">
            Thanks — this report is linked to design version{" "}
            {design.latest_version_number ?? 1} for follow-up.
          </p>
        )}
        {reportOpen && !reportDone && (
          <div className="space-y-2 text-[11px]">
            <p className="label">What went wrong?</p>
            <div className="flex flex-wrap gap-1.5">
              {FEEDBACK_CATEGORIES.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  onClick={() => toggleCategory(c.value)}
                  className={`rounded-full border px-2 py-0.5 ${
                    reportCategories.includes(c.value)
                      ? "border-amber-400/60 bg-amber-500/15 text-amber-100"
                      : "border-edge/60 text-slate-400 hover:border-edge"
                  }`}
                >
                  {c.label}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1">
              <TriToggle label="Printed successfully?" value={printSuccess} onChange={setPrintSuccess} />
              <TriToggle label="Fit as expected?" value={fitSuccess} onChange={setFitSuccess} />
            </div>

            <label className="flex items-start gap-1.5 pt-1">
              <input
                type="checkbox"
                checked={reportConsent}
                onChange={(e) => setReportConsent(e.target.checked)}
                className="mt-0.5"
              />
              <span className="text-slate-400">
                I consent to LunaiCAD storing my written explanation below to
                improve results. Without this, everything else in this report
                is still recorded, but the text is not.
              </span>
            </label>
            <textarea
              value={reportReason}
              onChange={(e) => setReportReason(e.target.value)}
              disabled={!reportConsent}
              placeholder={
                reportConsent
                  ? "Optional: describe what went wrong"
                  : "Check the consent box above to add a written explanation"
              }
              maxLength={2000}
              rows={2}
              className="w-full rounded border border-edge/60 bg-transparent p-1.5 text-slate-200 placeholder:text-slate-600 disabled:opacity-50"
            />

            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={submitReport}
                disabled={reportSubmitting || reportCategories.length === 0}
                className="rounded border border-edge/60 bg-white/5 px-2 py-1 font-semibold text-slate-200 hover:bg-white/10 disabled:opacity-50"
              >
                {reportSubmitting ? "Submitting..." : "Submit report"}
              </button>
              <button
                type="button"
                onClick={() => setReportOpen(false)}
                className="text-slate-500 hover:text-slate-300"
              >
                Cancel
              </button>
            </div>
            {reportError && <p className="text-[#e6a39b]">{reportError}</p>}
          </div>
        )}
      </div>
    </section>
  );
}

function TriToggle({
  label,
  value,
  onChange,
}: {
  label: string;
  value: boolean | null;
  onChange: (v: boolean | null) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-slate-500">{label}</span>
      {(["yes", "no", "unset"] as const).map((opt) => {
        const optValue = opt === "yes" ? true : opt === "no" ? false : null;
        const active = value === optValue;
        return (
          <button
            key={opt}
            type="button"
            onClick={() => onChange(optValue)}
            className={`rounded border px-1.5 py-0.5 text-[10px] ${
              active
                ? "border-amber-400/60 bg-amber-500/15 text-amber-100"
                : "border-edge/60 text-slate-500 hover:border-edge"
            }`}
          >
            {opt === "yes" ? "Yes" : opt === "no" ? "No" : "N/A"}
          </button>
        );
      })}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-slate-500">{k}</dt>
      <dd className="stat truncate text-right text-slate-300">{v}</dd>
    </div>
  );
}
