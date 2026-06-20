import Link from "next/link";
import { ONBOARDING_EXAMPLES } from "@/lib/examples";

export default function LandingPage() {
  return (
    <div className="space-y-12">
      <section className="space-y-5 py-10">
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
          Describe a part. <span className="text-accent">Get editable CAD.</span>
        </h1>
        <p className="max-w-2xl text-lg text-slate-300">
          SourceCAD turns plain English into parametric, manufacturable
          mechanical parts — brackets, enclosures, mounts, adapters, spacers and
          jigs. Not decorative text-to-3D: real geometry you can edit and export
          as STL and STEP.
        </p>
        <div className="flex gap-3">
          <Link href="/signup" className="btn-primary">
            Get started
          </Link>
          <Link href="/signin" className="btn-ghost">
            Sign in
          </Link>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-3">
        {[
          {
            t: "Structured, safe generation",
            d: "The LLM emits a validated JSON spec — never executable code. Trusted local templates build the geometry.",
          },
          {
            t: "Editable parameters",
            d: "Tune dimensions, holes and edges and the model regenerates deterministically, no LLM round-trip.",
          },
          {
            t: "Manufacturability checks",
            d: "Wall thickness, hole sizing, spacing, edge distance and print-risk warnings on every model.",
          },
        ].map((f) => (
          <div key={f.t} className="card p-5">
            <h3 className="font-semibold">{f.t}</h3>
            <p className="mt-2 text-sm text-slate-300">{f.d}</p>
          </div>
        ))}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">What you can make</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {ONBOARDING_EXAMPLES.map((ex) => (
            <div key={ex.label} className="card p-4">
              <div className="text-sm font-medium text-accent">{ex.label}</div>
              <p className="mt-1 text-sm text-slate-300">{ex.prompt}</p>
            </div>
          ))}
        </div>
        <p className="text-sm text-slate-400">
          <Link href="/signup" className="text-accent underline">
            Create an account
          </Link>{" "}
          to start generating.
        </p>
      </section>
    </div>
  );
}
