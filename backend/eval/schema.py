"""The evaluation record schema.

One dataclass (`EvalCase`) is the single source of truth for what a case
means; `eval/README.md` documents it in prose. Every fixture file under
`eval/fixtures/*.json` is a JSON list of objects matching this schema,
validated by `load_cases()` at load time — a malformed fixture fails the run
loudly instead of silently evaluating a wrong/partial case.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# --- closed vocabularies (a typo here must fail loudly, not silently pass) --
WORKFLOWS = {
    "text_to_cad", "drawing_to_cad", "modification", "export_integrity",
    "security",
}
COMPLEXITIES = {"simple", "moderate", "complex"}
CAPABILITY_CLASSIFICATIONS = {
    "generates",              # should produce a downloadable model
    "clarification_required", # should ask, not guess or refuse
    "refusal",                # should refuse / mark unsupported, no export
    "needs_decomposition",    # whole-assembly prompt -> named sub-components
    "rejected_input",         # malformed/invalid input -> 4xx, not a 500
}
CLARIFICATION_BEHAVIORS = {"must_ask", "must_not_ask", None}
SAFETY_CLASSIFICATIONS = {"safe", "boundary", "adversarial"}
DATA_SPLITS = {"development", "regression", "held_out"}
SUITES = {
    "known_families", "compositional_cadplan", "ambiguous_prompts",
    "unsupported_prompts", "modifications", "drawing_to_cad",
    "export_integrity", "security_regressions",
}


@dataclass
class EvalCase:
    # --- identity -----------------------------------------------------
    case_id: str                      # stable, unique across all suites
    suite: str                        # one of SUITES
    workflow: str                     # one of WORKFLOWS
    complexity: str                   # one of COMPLEXITIES
    source: str                       # provenance: which test/dataset this
                                       # was derived from, or "new"
    data_split: str                   # development | regression | held_out

    # --- input ----------------------------------------------------------
    prompt: Optional[str] = None                 # text_to_cad / modification
    fixture: Optional[str] = None                # drawing_to_cad file, relative
                                                  # to backend/tests/data/
    fixture_media_type: Optional[str] = None
    # Extra multipart form fields sent alongside the fixture (e.g.
    # {"thickness_mm": 6}) -- mirrors the frontend's "more options" override,
    # used to prove the resolution path for a drawing with a disclosed
    # missing dimension (docs/drawing-to-cad-beta.md).
    fixture_form: dict[str, Any] = field(default_factory=dict)
    setup_prompt: Optional[str] = None           # modification: base design
    edit_instruction: Optional[str] = None       # modification: the edit
    edit_kind: Optional[str] = None              # "modify" | "circle_edit" |
                                                  # "localized_edit" | "face_edit"
    edit_selection: Optional[dict] = None        # for circle/face edit APIs
    pytest_node_id: Optional[str] = None         # security suite only
    requires_live: bool = False                  # needs real OpenAI (e.g. vision);
                                                  # skipped offline, run only with --live

    # --- expectations -----------------------------------------------------
    expected_capability_classification: str = "generates"
    expected_object_type: Optional[list[str]] = None   # any-of match
    # One of app.cad.families.Maturity's four values, or "null" (the string)
    # for a design deliberately governed by the part_family honesty layer
    # instead of the family registry (docs/product-contract.md "Two honesty
    # layers"). None (the Python default) means "not asserted".
    expected_capability_level: Optional[str] = None
    required_dimensions: dict[str, float] = field(default_factory=dict)
    allowed_assumptions: list[str] = field(default_factory=list)
    required_clarification_behavior: Optional[str] = None
    expected_semantic_features: list[str] = field(default_factory=list)
    geometric_assertions: dict[str, Any] = field(default_factory=dict)
    export_expectations: dict[str, Any] = field(default_factory=dict)
    # drawing_to_cad only: the exact set of critical-unresolved categories
    # (docs/drawing-to-cad-beta.md, e.g. "depth") the response's
    # drawing_fidelity.critical_unresolved must match. None = not asserted;
    # [] asserts the drawing left nothing critical unresolved.
    expected_critical_unresolved: Optional[list[str]] = None
    # Dotted/bracket spec paths that are PERMITTED to change after a
    # modification (e.g. "dimensions.width", "holes[0].diameter"). Every
    # OTHER path must be byte-identical before/after -- this is the
    # "unchanged geometry except the edit" invariant. Modification workflow
    # only.
    allowed_changed_paths: list[str] = field(default_factory=list)
    # Dotted/bracket paths into the AFTER response's `spec`, e.g.
    # {"holes[0].diameter": 8.0} -- proves an edit actually took effect,
    # complementing allowed_changed_paths (which only proves everything ELSE
    # did NOT change).
    expected_spec_values: dict[str, Any] = field(default_factory=dict)

    # --- classification ---------------------------------------------------
    safety_classification: str = "safe"
    notes: str = ""

    def validate(self) -> list[str]:
        """Return a list of schema-violation strings (empty = valid)."""
        errs = []
        if self.suite not in SUITES:
            errs.append(f"unknown suite {self.suite!r}")
        if self.workflow not in WORKFLOWS:
            errs.append(f"unknown workflow {self.workflow!r}")
        if self.complexity not in COMPLEXITIES:
            errs.append(f"unknown complexity {self.complexity!r}")
        if self.expected_capability_classification not in CAPABILITY_CLASSIFICATIONS:
            errs.append(
                f"unknown expected_capability_classification "
                f"{self.expected_capability_classification!r}")
        if self.required_clarification_behavior not in CLARIFICATION_BEHAVIORS:
            errs.append(
                f"unknown required_clarification_behavior "
                f"{self.required_clarification_behavior!r}")
        if self.safety_classification not in SAFETY_CLASSIFICATIONS:
            errs.append(f"unknown safety_classification {self.safety_classification!r}")
        if self.data_split not in DATA_SPLITS:
            errs.append(f"unknown data_split {self.data_split!r}")
        if self.workflow == "text_to_cad" and not self.prompt:
            errs.append("text_to_cad case has no prompt")
        if self.workflow == "drawing_to_cad" and not self.fixture:
            errs.append("drawing_to_cad case has no fixture")
        if self.workflow == "modification" and not (self.setup_prompt and self.edit_instruction):
            errs.append("modification case needs setup_prompt and edit_instruction")
        if self.workflow == "security" and not self.pytest_node_id:
            errs.append("security case has no pytest_node_id")
        return errs


def load_cases(path: Path) -> list[EvalCase]:
    """Load one fixture file (a JSON list of case dicts) and validate every
    entry. Raises ValueError naming the file/case/problem on the first
    invalid record — a broken fixture must fail the run, not degrade it."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path}: fixture must be a JSON list of case objects")
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(raw):
        try:
            case = EvalCase(**entry)
        except TypeError as exc:
            raise ValueError(f"{path}: entry {i} has an invalid/unknown field: {exc}") from exc
        errs = case.validate()
        if errs:
            raise ValueError(f"{path}: case {case.case_id!r} (entry {i}): {'; '.join(errs)}")
        if case.case_id in seen_ids:
            raise ValueError(f"{path}: duplicate case_id {case.case_id!r}")
        seen_ids.add(case.case_id)
        cases.append(case)
    return cases


def load_all_cases(fixtures_dir: Path = FIXTURES_DIR) -> list[EvalCase]:
    """Load every fixture file in the directory, validating case_id uniqueness
    GLOBALLY (not just per-file) since case ids must be stable and unique
    across the whole harness."""
    all_cases: list[EvalCase] = []
    seen: dict[str, Path] = {}
    for path in sorted(fixtures_dir.glob("*.json")):
        for case in load_cases(path):
            if case.case_id in seen:
                raise ValueError(
                    f"duplicate case_id {case.case_id!r} in {path} "
                    f"(already defined in {seen[case.case_id]})")
            seen[case.case_id] = path
            all_cases.append(case)
    return all_cases
