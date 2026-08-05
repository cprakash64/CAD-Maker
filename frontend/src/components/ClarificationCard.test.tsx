import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { makeDesign } from "@/lib/testFixtures";
import ClarificationCard from "./ClarificationCard";

describe("ClarificationCard — clarification", () => {
  it("lists every open clarification question", () => {
    const design = makeDesign({
      needs_clarification: true,
      clarification_questions: [
        "What material should this be printed in?",
        "How thick should the wall be?",
      ],
    });
    render(<ClarificationCard design={design} busy={false} onGenerateDefaults={vi.fn()} />);
    expect(
      screen.getByText("What material should this be printed in?")
    ).toBeInTheDocument();
    expect(screen.getByText("How thick should the wall be?")).toBeInTheDocument();
  });

  it("falls back to the single clarification_question when the list is empty", () => {
    const design = makeDesign({
      needs_clarification: true,
      clarification_question: "What size bracket do you need?",
      clarification_questions: [],
    });
    render(<ClarificationCard design={design} busy={false} onGenerateDefaults={vi.fn()} />);
    expect(screen.getByText("What size bracket do you need?")).toBeInTheDocument();
  });

  it("offers ready-to-generate clarification options as links", () => {
    const design = makeDesign({
      needs_clarification: true,
      clarification_options: [
        { label: "M6 hex nut", prompt: "a standard M6 hex nut" },
      ],
    });
    render(<ClarificationCard design={design} busy={false} onGenerateDefaults={vi.fn()} />);
    const link = screen.getByRole("link", { name: /M6 hex nut/ });
    expect(link).toHaveAttribute("href", "/new?prompt=a%20standard%20M6%20hex%20nut");
  });

  it("calls onGenerateDefaults when the defaults button is clicked", async () => {
    const onGenerateDefaults = vi.fn();
    const design = makeDesign({
      needs_clarification: true,
      can_generate_with_defaults: true,
      clarification_question: "Need more info",
    });
    const user = userEvent.setup();
    render(
      <ClarificationCard design={design} busy={false} onGenerateDefaults={onGenerateDefaults} />
    );
    await user.click(screen.getByText("Generate with defaults"));
    expect(onGenerateDefaults).toHaveBeenCalledOnce();
  });

  it("disables the defaults button while busy", () => {
    const design = makeDesign({
      needs_clarification: true,
      can_generate_with_defaults: true,
      clarification_question: "Need more info",
    });
    render(<ClarificationCard design={design} busy onGenerateDefaults={vi.fn()} />);
    expect(screen.getByText("Generate with defaults")).toBeDisabled();
  });
});
