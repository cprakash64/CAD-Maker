import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { makeDesign } from "@/lib/testFixtures";
import TrustPanel from "./TrustPanel";

const { reportBadResult } = vi.hoisted(() => ({
  reportBadResult: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, reportBadResult },
  };
});

describe("TrustPanel — assumption review", () => {
  it("lists every assumption behind a labeled, countable disclosure", () => {
    const design = makeDesign({
      assumptions: ["Assumed PLA material", "Assumed 6mm wall thickness"],
    });
    render(<TrustPanel design={design} />);
    const summary = screen.getByText(/Assumptions \(2\)/);
    expect(summary).toBeInTheDocument();
    expect(screen.getByText("Assumed PLA material")).toBeInTheDocument();
    expect(screen.getByText("Assumed 6mm wall thickness")).toBeInTheDocument();
  });

  it("omits the assumptions disclosure entirely when there are none", () => {
    const design = makeDesign({ assumptions: [] });
    render(<TrustPanel design={design} />);
    expect(screen.queryByText(/Assumptions \(/)).not.toBeInTheDocument();
  });

  it("shows unresolved questions distinctly from resolved assumptions", () => {
    const design = makeDesign({
      unanswered_questions: ["What material should the bracket be?"],
    });
    render(<TrustPanel design={design} />);
    expect(screen.getByText("Unresolved questions")).toBeInTheDocument();
    expect(
      screen.getByText("What material should the bracket be?")
    ).toBeInTheDocument();
  });

  it("shows the interpreted intent restatement", () => {
    const design = makeDesign({ interpreted_intent: "Mounting plate with two holes" });
    render(<TrustPanel design={design} />);
    expect(screen.getByText(/Mounting plate with two holes/)).toBeInTheDocument();
  });
});

describe("TrustPanel — capability labels", () => {
  it.each([
    ["production_ready", "High-confidence template"],
    ["validated_beta", "Validated beta"],
    ["experimental", "Experimental"],
    ["unsupported", "Unsupported"],
  ])("renders the %s capability as %s", (level, label) => {
    const design = makeDesign({ capability_level: level });
    render(<TrustPanel design={design} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("does not render a capability badge when the level is unknown", () => {
    const design = makeDesign({ capability_level: null });
    render(<TrustPanel design={design} />);
    expect(screen.queryByText(/High-confidence template|Validated beta|Experimental/)).not.toBeInTheDocument();
  });

  it("shows a Beta pill for drawing-derived designs", () => {
    const design = makeDesign({ drawing_beta: true });
    render(<TrustPanel design={design} />);
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });
});

describe("TrustPanel — export eligibility / blocked exports", () => {
  it("shows a blocked notice with the reason when export is ineligible", () => {
    const design = makeDesign({
      export_eligibility: { eligible: false, reason: "Critical validation failure." },
    });
    render(<TrustPanel design={design} />);
    expect(screen.getByText("Manufacturable export blocked.")).toBeInTheDocument();
    expect(screen.getByText(/Critical validation failure\./)).toBeInTheDocument();
  });

  it("reports the per-format breakdown, including GLB staying available", () => {
    const design = makeDesign({
      export_eligibility: {
        eligible: false,
        reason: "Critical validation failure.",
        formats: { stl: false, step: false, glb: true },
      },
    });
    render(<TrustPanel design={design} />);
    expect(screen.getByText(/STL ✗/)).toBeInTheDocument();
    expect(screen.getByText(/STEP ✗/)).toBeInTheDocument();
    expect(screen.getByText(/GLB preview\s*✓/)).toBeInTheDocument();
  });

  it("shows a positive notice when export is eligible", () => {
    const design = makeDesign({ export_eligibility: { eligible: true, reason: null } });
    render(<TrustPanel design={design} />);
    expect(screen.getByText("Manufacturable export available.")).toBeInTheDocument();
  });
});

describe("TrustPanel — report a bad result", () => {
  beforeEach(() => {
    reportBadResult.mockReset();
  });

  it("keeps the form collapsed until the user opts in", () => {
    render(<TrustPanel design={makeDesign()} />);
    expect(screen.getByText("Report a problem with this design")).toBeInTheDocument();
    expect(screen.queryByText("What went wrong?")).not.toBeInTheDocument();
  });

  it("disables the explanation textarea until consent is checked", async () => {
    const user = userEvent.setup();
    render(<TrustPanel design={makeDesign()} />);
    await user.click(screen.getByText("Report a problem with this design"));
    const textarea = screen.getByPlaceholderText(
      "Check the consent box above to add a written explanation"
    );
    expect(textarea).toBeDisabled();

    await user.click(screen.getByRole("checkbox"));
    expect(
      screen.getByPlaceholderText("Optional: describe what went wrong")
    ).toBeEnabled();
  });

  it("requires at least one category before submitting", async () => {
    const user = userEvent.setup();
    render(<TrustPanel design={makeDesign()} />);
    await user.click(screen.getByText("Report a problem with this design"));
    expect(screen.getByText("Submit report")).toBeDisabled();

    await user.click(screen.getByText("Bad geometry"));
    expect(screen.getByText("Submit report")).toBeEnabled();
  });

  it("submits categories, print/fit outcomes, and drops the reason without consent", async () => {
    reportBadResult.mockResolvedValue({});
    const user = userEvent.setup();
    render(<TrustPanel design={makeDesign()} />);
    await user.click(screen.getByText("Report a problem with this design"));
    await user.click(screen.getByText("Wrong dimensions"));
    await user.click(screen.getAllByText("No")[0]!); // "Printed successfully?" -> No
    await user.type(
      screen.getByPlaceholderText("Check the consent box above to add a written explanation"),
      "should be ignored without consent"
    );
    await user.click(screen.getByText("Submit report"));

    await waitFor(() => expect(reportBadResult).toHaveBeenCalledTimes(1));
    const [, body] = reportBadResult.mock.calls[0]!;
    expect(body.categories).toEqual(["wrong_dimensions"]);
    expect(body.consent).toBe(false);
    expect(body.print_success).toBe(false);
    expect(body.fit_success).toBe(null);
    expect(screen.getByText(/Thanks — this report is linked to design version/)).toBeInTheDocument();
  });

  it("includes the reason when consent is given", async () => {
    reportBadResult.mockResolvedValue({});
    const user = userEvent.setup();
    render(<TrustPanel design={makeDesign()} />);
    await user.click(screen.getByText("Report a problem with this design"));
    await user.click(screen.getByText("Other"));
    await user.click(screen.getByRole("checkbox"));
    await user.type(
      screen.getByPlaceholderText("Optional: describe what went wrong"),
      "walls too thin"
    );
    await user.click(screen.getByText("Submit report"));

    await waitFor(() => expect(reportBadResult).toHaveBeenCalledTimes(1));
    const [, body] = reportBadResult.mock.calls[0]!;
    expect(body.consent).toBe(true);
    expect(body.reason).toBe("walls too thin");
  });
});
