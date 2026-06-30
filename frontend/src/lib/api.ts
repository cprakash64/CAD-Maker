import type {
  AuthResponse,
  Design,
  DesignSummary,
  Feedback,
  Hole,
  TemplateInfo,
} from "./types";

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
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
    });
  } catch (e) {
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
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, res.status, `${init?.method ?? "GET"} ${path}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export class ApiError extends Error {
  status: number; // 0 = network-level failure (backend unreachable)
  endpoint?: string; // "POST /api/drawings/generate"
  constructor(message: string, status: number, endpoint?: string) {
    super(message);
    this.status = status;
    this.endpoint = endpoint;
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

  // Designs
  createDesign: (prompt: string) =>
    request<Design>("/api/designs/create", {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
  getDesign: (id: string) => request<Design>(`/api/designs/${id}`),
  listDesigns: () => request<DesignSummary[]>("/api/designs"),
  regenerate: (
    id: string,
    dimensions: Record<string, number>,
    holes?: Hole[]
  ) =>
    request<Design>(`/api/designs/${id}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ dimensions, holes }),
    }),
  modify: (id: string, prompt: string) =>
    request<Design>(`/api/designs/${id}/modify`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
  exportDesign: (id: string) =>
    request<Design>(`/api/designs/${id}/export`, { method: "POST" }),
  generateWithDefaults: (id: string) =>
    request<Design>(`/api/designs/${id}/generate-with-defaults`, { method: "POST" }),
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
    request<Design>(`/api/designs/${id}/localized-edit`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // Circle-to-edit: apply an edit to a feature resolved from a circle selection.
  circleEdit: (id: string, body: CircleEdit) =>
    request<Design>(`/api/designs/${id}/circle-edit`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

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

  // ONE-SHOT drawing -> CAD: interpret + auto-generate in a single call.
  generateFromDrawing: async (
    file: File,
    hint?: string
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
    return (await res.json()) as DrawingGenerateResult;
  },
};

export interface DrawingGenerateResult {
  generated: boolean;
  interpretation: DrawingInterpretation;
  design: Design | null;
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
  app_env: string;
  image_understanding: boolean;
  drawing_to_cad_enabled: boolean;
  mock_allowed: boolean;
  status_label: string;
}
