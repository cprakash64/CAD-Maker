import Link from "next/link";
import { ONBOARDING_EXAMPLES } from "@/lib/examples";

export default function LandingPage() {
  return (
    <div className="space-y-16 py-6">
      {/* Hero */}
      <section className="grid items-center gap-10 lg:grid-cols-[1.1fr_0.9fr]">
        <div className="space-y-6">
          <span className="label">Parametric CAD · plain English</span>
          <h1 className="text-4xl font-semibold leading-tight tracking-tight text-slate-50 sm:text-5xl">
            Describe a part.
            <br />
            Get <span className="text-accent">validated, manufacturable CAD.</span>
          </h1>
          <p className="max-w-xl text-base leading-relaxed text-slate-400">
            SourceCAD turns a written spec into real parametric geometry —
            brackets, enclosures, flanges, mounts, jigs — then measures the result
            and tells you whether it is dimensionally accurate and 3D‑print ready
            before you export STEP or STL.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link href="/signup" className="btn-primary">
              Start designing
            </Link>
            <Link href="/signin" className="btn-ghost">
              Sign in
            </Link>
          </div>
          <p className="text-xs text-slate-500">
            Not text‑to‑mesh. Every part is built from audited parametric features
            and verified against your requested dimensions.
          </p>
        </div>

        {/* Trust card — validation is the hero feature, not a footnote. */}
        <div className="card p-5">
          <div className="mb-3 flex items-center justify-between">
            <span className="label">Validation report</span>
            <span className="badge-pass">Pass</span>
          </div>
          <div className="space-y-2 text-sm">
            <Row k="Bounding box" v="60.0 × 20.0 × 60.0 mm" ok />
            <Row k="Holes / through" v="4 / 4" ok />
            <Row k="Single fused body" v="Yes" ok />
            <Row k="Watertight + manifold" v="Yes" ok />
            <Row k="Within tolerance" v="±0.5 mm" ok />
          </div>
          <p className="mt-3 border-t border-edge pt-3 text-xs text-slate-500">
            Failed designs are flagged and cannot be exported as manufacturable
            files — so a draft is never mistaken for a finished part.
          </p>
        </div>
      </section>

      {/* Capabilities */}
      <section className="grid gap-4 md:grid-cols-3">
        {[
          {
            t: "Structured generation",
            d: "The model emits a validated feature graph — never executable code. A trusted compiler builds the geometry with CadQuery / OpenCascade.",
          },
          {
            t: "Measured validation",
            d: "Every part is measured from its real B‑rep and mesh: dimensions, holes, volume, watertightness — compared against what you asked for.",
          },
          {
            t: "Editable parameters",
            d: "Adjust dimensions, holes and edges; the model rebuilds deterministically with no second guess. Export STEP for CAD and STL for printing.",
          },
        ].map((f) => (
          <div key={f.t} className="card p-5">
            <h3 className="text-sm font-semibold text-slate-100">{f.t}</h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">{f.d}</p>
          </div>
        ))}
      </section>

      {/* Examples */}
      <section className="space-y-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-slate-100">What you can build</h2>
          <Link href="/signup" className="text-sm text-accent hover:underline">
            Create an account →
          </Link>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {ONBOARDING_EXAMPLES.map((ex) => (
            <div key={ex.label} className="card p-4">
              <div className="label mb-1 text-slate-300">{ex.label}</div>
              <p className="text-sm leading-relaxed text-slate-400">{ex.prompt}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function Row({ k, v, ok }: { k: string; v: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-edge/60 pb-2 last:border-0 last:pb-0">
      <span className="text-slate-400">{k}</span>
      <span className="flex items-center gap-1.5">
        <span className="stat text-slate-200">{v}</span>
        {ok && <span className="text-emerald-400">✓</span>}
      </span>
    </div>
  );
}
