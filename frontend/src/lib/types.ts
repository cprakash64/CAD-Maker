export type HoleType = "simple" | "counterbore" | "countersink";

export interface Hole {
  diameter: number;
  x: number;
  y: number;
  hole_type: HoleType;
  screw_size?: string | null;
  counterbore_diameter?: number | null;
  counterbore_depth?: number | null;
  countersink_diameter?: number | null;
  countersink_angle?: number;
}

export interface DesignSpec {
  object_type: string;
  units: string;
  manufacturing_method: string;
  material: string;
  dimensions: Record<string, number>;
  holes: Hole[];
  fillet_radius?: number | null;
  chamfer_size?: number | null;
  notes?: string | null;
}

export interface PreviewMesh {
  positions: number[];
  indices: number[];
  vertex_count: number;
  triangle_count: number;
}

export interface Check {
  check: string;
  severity: "info" | "warning" | "error" | "critical";
  passed: boolean;
  message: string;
}

export interface ExportFile {
  fmt: string;
  url: string;
  size_bytes: number;
}

export interface Design {
  id: string;
  project_id: string;
  prompt: string;
  object_type: string | null;
  spec: DesignSpec | null;
  assumptions: string[];
  explanation: string | null;
  clarification_question: string | null;
  needs_clarification: boolean;
  preview: PreviewMesh | null;
  bounding_box_mm: Record<string, number> | null;
  spec_hash: string | null;
  exports: ExportFile[];
  checks: Check[];
  editable_parameters: Record<string, number>;
  provider: string | null;
  generation_ms: number | null;
  created_at: string | null;
  updated_at: string | null;
  my_feedback: Feedback | null;
  features: FeatureInfo[];
  default_assumptions: string[];
  can_generate_with_defaults: boolean;
  missing_required: string[];
  clarification_questions: string[];
  feature_graph_ops: string[];
  route: string | null;
  route_reason: string | null;
  auto_repaired: boolean;
  export_formats: string[];
  semantic_checks: SemanticCheck[];
  semantic_passed: boolean | null;
  repair_attempts: number;
  has_program: boolean;
  warnings: string[];
  feature_audit: FeatureAuditItem[];
  feature_audit_passed: boolean | null;
  // Requested-vs-generated dimension report + print readiness (may be null).
  dimension_report?: DimensionReport | null;
  print_readiness?: PrintReadiness | null;
  dimensions_within_tolerance?: boolean | null;
  // Overall validation severity + the specific critical failures / warnings.
  validation_status?: ValidationStatus | null;
  validation_critical_failures?: string[];
  validation_warnings?: string[];
  // Large-assembly gate: the prompt describes a whole machine / multi-subsystem
  // assembly and must be decomposed into single parts before generation.
  needs_decomposition?: boolean;
  decomposition?: Decomposition | null;
  // "single_part" | "assembly" (concept model).
  design_mode?: string | null;
}

export interface Decomposition {
  reason?: string;
  components?: string[];
  recommended_first?: string;
  examples?: string[];
}

export interface FeatureAuditItem {
  feature_id: string;
  requirement: string;
  forbidden: boolean;
  satisfied: boolean;
  detail: string;
}

export interface SemanticCheck {
  name: string;
  passed: boolean;
  expected: string | null;
  actual: string | null;
  severity: string;
}

export interface FeatureInfo {
  id: string;
  type: string;
  label: string;
  anchor: [number, number, number];
  meta?: Record<string, unknown>;
}

// --- Validation / print-readiness (additive backend fields) ---------------
// Everything optional: older designs and non-CadPlan routes may omit parts, and
// the UI must never assume a field exists.
export interface DimensionComparison {
  name: string;
  requested_mm?: number;
  measured_mm?: number;
  tolerance_mm?: number;
  delta_mm?: number;
  within?: boolean;
}

export interface PrintReadiness {
  printable?: boolean;
  watertight?: boolean;
  manifold?: boolean;
  single_body?: boolean;
  positive_volume?: boolean;
  min_hole_diameter_mm?: number | null;
  min_printable_hole_mm?: number | null;
  min_wall_checked?: boolean;
  issues?: string[];
}

export interface DimensionTolerance {
  unit?: string;
  length_tolerance_mm?: number;
  length_tolerance_frac?: number;
  diameter_tolerance_mm?: number;
  printer_min_feature_mm?: number;
  printer_min_hole_mm?: number;
  printer_xy_compensation_mm?: number;
}

export interface DimensionMeasured {
  bbox_mm?: Record<string, number>;
  volume_mm3?: number;
  surface_area_mm2?: number;
  hole_count?: number;
  through_hole_count?: number;
  triangles?: number;
  watertight?: boolean;
  manifold?: boolean;
  boundary_edges?: number;
  components?: number;
  through_holes_genus?: number;
  // Assembly mode:
  component_count?: number;
  tube_count?: number;
  mesh_components?: number;
  sections_present?: string[];
}

export interface AssemblyComponent {
  id: string;
  section: string;
  kind: string;
  type: string;
  anchor?: number[];
}

export type ValidationStatus = "pass" | "warning" | "critical_failure";

export interface ValidationSummary {
  status?: ValidationStatus;
  critical_failures?: string[];
  warnings?: string[];
}

export interface DimensionReport {
  unit?: string;
  tolerance?: DimensionTolerance;
  requested?: {
    bbox_mm?: Record<string, number> | null;
    hole_count?: number | null;
    through_hole_count?: number | null;
    dimensions_mm?: Record<string, number>;
  };
  measured?: DimensionMeasured;
  comparisons?: DimensionComparison[];
  within_tolerance?: boolean | null;
  print_readiness?: PrintReadiness;
  validation?: ValidationSummary;
  notes?: string[];
  // Assembly mode:
  design_mode?: string;
  components?: AssemblyComponent[];
  sections?: { present?: string[]; missing?: string[] };
}

export interface DesignSummary {
  id: string;
  project_id: string;
  prompt: string;
  object_type: string | null;
  created_at: string;
  updated_at: string | null;
  needs_clarification: boolean;
  export_ready: boolean;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: { id: string; email: string };
}

export interface Feedback {
  id: string;
  design_id: string;
  rating: "up" | "down";
  categories: string[];
  comment: string | null;
  created_at: string;
}

export const FEEDBACK_CATEGORIES: { value: string; label: string }[] = [
  { value: "wrong_template", label: "Wrong template" },
  { value: "wrong_dimensions", label: "Wrong dimensions" },
  { value: "bad_geometry", label: "Bad geometry" },
  { value: "export_failed", label: "Export failed" },
  { value: "confusing_explanation", label: "Confusing explanation" },
  { value: "missing_feature", label: "Missing feature" },
  { value: "other", label: "Other" },
];

export interface TemplateParam {
  name: string;
  label: string;
  default: number;
  min: number;
  max: number;
  unit: string;
  required: boolean;
}

export interface TemplateInfo {
  object_type: string;
  name: string;
  description: string;
  parameters: TemplateParam[];
}
