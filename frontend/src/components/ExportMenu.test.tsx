import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ExportMenu from "./ExportMenu";

describe("ExportMenu — blocked exports", () => {
  it("does not offer STL/STEP when the design failed validation, but keeps GLB", async () => {
    const onDownload = vi.fn();
    const user = userEvent.setup();
    render(
      <ExportMenu
        formats={["stl", "step"]}
        blocked
        previewAvailable
        hasPackage
        onDownload={onDownload}
        onPackage={vi.fn()}
      />
    );
    await user.click(screen.getByRole("button", { name: /Export/ }));

    expect(screen.queryByText("STL")).not.toBeInTheDocument();
    expect(screen.queryByText("STEP")).not.toBeInTheDocument();
    expect(screen.getByText("GLB")).toBeInTheDocument();

    await user.click(screen.getByText("GLB"));
    expect(onDownload).toHaveBeenCalledWith("glb");
  });

  it("does not offer the CAD package download when blocked", async () => {
    const user = userEvent.setup();
    render(
      <ExportMenu
        formats={["stl", "step"]}
        blocked
        previewAvailable
        hasPackage
        onDownload={vi.fn()}
        onPackage={vi.fn()}
      />
    );
    await user.click(screen.getByRole("button", { name: /Export/ }));
    expect(screen.queryByText(/CAD package/)).not.toBeInTheDocument();
  });

  it("offers STL and STEP with intent-focused hints when not blocked", async () => {
    const user = userEvent.setup();
    render(
      <ExportMenu
        formats={["stl", "step"]}
        blocked={false}
        hasPackage
        onDownload={vi.fn()}
        onPackage={vi.fn()}
      />
    );
    await user.click(screen.getByRole("button", { name: /Export/ }));
    expect(screen.getByText("STL")).toBeInTheDocument();
    expect(screen.getByText("3D printing")).toBeInTheDocument();
    expect(screen.getByText("STEP")).toBeInTheDocument();
    expect(screen.getByText("CAD editing")).toBeInTheDocument();
  });

  it("shows an unavailable state when there is nothing to export and it isn't blocked", async () => {
    const user = userEvent.setup();
    render(
      <ExportMenu formats={[]} blocked={false} hasPackage={false} onDownload={vi.fn()} onPackage={vi.fn()} />
    );
    await user.click(screen.getByRole("button", { name: /Export/ }));
    expect(screen.getByText("Export unavailable")).toBeInTheDocument();
    expect(screen.getByText("Generate a valid part first.")).toBeInTheDocument();
  });
});
