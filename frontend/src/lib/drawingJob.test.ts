/** Async drawing-job polling state machine: progress mapping, stage updates,
 *  success transition, failure, timeout, and lost-job handling. */
import { describe, expect, it } from "vitest";
import {
  DrawingJobError,
  pollDrawingJob,
  stepIndexForStage,
  successfulDesignId,
  type DrawingJobStage,
  type DrawingJobStatus,
} from "./drawingJob";

function snap(partial: Partial<DrawingJobStatus>): DrawingJobStatus {
  return {
    job_id: "j1",
    status: "running",
    stage: "queued",
    progress: 5,
    message: null,
    error: null,
    design_id: null,
    ...partial,
  };
}

function sequenceFetcher(snaps: DrawingJobStatus[]) {
  let i = 0;
  return async (): Promise<DrawingJobStatus> =>
    snaps[Math.min(i++, snaps.length - 1)] as DrawingJobStatus;
}

const fast = { intervalMs: 1, sleep: async () => {} };

describe("stepIndexForStage (progress state rendering)", () => {
  it("maps backend stages onto the 6-step UI in order", () => {
    const order: DrawingJobStage[] = [
      "queued", "reading_drawing", "extracting_dimensions",
      "building_cad", "validating", "exporting", "done",
    ];
    const idx = order.map(stepIndexForStage);
    expect(idx).toEqual([1, 1, 2, 3, 4, 5, 6]);
    expect(stepIndexForStage("failed")).toBe(-1);
  });
});

describe("successfulDesignId (studio navigation gate)", () => {
  it("returns the id only for generated=true with a real design id", () => {
    expect(successfulDesignId({ generated: true, design: { id: "d1" } })).toBe("d1");
  });

  it("generated=false with a null design never navigates", () => {
    expect(successfulDesignId({ generated: false, design: null })).toBeNull();
  });

  it("generated=false with a (stale) design object never navigates", () => {
    expect(successfulDesignId({ generated: false, design: { id: "d1" } })).toBeNull();
  });

  it("generated=true without a design id never navigates", () => {
    expect(successfulDesignId({ generated: true, design: null })).toBeNull();
    expect(successfulDesignId({ generated: true, design: {} })).toBeNull();
    expect(successfulDesignId({ generated: true, design: { id: "" } })).toBeNull();
  });

  it("missing/undefined results never navigate", () => {
    expect(successfulDesignId(null)).toBeNull();
    expect(successfulDesignId(undefined)).toBeNull();
    expect(successfulDesignId({})).toBeNull();
  });
});

describe("pollDrawingJob", () => {
  it("polls until done, reporting each stage transition once", async () => {
    const snaps = [
      snap({ stage: "queued" }),
      snap({ stage: "reading_drawing" }),
      snap({ stage: "reading_drawing" }), // duplicate: no extra update
      snap({ stage: "building_cad" }),
      snap({ status: "done", stage: "done", progress: 100,
             design_id: "d1", result: { generated: true } }),
    ];
    const seen: string[] = [];
    const finished = await pollDrawingJob("j1", {
      fetchStatus: sequenceFetcher(snaps),
      onUpdate: (s) => seen.push(s.stage),
      ...fast,
    });
    expect(finished.status).toBe("done");
    expect(finished.design_id).toBe("d1");
    expect(finished.result).toEqual({ generated: true });
    expect(seen).toEqual(["queued", "reading_drawing", "building_cad", "done"]);
  });

  it("rejects with kind 'failed' when the job fails", async () => {
    const snaps = [
      snap({ stage: "reading_drawing" }),
      snap({ status: "failed", stage: "failed", error: "kernel exploded" }),
    ];
    await expect(
      pollDrawingJob("j1", { fetchStatus: sequenceFetcher(snaps), ...fast })
    ).rejects.toSatisfy((e: unknown) =>
      e instanceof DrawingJobError && e.kind === "failed" &&
      e.message === "kernel exploded");
  });

  it("rejects with kind 'timeout' when the job never finishes", async () => {
    let t = 0;
    await expect(
      pollDrawingJob("j1", {
        fetchStatus: async () => snap({ stage: "building_cad" }),
        timeoutMs: 100,
        now: () => (t += 60),
        ...fast,
      })
    ).rejects.toSatisfy(
      (e: unknown) => e instanceof DrawingJobError && e.kind === "timeout");
  });

  it("rejects with kind 'lost' when the job 404s (server restart)", async () => {
    await expect(
      pollDrawingJob("j1", {
        fetchStatus: async () => {
          const err = new Error("not found") as Error & { status: number };
          err.status = 404;
          throw err;
        },
        ...fast,
      })
    ).rejects.toSatisfy(
      (e: unknown) => e instanceof DrawingJobError && e.kind === "lost");
  });

  it("propagates non-404 fetch errors (network/auth) as-is", async () => {
    const boom = new Error("network down");
    await expect(
      pollDrawingJob("j1", {
        fetchStatus: async () => {
          throw boom;
        },
        ...fast,
      })
    ).rejects.toBe(boom);
  });
});
