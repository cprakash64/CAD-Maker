import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EditDiff from "./EditDiff";

describe("EditDiff — old/new values for the affected feature", () => {
  it("shows the old and new value for a changed dimension", () => {
    render(<EditDiff diff={[{ field: "dimensions.width", old: 80, new: 100 }]} />);
    expect(screen.getByText("width:")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("identifies the affected hole by a human 1-based index", () => {
    render(<EditDiff diff={[{ field: "holes[0].diameter", old: 6, new: 9 }]} />);
    expect(screen.getByText("hole 1 diameter:")).toBeInTheDocument();
  });

  it("renders every entry when an edit touches multiple fields", () => {
    render(
      <EditDiff
        diff={[
          { field: "dimensions.width", old: 80, new: 100 },
          { field: "dimensions.depth", old: 40, new: 50 },
        ]}
      />
    );
    expect(screen.getByText("width:")).toBeInTheDocument();
    expect(screen.getByText("depth:")).toBeInTheDocument();
  });

  it("formats a null old value as an em dash rather than blank", () => {
    render(<EditDiff diff={[{ field: "material", old: null, new: "PLA" }]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("PLA")).toBeInTheDocument();
  });

  it("renders nothing when there is no diff", () => {
    const { container } = render(<EditDiff diff={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("uses the custom title when given one", () => {
    render(
      <EditDiff
        diff={[{ field: "dimensions.width", old: 80, new: 100 }]}
        title="Changed in this version"
      />
    );
    expect(screen.getByText("Changed in this version")).toBeInTheDocument();
  });
});
