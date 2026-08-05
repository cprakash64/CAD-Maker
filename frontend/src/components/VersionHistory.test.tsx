import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import type { VersionSummary } from "@/lib/types";
import VersionHistory from "./VersionHistory";

const { listVersions, restoreVersion } = vi.hoisted(() => ({
  listVersions: vi.fn(),
  restoreVersion: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, listVersions, restoreVersion },
  };
});

function version(overrides: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id: "v1",
    version_number: 1,
    edit_kind: "create",
    summary: "Initial generation",
    spec_hash: "abc",
    diff: [],
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("VersionHistory — version restoration", () => {
  beforeEach(() => {
    listVersions.mockReset();
    restoreVersion.mockReset();
  });

  it("renders nothing until the design has been edited at least once", () => {
    const { container } = render(
      <VersionHistory designId="d1" latestVersionNumber={null} busy={false} onRestored={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists versions newest-first with the current one marked", async () => {
    listVersions.mockResolvedValue([
      version({ id: "v2", version_number: 2, edit_kind: "regenerate", summary: "Edited parameters" }),
      version({ id: "v1", version_number: 1, edit_kind: "create", summary: "Initial generation" }),
    ]);
    const user = userEvent.setup();
    render(
      <VersionHistory designId="d1" latestVersionNumber={2} busy={false} onRestored={vi.fn()} />
    );
    await user.click(screen.getByText("Version history"));
    await waitFor(() => expect(screen.getByText("v2")).toBeInTheDocument());
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
  });

  it("restores a version after confirmation and reports the updated design", async () => {
    listVersions.mockResolvedValue([
      version({ id: "v2", version_number: 2, edit_kind: "regenerate" }),
      version({ id: "v1", version_number: 1, edit_kind: "create" }),
    ]);
    const restored = { id: "d1" } as never;
    restoreVersion.mockResolvedValue(restored);
    const onRestored = vi.fn();
    const user = userEvent.setup();
    render(
      <VersionHistory designId="d1" latestVersionNumber={2} busy={false} onRestored={onRestored} />
    );
    await user.click(screen.getByText("Version history"));
    await waitFor(() => screen.getByText("v1"));

    await user.click(screen.getByText("Restore this version"));
    expect(screen.getByText("Restore version 1?")).toBeInTheDocument();

    await user.click(screen.getByText("Confirm"));
    await waitFor(() => expect(restoreVersion).toHaveBeenCalledWith("d1", "v1"));
    expect(onRestored).toHaveBeenCalledWith(restored);
  });

  it("undoes the last edit by restoring the previous version directly", async () => {
    listVersions.mockResolvedValue([
      version({ id: "v2", version_number: 2 }),
      version({ id: "v1", version_number: 1 }),
    ]);
    restoreVersion.mockResolvedValue({ id: "d1" } as never);
    const user = userEvent.setup();
    render(
      <VersionHistory designId="d1" latestVersionNumber={2} busy={false} onRestored={vi.fn()} />
    );
    await user.click(screen.getByText("Version history"));
    await waitFor(() => screen.getByText("v1"));

    await user.click(screen.getByText("Undo last edit"));
    await waitFor(() => expect(restoreVersion).toHaveBeenCalledWith("d1", "v1"));
  });
});

describe("VersionHistory — error states", () => {
  beforeEach(() => {
    listVersions.mockReset();
    restoreVersion.mockReset();
  });

  it("shows a message when the version list fails to load", async () => {
    listVersions.mockRejectedValue(new ApiError("Could not load version history", 500));
    const user = userEvent.setup();
    render(
      <VersionHistory designId="d1" latestVersionNumber={2} busy={false} onRestored={vi.fn()} />
    );
    await user.click(screen.getByText("Version history"));
    await waitFor(() =>
      expect(screen.getByText("Could not load version history")).toBeInTheDocument()
    );
  });

  it("shows a message when restoring fails, without crashing", async () => {
    listVersions.mockResolvedValue([
      version({ id: "v2", version_number: 2 }),
      version({ id: "v1", version_number: 1 }),
    ]);
    restoreVersion.mockRejectedValue(new ApiError("This version is no longer valid.", 422));
    const user = userEvent.setup();
    render(
      <VersionHistory designId="d1" latestVersionNumber={2} busy={false} onRestored={vi.fn()} />
    );
    await user.click(screen.getByText("Version history"));
    await waitFor(() => screen.getByText("v1"));
    await user.click(screen.getByText("Restore this version"));
    await user.click(screen.getByText("Confirm"));
    await waitFor(() =>
      expect(screen.getByText("This version is no longer valid.")).toBeInTheDocument()
    );
  });
});
