import type {
  AuthResponse,
  Design,
  DesignSummary,
  Feedback,
  Hole,
  TemplateInfo,
} from "./types";
import {
  pollDrawingJob,
  type DrawingJobStage,
  type DrawingJobStatus,
} from "./drawingJob";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const TOKEN_KEY = "sourcecad_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

// Default per-request timeout. Generation endpoints (create/regenerate/modify/
// edits) get a longer one — the backend bounds its own work to ~120s, so the
// client waits a little longer, then aborts so the UI never hangs forever.
const DEFAULT_TIMEOUT_MS = 45_000;
export const GENERATION_TIMEOUT_MS = 150_000;

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<T> {
  const token = getToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (e) {
    // A timeout abort must surface as a clear, actionable error so the caller
    // can exit its loading state instead of hanging on "Generating…".
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(
        `The request timed out after ${Math.round(timeoutMs / 1000)}s ` +
          `(${init?.method ?? "GET"} ${path}). The backend may be busy or ` +
          `misconfigured (check the LLM provider/model). Please try again.`,
        0,
        `${init?.method ?? "GET"} ${path}`
      );
    }
    // Network-level failure (backend down, wrong port, CORS, DNS). Never let a
    // bare "TypeError: Failed to fetch" reach the user — say exactly which
    // endpoint failed and how to fix it.
    throw new ApiError(
      `Cannot reach the LunaiCAD backend at ${API_BASE} ` +
        `(${init?.method ?? "GET"} ${path}). ` +
        `Check that the backend is running — \`uvicorn app.main:app --port 8000\` ` +
        `from backend/ — and that NEXT_PUBLIC_API_BASE points at it. ` +
        `(${e instanceof Error ? e.message : String(e)})`,
      0
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    let code: string | undefined;
    let safeToRetry: boolean | undefined;
    try {
      const body = (await res.json()) as { detail?: string | StructuredErrorDetail };
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (body.detail && typeof body.detail === "object") {
        // Structured error (e.g. face-edit unsupported_operation): carry the
        // machine code + retry hint so the UI can show a calm limitation.
        detail = body.detail.message ?? detail;
        code = body.detail.code;
        safeToRetry = body.detail.safe_to_retry;
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, res.status, `${init?.method ?? "GET"} ${path}`, code, safeToRetry);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Structured error body some endpoints return under `detail` (e.g. face-edit). */
export interface StructuredErrorDetail {
  code: string;
  operation?: string | null;
  selection_type?: string;
  message: string;
  safe_to_retry?: boolean;
}

export class ApiError extends Error {
  status: number; // 0 = network-level failure (backend unreachable)
  endpoint?: string; // "POST /api/drawings/generate"
  /** Machine code from a structured error body (e.g. "unsupported_operation"). */
  code?: string;
  /** True when the request failed safely (design intact) and the user may retry. */
  safeToRetry?: boolean;
  constructor(
    message: string,
    status: number,
    endpoint?: string,
    code?: string,
    safeToRetry?: boolean
  ) {
    super(message);
    this.status = status;
    this.endpoint = endpoint;
    this.code = code;
    this.safeToRetry = safeToRetry;
  }
}

export const api = {
  base: API_BASE,

  // Auth
  signup: (email: string, password: string) =>
    request<AuthResponse>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  login: (email: string, password: string) =>
    request<AuthResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<{ id: string; email: string }>("/api/auth/me"),

  // Designs — generation endpoints use the longer timeout (CAD can take a while).
  createDesign: (prompt: string) =>
    request<Design>(
      "/api/designs/create",
      { method: "POST", body: JSON.stringify({ prompt }) },
      GENERATION_TIMEOUT_MS
    ),
  getDesign: (id: string) => request<Design>(`/api/designs/${id}`),
  listDesigns: () => request<DesignSummary[]>("/api/designs"),
  regenerate: (
    id: string,
    dimensions: Record<string, number>,
    holes?: Hole[]
  ) =>
    request<Design>(
      `/api/designs/${id}/regenerate`,
      { method: "POST", body: JSON.stringify({ dimensions, holes }) },
      GENERATION_TIMEOUT_MS
    ),
  modify: (id: string, prompt: string) =>
    request<Design>(
      `/api/designs/${id}/modify`,
      { method: "POST", body: JSON.stringify({ prompt }) },
      GENERATION_TIMEOUT_MS
    ),
  exportDesign: (id: string) =>
    request<Design>(`/api/designs/${id}/export`, { method: "POST" }, GENERATION_TIMEOUT_MS),
  generateWithDefaults: (id: string) =>
    request<Design>(
      `/api/designs/${id}/generate-with-defaults`,
      { method: "POST" },
      GENERATION_TIMEOUT_MS
    ),
  templates: () => request<TemplateInfo[]>("/api/templates"),

  // Feedback
  submitFeedback: (
    id: string,
    rating: "up" | "down",
    categories: string[],
    comment: string
  ) =>
    request<Feedback>(`/api/designs/${id}/feedback`, {
      method: "POST",
      body: JSON.stringify({ rating, categories, comment: comment || null }),
    }),

  // Owner-checked download URL (sends bearer via fetch in the component).
  downloadUrl: (id: string, fmt: string) =>
    `${API_BASE}/api/designs/${id}/files/${fmt}`,
  viewUrl: (id: string, view: string, fmt: "png" | "svg" = "png") =>
    `${API_BASE}/api/designs/${id}/views/${view}?fmt=${fmt}`,
  packageUrl: (id: string) => `${API_BASE}/api/designs/${id}/package`,

  // Localized point-and-prompt edit.
  localizedEdit: (id: string, body: LocalizedEdit) =>
    request<Design>(
      `/api/designs/${id}/localized-edit`,
      { method: "POST", body: JSON.stringify(body) },
      GENERATION_TIMEOUT_MS
    ),

  // Circle-to-edit: apply an edit to a feature resolved from a circle selection.
  circleEdit: (id: string, body: CircleEdit) =>
    request<Design>(
      `/api/designs/${id}/circle-edit`,
      { method: "POST", body: JSON.stringify(body) },
      GENERATION_TIMEOUT_MS
    ),

  // Phase 4: localized visual-face edit. Regenerates real CAD (STL/STEP) from
  // the trusted spec; rejected/unsupported edits return 4xx with a reason and
  // leave the design unchanged.
  faceEdit: (id: string, body: FaceEditRequestBody) =>
    request<Design>(
      `/api/designs/${id}/face-edit`,
      { method: "POST", body: JSON.stringify(body) },
      GENERATION_TIMEOUT_MS
    ),

  health: () =>
    request<{ status: string; llm_provider?: string; dev_mode?: boolean }>("/health"),

  providerStatus: () => request<ProviderStatus>("/api/provider-status"),

  // Drawing-to-CAD Assist.
  interpretDrawing: async (
    file: File,
    hint?: string
  ): Promise<DrawingInterpretation> => {
    const fd = new FormData();
    fd.append("file", file);
    if (hint) fd.append("hint", hint);
    const token = getToken();
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/drawings/interpret`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
    } catch (e) {
      throw new ApiError(
        `Cannot reach the LunaiCAD backend at ${API_BASE} (POST /api/drawings/interpret). ` +
          `(${e instanceof Error ? e.message : String(e)})`,
        0
      );
    }
    if (!res.ok) throw new ApiError(`Interpretation failed (${res.status})`, res.status);
    return (await res.json()) as DrawingInterpretation;
  },
  confirmDrawing: (interp: DrawingInterpretation) =>
    request<Design>("/api/drawings/confirm", {
      method: "POST",
      body: JSON.stringify(interp),
    }),

  // Drawing job status snapshot (poll target for the async generate flow).
  getDrawingJob: (jobId: string) =>
    request<DrawingJobStatus>(`/api/drawings/jobs/${jobId}`),

  // ONE-SHOT drawing -> CAD as an async job: POST returns 202 + job_id, then
  // we poll until done/failed and resolve with the final payload — callers
  // keep the old Promise semantics, plus optional stage updates.
  generateFromDrawing: async (
    file: File,
    hint?: string,
    onStage?: (stage: DrawingJobStage, status: DrawingJobStatus) => void
  ): Promise<DrawingGenerateResult> => {
    const fd = new FormData();
    fd.append("file", file);
    if (hint) fd.append("hint", hint);
    const token = getToken();
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/drawings/generate`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
    } catch (e) {
      throw new ApiError(
        `Cannot reach the LunaiCAD backend at ${API_BASE} (POST /api/drawings/generate). ` +
          `(${e instanceof Error ? e.message : String(e)})`,
        0,
        "POST /api/drawings/generate"
      );
    }
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = (await res.json()) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(detail, res.status, "POST /api/drawings/generate");
    }
    const body = (await res.json()) as { job_id?: string } & DrawingGenerateResult;
    if (res.status !== 202 || !body.job_id) {
      return body as DrawingGenerateResult; // sync=true escape hatch
    }
    const finished = await pollDrawingJob(body.job_id, {
      fetchStatus: api.getDrawingJob,
      onUpdate: (s) => onStage?.(s.stage, s),
    });
    return finished.result as DrawingGenerateResult;
  },

  // Drawing → CAD for all supported file types (PNG/JPG/PDF/SVG/DXF) as an
  // async job: upload (with progress) → 202 + job_id → poll with stage
  // updates → resolve with the final payload.
  drawingToCad: async (
    file: File,
    opts?: {
      notes?: string;
      units?: "mm" | "inch";
      thicknessMm?: number;
      family?: string;
      onProgress?: (pct: number) => void;
      onStage?: (stage: DrawingJobStage, status: DrawingJobStatus) => void;
    }
  ): Promise<DrawingToCadResult> => {
    const fd = new FormData();
    fd.append("file", file);
    if (opts?.notes) fd.append("notes", opts.notes);
    if (opts?.units) fd.append("units", opts.units);
    if (opts?.thicknessMm) fd.append("thickness_mm", String(opts.thicknessMm));
    if (opts?.family) fd.append("family", opts.family);
    const token = getToken();
    // XMLHttpRequest so real upload progress can be reported for large files.
    const accepted = await new Promise<{ status: number; body: unknown }>(
      (resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_BASE}/api/drawings/to-cad`);
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && opts?.onProgress)
            opts.onProgress(Math.round((e.loaded / e.total) * 100));
        };
        xhr.onerror = () =>
          reject(
            new ApiError(
              `Cannot reach the LunaiCAD backend at ${API_BASE} (POST /api/drawings/to-cad).`,
              0,
              "POST /api/drawings/to-cad"
            )
          );
        xhr.onload = () => {
          let body: unknown = null;
          try {
            body = JSON.parse(xhr.responseText);
          } catch {
            /* non-JSON body */
          }
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve({ status: xhr.status, body });
          } else {
            const detail =
              (body as { detail?: string } | null)?.detail ??
              `${xhr.status} ${xhr.statusText}`;
            reject(new ApiError(detail, xhr.status, "POST /api/drawings/to-cad"));
          }
        };
        xhr.send(fd);
      }
    );
    const body = accepted.body as { job_id?: string } | null;
    if (accepted.status !== 202 || !body?.job_id) {
      return accepted.body as DrawingToCadResult; // sync=true escape hatch
    }
    const finished = await pollDrawingJob(body.job_id, {
      fetchStatus: api.getDrawingJob,
      onUpdate: (s) => opts?.onStage?.(s.stage, s),
    });
    return finished.result as DrawingToCadResult;
  },
};

export interface DrawingGenerateResult {
  generated: boolean;
  // Unified pipeline payload: `interpretation` mirrors the canonical
  // `analysis` block (legacy key kept for compatibility).
  interpretation: DrawingAnalysisSummary | null;
  analysis?: DrawingAnalysisSummary | null;
  design: Design | null;
  message?: string | null;
}

export interface DrawingAnalysisSummary {
  source: "vision" | "dxf" | "svg" | "pdf" | "hint";
  units: "mm" | "inch";
  drawing_type: string;
  title: string | null;
  detected_views: { view: string; description: string | null }[];
  scale_confidence: number;
  overall_dimensions: {
    width_mm: number | null;
    height_mm: number | null;
    depth_mm: number | null;
  };
  inferred_depth_mm: number | null;
  outer_profile: {
    kind: "rectangle" | "circle" | "polygon" | "unknown";
    width_mm: number | null;
    height_mm: number | null;
    diameter_mm: number | null;
    corner_radius_mm: number | null;
  };
  features: {
    through_holes: { diameter_mm: number; x_mm: number; y_mm: number; count: number }[];
    patterns: {
      kind: string;
      count: number;
      hole_diameter_mm: number;
      pitch_circle_diameter_mm: number | null;
    }[];
    slots: { width_mm: number; length_mm: number }[];
  };
  dimension_annotations: { text: string; value: number | null }[];
  assumptions: string[];
  ambiguities: string[];
  recommended_family: string | null;
  confidence_score: number;
  needs_clarification: boolean;
  clarification_questions: string[];
}

export interface DrawingToCadResult {
  generated: boolean;
  analysis: DrawingAnalysisSummary;
  design: Design | null;
  message: string | null;
}

export interface LocalizedEdit {
  selected_entity_type: "face" | "edge" | "hole" | "feature" | "body";
  selected_entity_id: string;
  allowed_operation: string;
  natural_language_instruction: string;
  validated_parameters?: Record<string, number>;
}

export interface CircleEdit {
  selected: { entity_type: string; entity_id: string; label?: string };
  operation?: string;
  instruction: string;
  validated_parameters?: Record<string, number>;
}

// Body for POST /api/designs/{id}/face-edit — the localized face-edit payload
// (the `design_id` in FaceEditPayload is dropped; it's the URL path param).
export interface FaceEditRequestBody {
  instruction: string;
  quick_action: string | null;
  selection: Record<string, unknown>;
}

export interface FeatureInfo {
  id: string;
  type: string;
  label: string;
  anchor: [number, number, number];
  meta?: Record<string, unknown>;
}

export interface DrawingInterpretation {
  title: string | null;
  units: string;
  suggested_object_type: string | null;
  detected_object_type: string | null;
  template_candidate: string | null;
  views: { view_type: string }[];
  overall_dimensions: Record<string, number>;
  holes: { diameter: number | null; count: number; callout: string | null }[];
  assumptions: { field: string; assumption: string }[];
  clarification_questions: { field: string; question: string }[];
  missing_critical_dimensions: string[];
  overall_confidence: number;
  drawing_units_confidence: number;
  view_detection_confidence: number;
  dimension_extraction_confidence: number;
  unsupported_reason: string | null;
  interpretation_rationale: string | null;
  provider_error: string | null;
  // Backend-computed gates (assumption-first drawing-to-CAD).
  actionable?: boolean;
  generate_with_assumptions_available?: boolean;
}

export const DRAWING_CONFIDENCE_THRESHOLD = 0.75;
// A recognized mechanical drawing at/above this confidence generates with
// assumptions even when clarification questions are open.
export const DRAWING_ASSUMPTIONS_THRESHOLD = 0.45;

export interface ProviderStatus {
  provider: string;
  image_understanding: boolean;
  drawing_to_cad_enabled: boolean;
  status_label: string;
  // Deployment detail — only returned when the backend runs with DEV_MODE on.
  // Absent in production, so these must be treated as optional.
  app_env?: string;
  model?: string;
  mock_allowed?: boolean;
  provider_error?: string | null;
}
