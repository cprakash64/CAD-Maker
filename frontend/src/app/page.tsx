"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { usePartPrompt } from "@/components/PartPromptOverlay";

export default function LandingPage() {
  const { user } = useAuth();
  const partPrompt = usePartPrompt();

  return (
    <div className="page max-w-6xl space-y-20 sm:space-y-24 xl:max-w-7xl 2xl:max-w-[90rem]">
      {/* ---------------------------------------------------------------- Hero */}
      <section className="grid items-center gap-12 pt-8 lg:grid-cols-[1.02fr_0.98fr] lg:gap-16 lg:pt-16 2xl:gap-24">
        <div className="animate-fade-in-up space-y-7">
          <span className="inline-flex items-center gap-2 rounded-full border border-edge bg-raised/40 px-3 py-1 text-[11px] font-medium tracking-wide text-slate-300">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" />
            Parametric CAD · plain English
          </span>

          <h1 className="text-balance text-[2.5rem] font-semibold leading-[1.05] tracking-tight text-slate-50 sm:text-[3.25rem]">
            Generate validated CAD from a{" "}
            <span className="bg-gradient-to-r from-champagne to-accent bg-clip-text text-transparent">
              plain-English part spec.
            </span>
          </h1>

          <p className="max-w-xl text-base leading-relaxed text-slate-300 sm:text-[17px]">
            Describe a bracket, enclosure, jig, flange, clamp, spacer, or gear.
            LunaiCAD builds parametric geometry, checks dimensions, and lets you
            export STEP or STL.
          </p>

          <ul className="space-y-2.5">
            {[
              "Parametric geometry, not text-to-mesh",
              "Manufacturability checks before export",
              "STEP and STL export when ready",
            ].map((b) => (
              <li key={b} className="flex items-start gap-2.5 text-sm text-slate-300">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-accent" />
                {b}
              </li>
            ))}
          </ul>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            {user ? (
              <>
                <button className="btn-primary" onClick={() => partPrompt.open()}>
                  New part
                </button>
                <Link href="/dashboard" className="btn-ghost">
                  Workspace
                </Link>
              </>
            ) : (
              <>
                <button className="btn-primary" onClick={() => partPrompt.open()}>
                  Generate CAD
                </button>
                <Link href="/signin" className="btn-ghost">
                  Sign in
                </Link>
              </>
            )}
          </div>

          <p className="text-xs text-slate-500">
            {user
              ? "Opens a single prompt surface — describe the part and generate."
              : "Type your spec first — no account needed to start."}
          </p>
        </div>

        {/* What you get — a calm preview of the validated result. */}
        <div className="animate-fade-in-up [animation-delay:120ms]">
          <ValidationInstrument />
        </div>
      </section>

      {/* -------------------------------------------------------- How it works */}
      <section className="grid gap-px overflow-hidden rounded-2xl border border-[color:var(--glass-border)] bg-edge/60 md:grid-cols-3">
        {[
          {
            n: "01",
            t: "Describe the part",
            d: "Write the spec in plain English — dimensions, holes, thickness, material. No CAD software required.",
          },
          {
            n: "02",
            t: "Generate the geometry",
            d: "A validated feature graph compiles to a real parametric B-rep with CadQuery / OpenCascade — never executable code.",
          },
          {
            n: "03",
            t: "Inspect & export",
            d: "Review measured dimensions, adjust parameters, then export STEP for CAD or STL for printing.",
          },
        ].map((f) => (
          <div key={f.t} className="bg-panel/80 p-6 backdrop-blur-sm">
            <span className="stat text-xs tracking-[0.2em] text-accent/80">{f.n}</span>
            <h3 className="mt-3 text-[15px] font-semibold tracking-tight text-slate-100">
              {f.t}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-400">{f.d}</p>
          </div>
        ))}
      </section>
    </div>
  );
}

/* --------------------------------------------------------------------------
   Validation instrument panel — shows what you get after generation.
-------------------------------------------------------------------------- */
function ValidationInstrument() {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-[color:var(--glass-border)] bg-panel/70 shadow-lift backdrop-blur-xl">
      <div className="etched-grid pointer-events-none absolute inset-0 opacity-[0.5] [mask-image:radial-gradient(120%_120%_at_70%_0%,#000,transparent_75%)]" />

      <div className="relative">
        <div className="flex items-center justify-between border-b border-edge px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <span className="h-2 w-2 rounded-full bg-accent shadow-[0_0_10px_2px_rgba(214,170,77,0.5)]" />
            <span className="label text-slate-400">Validation report</span>
          </div>
          <span className="badge-pass">
            <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
            Pass
          </span>
        </div>

        <div className="flex items-center justify-between border-b border-edge/70 px-5 py-3">
          <span className="text-sm font-medium text-slate-200">Adapter plate</span>
          <span className="stat text-[11px] text-slate-500">REV·A · STEP / STL</span>
        </div>

        <div className="divide-y divide-edge/60 px-5">
          <InstrumentRow k="Bounding box" v="60.0 × 20.0 × 60.0 mm" />
          <InstrumentRow k="Holes / through" v="4 / 4" />
          <InstrumentRow k="Single fused body" v="Yes" />
          <InstrumentRow k="Watertight + manifold" v="Yes" />
          <InstrumentRow k="Within tolerance" v="±0.5 mm" />
        </div>

        <p className="border-t border-edge px-5 py-3.5 text-xs leading-relaxed text-slate-500">
          Failed designs are flagged and cannot be exported as manufacturable
          files — so a draft is never mistaken for a finished part.
        </p>
      </div>
    </div>
  );
}

function InstrumentRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5 text-sm">
      <span className="text-slate-400">{k}</span>
      <span className="flex items-center gap-2">
        <span className="stat text-slate-100">{v}</span>
        <span
          className="grid h-4 w-4 place-items-center rounded-full bg-accent/15 text-[9px] text-accent"
          aria-label="ok"
        >
          ✓
        </span>
      </span>
    </div>
  );
}
