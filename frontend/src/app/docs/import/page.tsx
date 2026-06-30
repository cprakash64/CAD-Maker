export const metadata = { title: "Import & Compatibility — LunaiCAD" };

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-5">
      <h2 className="mb-2 text-base font-semibold tracking-tight text-slate-100">{title}</h2>
      <div className="space-y-2 text-sm leading-relaxed text-slate-300">{children}</div>
    </section>
  );
}

export default function ImportDocsPage() {
  return (
    <div className="page max-w-3xl space-y-5">
      <div className="space-y-1.5">
        <span className="label block">Documentation</span>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-50">
          Import &amp; Compatibility
        </h1>
        <p className="mt-1 text-sm leading-relaxed text-slate-400">
          Every part exports as <strong>STEP</strong> (parametric-friendly B-rep)
          and <strong>STL</strong> (mesh). The “CAD Package” download bundles
          STEP, STL, the DesignSpec JSON, a manufacturing report, and drawing
          views. Use STEP for CAD tools and STL for 3D printing.
        </p>
      </div>

      <Section title="Fusion 360">
        <ol className="list-decimal space-y-1 pl-5">
          <li>Download the CAD Package and unzip it.</li>
          <li>In Fusion 360: <em>File → Open → Upload</em> and choose the <code>.step</code> file.</li>
          <li>The part imports as a solid body; right-click to create components or edit features.</li>
          <li>For printing, you can instead insert the <code>.stl</code> via <em>Insert → Insert Mesh</em>.</li>
        </ol>
      </Section>

      <Section title="AutoCAD">
        <ol className="list-decimal space-y-1 pl-5">
          <li>Use <code>IMPORT</code> and select the <code>.step</code> file (AutoCAD 3D / Mechanical).</li>
          <li>For 2D drawings, import the provided <code>.svg</code> views, or place the PNGs as references.</li>
          <li>3D solids can be flattened with <code>FLATSHOT</code> to create 2D layouts.</li>
        </ol>
      </Section>

      <Section title="FreeCAD (free)">
        <ol className="list-decimal space-y-1 pl-5">
          <li><em>File → Open</em> the <code>.step</code> file.</li>
          <li>Switch to the <em>Part</em> or <em>TechDraw</em> workbench to add dimensions/drawings.</li>
          <li>STEP files from LunaiCAD are generated with OpenCascade — the same kernel FreeCAD uses — so they import cleanly.</li>
        </ol>
      </Section>

      <Section title="Known limitations">
        <ul className="list-disc space-y-1 pl-5">
          <li>Parts are generated from parametric templates, not full feature trees — imported STEP is a solid body, not editable Fusion/SolidWorks features.</li>
          <li>Drawing views are template-dimensioned where reliable; always verify critical dimensions before manufacturing.</li>
          <li>Drawing-to-CAD is <strong>assistance</strong>: it interprets a 2D image and asks you to confirm — it is not an exact, guaranteed conversion.</li>
          <li>DXF export is not available yet; SVG drawing views are provided instead.</li>
          <li>The crankshaft template is a demonstration model, not a balanced, production-ready engine part.</li>
        </ul>
      </Section>
    </div>
  );
}
