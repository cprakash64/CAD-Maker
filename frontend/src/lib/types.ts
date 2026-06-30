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
  // Clean, family-accurate display name ("Spur gear", "Pulley", "Hex standoff",
  // "Flange"). Prefer this over object_type for the part title.
  title: string | null;
  spec: DesignSpec | null;
  assumptions: string[];
  explanation: string | null;
  clarification_question: string | null;
  // Ready-to-run suggested parts for a too-vague prompt (clickable in the UI).
  clarification_options: ClarificationOption[];
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
  // Expectation-control presentation copy (badge/export/holes/beta notice).
  presentation?: Presentation | null;
  // Recognized standard / catalog part (e.g. a hex nut). Present only when the
  // prompt mapped to a standard; carries the badge + resolved assumptions.
  standard_part?: StandardPart | null;
  // Part Family Contract (honesty): what was requested vs resolved, and whether
  // the build is exact / partial / substituted / unsupported. Always present.
  part_family_contract?: PartFamilyContract | null;
  // Family-specific inspector detail (thread mode/length, set-screw fields, GT2).
  part_family_detail?: PartFamilyDetail | null;
  // Device-enclosure validation (board enclosures): board preset, posts, cutouts.
  device_enclosure_validation?: DeviceEnclosureValidation | null;
  // Object Intelligence: detected object, source type, confidence, dimensions used.
  object_intelligence?: ObjectIntelligence | null;
}

export interface ObjectIntelligence {
  object_detected: string;
  normalized_name?: string;
  category?: string;
  manufacturer?: string | null;
  model?: string | null;
  source_type: string;
  source_urls?: string[];
  confidence_score?: number;
  dimensions_used?: Record<string, number> | null;
  standards?: string[];
  assumptions?: string[];
  missing_or_assumed?: string[];
  generated_family?: string;
  validation_requirements?: string[];
  match_status?: string;
  status?: string;
  why?: string;
  cached?: boolean;
  feature_contract?: FeatureContract | null;
}

export interface FeatureContract {
  requested_features: string[];
  generated_features: string[];
  missing_features: string[];
  approximate_features?: string[];
  unsupported_features?: string[];
  pass_blocking_missing_features: string[];
}

export interface DeviceEnclosureValidation {
  board_preset: string;
  board_outline_present: boolean;
  mounting_posts_count: number;
  mounting_posts_aligned: boolean;
  required_port_cutouts_present: boolean;
  usb_c_cutout_present: boolean;
  micro_hdmi_cutout_count: number;
  usb_ethernet_cutout_present: boolean;
  gpio_access_present: boolean;
  assumption_hidden_gpio?: boolean;
  microsd_access_present: boolean;
  ventilation_present: boolean;
  wall_thickness_mm: number;
  lid_type: string;
  logo_feature_status: string;
  // Through-hole verification: every required port opens fully to the cavity.
  all_required_ports_open?: boolean;
  blocked_ports?: string[];
  port_openings?: {
    name: string;
    side: string;
    kind: string;
    required: boolean;
    open: boolean;
    residual_mm3: number;
  }[];
}

// Family-specific inspector fields (only the keys relevant to the family are set).
export interface PartFamilyDetail {
  family: string;
  // bolts / threaded rods
  thread?: string;
  thread_label?: string;
  pitch_mm?: number;
  thread_major_diameter_mm?: number;
  thread_representation?: string;
  external_thread_modeled?: boolean;
  threaded_length_mm?: number;
  length_mm?: number;
  shank_length_mm?: number;
  head_type?: string;
  head_across_flats_mm?: number;
  head_height_mm?: number;
  fit_warning?: string | null;
  // shaft couplers
  outer_diameter_mm?: number;
  bore_1_mm?: number;
  bore_2_mm?: number;
  axial_bores?: number;
  set_screw_count?: number;
  radial_set_screw_holes?: number;
  set_screw_thread?: string;
  set_screw_thread_mode?: string;
  set_screw_pitch_mm?: number;
  set_screw_tap_drill_mm?: number | null;
  set_screw_hole_mode?: string;
  placement_strategy?: string;
  threaded_holes?: number;
  // device enclosure (Raspberry Pi)
  device?: string;
  device_name?: string;
  board_preset_source?: string;
  mounting_posts?: number;
  port_cutouts?: string[];
  micro_hdmi_count?: number;
  ports_through_hole_verified?: boolean;
  blocked_ports?: string[];
  lid_type?: string;
  wall_thickness_mm?: number;
  logo_feature_status?: string;
  match_status?: string;
  // GT2 timing pulleys
  teeth?: number;
  pitch_diameter_mm?: number;
  belt_width_mm?: number;
  bore_mm?: number;
  has_flanges?: boolean;
  not_spur_gear?: boolean;
}

// Honesty contract: guarantees the UI never implies a substituted part is exact.
export interface PartFamilyContract {
  requested_family: string | null;
  resolved_family: string | null;
  requested_variant: string | null;
  resolved_variant: string | null;
  standard_part: boolean;
  standard: string | null;
  unsupported_features: string[];
  substituted_features: string[];
  missing_inputs: string[];
  // "exact" | "partial" | "substituted" | "unsupported"
  generation_honesty_status: string;
  reason: string | null;
}

// A recognized standard / catalog part resolved deterministically from a
// published dimensional standard (ISO/DIN), not from user-supplied dimensions.
export interface StandardPart {
  standard_part: boolean;
  family: string; // "hex_nut"
  standard: string; // "ISO 4032"
  standard_assumed: boolean;
  thread: string; // "M12"
  pitch_mm: number;
  across_flats_mm?: number;
  across_corners_mm?: number;
  height_mm?: number;
  bore_diameter_mm?: number;
  minor_diameter_mm?: number;
  thread_depth_mm?: number | null;
  internal_thread_modeled?: boolean;
  // "modeled" | "cosmetic" | "failed_to_model_fallback_cosmetic"
  thread_representation?: string;
  badge: string; // "Standard part · ISO 4032 · M12 × 1.75 · Modeled thread"
  assumed_message: string;
  hex_six_sided?: boolean | null;
  measured_corner_count?: number | null;
}

export interface ClarificationOption {
  label: string;
  prompt: string;
}

export interface ParametricHole {
  label: string;
  diameter_mm: number;
  through: boolean;
}

// Expectation-control copy (single source of truth, computed by the backend).
export interface Presentation {
  status_badge: string | null;
  status_detail: string | null;
  status_tone: "pass" | "review" | "fail" | null;
  is_concept: boolean;
  concept_notice: string | null;
  export_kind: "concept" | "validated";
  export_labels: { stl: string; step: string; package: string };
  export_notice: string | null;
  parametric_holes: ParametricHole[];
  manual_hole_editing: boolean;
  beta_notice: string;
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
  threaded_hole_count?: number;
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
  title: string | null;
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
