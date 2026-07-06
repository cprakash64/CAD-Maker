/**
 * Drawing → CAD async job polling — framework-agnostic state machine.
 *
 * The backend returns 202 + job_id; this module polls the job endpoint,
 * reports stage transitions to the UI, and resolves with the finished result
 * (or rejects on job failure / poll timeout so the UI can never sit on
 * "Generating…" forever).
 */

export type DrawingJobStage =
  | "queued"
  | "reading_drawing"
  | "interpreting"
  | "extracting_dimensions"
  | "fallback_generating"
  | "building_cad"
  | "validating"
  | "exporting"
  | "done"
  | "failed";

export interface DrawingJobStatus {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  stage: DrawingJobStage;
  progress: number;
  message: string | null;
  error: string | null;
  design_id: string | null;
  result?: unknown;
}

/** User-facing labels for the progress UI, in pipeline order. */
export const JOB_STAGE_LABELS: Record<DrawingJobStage, string> = {
  queued: "Queued",
  reading_drawing: "Reading drawing",
  interpreting: "Interpreting drawing",
  extracting_dimensions: "Extracting dimensions",
  fallback_generating: "Building from drawing geometry",
  building_cad: "Building CAD",
  validating: "Validating",
  exporting: "Exporting",
  done: "Done",
  failed: "Failed",
};

/** Map a backend stage onto the 6-step UI:
 *  Uploading → Reading drawing → Extracting dimensions → Building CAD →
 *  Validating → Exporting. (Uploading is client-side, index 0.) */
export function stepIndexForStage(stage: DrawingJobStage): number {
  switch (stage) {
    case "queued":
    case "reading_drawing":
      return 1;
    case "interpreting":
    case "extracting_dimensions":
      return 2;
    case "fallback_generating":
    case "building_cad":
      return 3;
    case "validating":
      return 4;
    case "exporting":
      return 5;
    case "done":
      return 6; // all steps complete
    case "failed":
      return -1;
  }
}

/**
 * The ONLY success gate for opening a generated drawing design: requires
 * `generated === true` AND a design object with a non-empty id. Anything else
 * (generated=false, missing design, missing id) returns null — the UI must
 * show the failure state and NEVER navigate to the studio.
 */
export function successfulDesignId(
  res:
    | { generated?: boolean | null; design?: { id?: string | null } | null }
    | null
    | undefined
): string | null {
  if (!res || res.generated !== true) return null;
  const id = res.design?.id;
  return typeof id === "string" && id.length > 0 ? id : null;
}

export class DrawingJobError extends Error {
  kind: "failed" | "timeout" | "lost";
  constructor(message: string, kind: "failed" | "timeout" | "lost") {
    super(message);
    this.kind = kind;
  }
}

export interface PollOptions {
  /** Fetch one status snapshot; throw ApiError(404) when the job is unknown. */
  fetchStatus: (jobId: string) => Promise<DrawingJobStatus>;
  onUpdate?: (status: DrawingJobStatus) => void;
  intervalMs?: number;
  timeoutMs?: number;
  sleep?: (ms: number) => Promise<void>;
  /** Monotonic clock (injectable for tests). */
  now?: () => number;
}

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/**
 * Poll until the job finishes. Resolves with the final status (status "done");
 * rejects with DrawingJobError on job failure ("failed"), poll timeout
 * ("timeout"), or a vanished job — e.g. server restart — ("lost").
 */
export async function pollDrawingJob(
  jobId: string,
  opts: PollOptions
): Promise<DrawingJobStatus> {
  const interval = opts.intervalMs ?? 1200;
  const timeout = opts.timeoutMs ?? 300_000;
  const sleep = opts.sleep ?? defaultSleep;
  const now = opts.now ?? Date.now;
  const start = now();
  let lastStage: string | null = null;

  for (;;) {
    let status: DrawingJobStatus;
    try {
      status = await opts.fetchStatus(jobId);
    } catch (e) {
      const code = (e as { status?: number }).status;
      if (code === 404) {
        throw new DrawingJobError(
          "The generation job was lost (the server may have restarted). Please retry.",
          "lost"
        );
      }
      throw e; // network/auth errors propagate as-is
    }
    if (status.stage !== lastStage) {
      lastStage = status.stage;
      opts.onUpdate?.(status);
    }
    if (status.status === "done") return status;
    if (status.status === "failed") {
      throw new DrawingJobError(
        status.error ?? "Generation failed. Please retry.",
        "failed"
      );
    }
    if (now() - start > timeout) {
      throw new DrawingJobError(
        "Generation is taking longer than expected and was stopped on the client. " +
          "The drawing may be too complex — try again or simplify it.",
        "timeout"
      );
    }
    await sleep(interval);
  }
}
