"""Reusable, composable assertions over an EvalContext.

Every function returns a list[AssertionResult] (never raises) so one bad
assertion can never abort the rest of a case's checks, and a "skip" (not
measurable for this case/pipeline) is always distinct from "pass" — a skip
must never silently count as a pass in the report.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.cad.tolerance import diameter_tolerance, length_tolerance, within
from eval.context import EvalContext

PASS, FAIL, SKIP = "pass", "fail", "skip"

_PATH_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def _get_path(obj, path: str):
    """Resolve a dotted/bracket path like 'holes[0].diameter' against a
    JSON-like dict/list structure. Raises KeyError/IndexError/TypeError on a
    missing path -- callers treat that as SKIP, not a silent None."""
    cur = obj
    for name, idx in _PATH_SEGMENT.findall(path):
        cur = cur[int(idx)] if idx else cur[name]
    return cur


@dataclass
class AssertionResult:
    name: str
    status: str
    detail: str = ""


def _r(name: str, ok: bool, detail: str = "") -> AssertionResult:
    return AssertionResult(name, PASS if ok else FAIL, detail)


# --------------------------------------------------------------------------
# classification / capability
# --------------------------------------------------------------------------

def assert_capability_classification(ctx: EvalContext) -> list[AssertionResult]:
    """Did the system land in the expected bucket: generated / clarified /
    refused / decomposed / rejected-input? This is the single most important
    check — everything else is conditional on getting this right first."""
    expected = ctx.case.expected_capability_classification
    resp = ctx.response or {}
    out = []

    if not ctx.ok:
        actual = "crashed"
    elif ctx.http_status is not None and ctx.http_status >= 400:
        actual = "rejected_input"
    elif resp.get("needs_decomposition"):
        actual = "needs_decomposition"
    # An "unsupported" route may ALSO populate needs_clarification/
    # clarification_question with a one-click-fallback offer (e.g. a nyloc
    # nut declining with "I can build a regular hex nut instead") -- that is
    # a graceful refusal, not a genuine "please tell me more" ask, so this
    # check must run BEFORE the clarification check.
    elif resp.get("route") == "unsupported" or resp.get("download_blocked_reason") or \
            resp.get("generation_outcome") in ("unsupported", "failed_safe"):
        actual = "refusal"
    # NOTE: the edit endpoints (/modify, /circle-edit, /localized-edit) set
    # `clarification_question` on a rejected edit WITHOUT touching
    # `needs_clarification` (that DTO field is only meaningful on the create
    # path) -- so both must be checked, or an edit-rejection is silently
    # misclassified as "generates" since the design already has exports.
    elif resp.get("needs_clarification") or resp.get("clarification_question"):
        actual = "clarification_required"
    elif resp.get("exports") or resp.get("id"):
        actual = "generates"
    else:
        actual = "unknown"

    out.append(_r(
        "capability_classification", actual == expected,
        f"expected {expected!r}, got {actual!r}"
        + (f" (error: {ctx.error})" if ctx.error else ""),
    ))
    return out


def assert_clarification_behavior(ctx: EvalContext) -> list[AssertionResult]:
    required = ctx.case.required_clarification_behavior
    if required is None:
        return []
    resp = ctx.response or {}
    asked = bool(resp.get("needs_clarification") or resp.get("clarification_question"))
    if required == "must_ask":
        return [_r("clarification_behavior", asked, "expected a clarification question")]
    if required == "must_not_ask":
        return [_r("clarification_behavior", not asked,
                    "expected NO clarification question (assumptions were allowed)")]
    return []


def assert_refusal(ctx: EvalContext) -> list[AssertionResult]:
    """A refusal must be a clean, safe refusal: no downloadable manufacturable
    file, no 500/traceback."""
    if ctx.case.expected_capability_classification != "refusal":
        return []
    resp = ctx.response or {}
    out = [_r("refusal_no_crash", ctx.ok, ctx.error or "")]
    exports = resp.get("exports") or []
    blocked = bool(resp.get("download_blocked_reason"))
    out.append(_r(
        "refusal_no_silent_export", (not exports) or blocked,
        "a refusal must not hand out an undocumented downloadable export",
    ))
    return out


def assert_unresolved_dimensions(ctx: EvalContext) -> list[AssertionResult]:
    """drawing_to_cad ground truth: does the response's drawing_fidelity
    correctly detect (or correctly clear) each critical-unresolved category
    (docs/drawing-to-cad-beta.md) -- the "unresolved-dimension detection"
    measurement the beta benchmark requires."""
    expected = ctx.case.expected_critical_unresolved
    if expected is None:
        return []
    resp = ctx.response or {}
    fidelity = resp.get("drawing_fidelity") or {}
    actual = fidelity.get("critical_unresolved") or []
    return [_r("unresolved_dimensions_detected", set(actual) == set(expected),
                f"expected critical_unresolved {sorted(expected)!r}, "
                f"got {sorted(actual)!r}")]


def assert_allowed_assumptions(ctx: EvalContext) -> list[AssertionResult]:
    """Every assumption the system states must be one the case explicitly
    allows — an unlisted assumption is a silent-guess regression."""
    allowed = ctx.case.allowed_assumptions
    if not allowed:
        return []
    resp = ctx.response or {}
    assumptions = resp.get("assumptions") or []
    bad = [a for a in assumptions if not any(tag.lower() in a.lower() for tag in allowed)]
    return [_r("allowed_assumptions_only", not bad,
                f"unlisted assumption(s): {bad}" if bad else "")]


def assert_capability_level(ctx: EvalContext) -> list[AssertionResult]:
    """Cross-checks the DTO's capability_level (docs/product-contract.md)
    against the case's expectation -- the ongoing regression form of
    tests/test_product_contract.py's 'capability labels match registry
    data' checks, run over real generated designs rather than the static
    registry alone."""
    expected = ctx.case.expected_capability_level
    if expected is None:
        return []
    actual = (ctx.response or {}).get("capability_level")
    expected_value = None if expected == "null" else expected
    return [_r("capability_level", actual == expected_value,
                f"expected {expected_value!r}, got {actual!r}")]


def assert_object_type(ctx: EvalContext) -> list[AssertionResult]:
    expected = ctx.case.expected_object_type
    if not expected:
        return []
    actual = (ctx.response or {}).get("object_type")
    return [_r("family_classification", actual in expected,
                f"expected one of {expected}, got {actual!r}")]


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def assert_units(ctx: EvalContext) -> list[AssertionResult]:
    """Every dimension the harness compares is mm; this asserts the design's
    own spec agrees, so a units mismatch can't silently pass a bbox check."""
    ga = ctx.case.geometric_assertions
    if "units" not in ga:
        return []
    spec = (ctx.response or {}).get("spec") or {}
    actual = spec.get("units")
    return [_r("units", actual is None or actual == ga["units"],
                f"expected units={ga['units']!r}, got {actual!r}")]


def assert_bounding_dimensions(ctx: EvalContext) -> list[AssertionResult]:
    expected = ctx.case.required_dimensions or ctx.case.geometric_assertions.get("bbox_mm")
    if not expected:
        return []
    reimport = ctx.reimport_step()
    if not reimport or not reimport.get("valid"):
        return [AssertionResult("bounding_dimensions", SKIP,
                                 "no valid re-imported solid to measure")]
    bbox = reimport["bbox_mm"]
    out = []
    for axis in ("x", "y", "z"):
        if axis not in expected:
            continue
        exp = float(expected[axis])
        act = float(bbox.get(axis, 0.0))
        tol = length_tolerance(exp)
        out.append(_r(f"bbox_{axis}", within(exp, act, tol),
                       f"expected {exp}±{tol:.2f}mm, measured {act}mm"))
    return out


def assert_volume_range(ctx: EvalContext) -> list[AssertionResult]:
    rng = ctx.case.geometric_assertions.get("volume_range_mm3")
    if not rng:
        return []
    reimport = ctx.reimport_step()
    if not reimport or not reimport.get("valid"):
        return [AssertionResult("volume_range", SKIP, "no valid re-imported solid")]
    v = reimport["volume_mm3"]
    lo, hi = rng
    return [_r("volume_range", lo <= v <= hi, f"expected [{lo}, {hi}] mm3, measured {v}")]


def assert_solid_validity(ctx: EvalContext) -> list[AssertionResult]:
    """The strongest single geometry check: re-import the exact bytes the user
    would download and confirm OCCT accepts it as a valid solid, and the
    exported mesh is watertight/manifold/single-body."""
    out = []
    reimport = ctx.reimport_step()
    if reimport is None:
        out.append(AssertionResult("solid_validity_step", SKIP, "no STEP bytes produced"))
    else:
        out.append(_r("solid_validity_step", reimport.get("valid", False),
                       reimport.get("error", "")))
    mesh = ctx.mesh_facts()
    if mesh is None:
        out.append(AssertionResult("solid_validity_mesh", SKIP, "no STL bytes produced"))
    else:
        problems = []
        if not mesh["watertight"]:
            problems.append("not watertight")
        if not mesh["manifold"]:
            problems.append("non-manifold")
        allow_multibody = ctx.case.geometric_assertions.get("allow_multibody", False)
        if mesh["components"] != 1 and not allow_multibody:
            problems.append(f"{mesh['components']} disconnected bodies")
        out.append(_r("solid_validity_mesh", not problems, "; ".join(problems)))
    return out


def _selectable_holes(ctx: EvalContext) -> list[dict]:
    return (ctx.response or {}).get("selectable_holes") or []


def assert_holes(ctx: EvalContext) -> list[AssertionResult]:
    """Number and diameter of holes, from the product's own selectable-holes
    metadata (the same data the face-edit UI relies on) — not a face count."""
    expected = ctx.case.geometric_assertions.get("holes")
    if expected is None:
        return []
    holes = _selectable_holes(ctx)
    out = []
    exp_count = expected.get("count")
    if exp_count is not None:
        if holes:
            out.append(_r("hole_count", len(holes) == exp_count,
                           f"expected {exp_count} holes, found {len(holes)}"))
        else:
            # Fall back to mesh genus (through-hole count) when selectable-hole
            # metadata isn't populated for this pipeline -- honestly marked as
            # a weaker signal, never silently treated as equivalent.
            mesh = ctx.mesh_facts()
            if mesh is not None:
                out.append(_r(
                    "hole_count_via_mesh_genus", mesh["through_holes_genus"] == exp_count,
                    f"[weaker signal: mesh genus] expected {exp_count}, "
                    f"measured {mesh['through_holes_genus']}"))
            else:
                out.append(AssertionResult("hole_count", SKIP,
                                            "no selectable-hole metadata or mesh available"))
    exp_diam = expected.get("diameter_mm")
    if exp_diam is not None:
        if not holes:
            out.append(AssertionResult("hole_diameter", SKIP,
                                        "no selectable-hole metadata to check diameter"))
        else:
            tol = diameter_tolerance(exp_diam)
            bad = [h["diameter_mm"] for h in holes
                   if not within(exp_diam, h["diameter_mm"], tol)]
            out.append(_r("hole_diameter", not bad,
                           f"expected Ø{exp_diam}±{tol:.2f}mm, off: {bad}" if bad else ""))
    return out


def assert_feature_positions(ctx: EvalContext) -> list[AssertionResult]:
    expected = ctx.case.geometric_assertions.get("feature_positions")
    if not expected:
        return []
    holes = _selectable_holes(ctx)
    if not holes:
        return [AssertionResult("feature_positions", SKIP, "no positioned features available")]
    centers = [h["center"][:2] for h in holes]  # x,y only
    out = []
    for i, (ex, ey) in enumerate(expected):
        tol = length_tolerance(max(abs(ex), abs(ey), 1.0))
        match = any(math.hypot(cx - ex, cy - ey) <= tol for cx, cy in centers)
        out.append(_r(f"feature_position_{i}", match,
                       f"expected a feature near ({ex}, {ey}) ±{tol:.2f}mm"))
    return out


def assert_symmetry(ctx: EvalContext) -> list[AssertionResult]:
    """Hole centers mirror across the requested axis within tolerance — catches
    a compositional plan that silently drops or offsets one side of a
    symmetric pattern."""
    axis = ctx.case.geometric_assertions.get("symmetry_axis")
    if not axis:
        return []
    holes = _selectable_holes(ctx)
    if len(holes) < 2:
        return [AssertionResult("symmetry", SKIP, "fewer than 2 holes to check symmetry over")]
    idx = {"x": 0, "y": 1}[axis]
    coords = sorted(h["center"][idx] for h in holes)
    tol = length_tolerance(max(abs(c) for c in coords) or 1.0)
    # Every coordinate c must have a mirrored partner -c (or itself, if ~0).
    unmatched = []
    remaining = list(coords)
    for c in coords:
        if c not in remaining:
            continue
        remaining.remove(c)
        if abs(c) <= tol:
            continue
        partner = -c
        match = min(remaining, key=lambda x: abs(x - partner), default=None)
        if match is not None and abs(match - partner) <= tol:
            remaining.remove(match)
        else:
            unmatched.append(c)
    return [_r("symmetry", not unmatched,
                f"unmirrored coordinate(s) on {axis}: {unmatched}" if unmatched else "")]


def assert_wall_thickness(ctx: EvalContext) -> list[AssertionResult]:
    """Measurable only when the design exposes the wall thickness as an
    editable parameter (enclosure-family templates); otherwise explicitly
    skipped rather than approximated and reported as a false pass."""
    expected = ctx.case.geometric_assertions.get("wall_thickness_mm")
    if expected is None:
        return []
    params = (ctx.response or {}).get("editable_parameters") or {}
    key = next((k for k in params if "wall_thickness" in k), None)
    if key is None:
        return [AssertionResult("wall_thickness", SKIP,
                                 "design has no editable wall_thickness parameter")]
    tol = length_tolerance(expected)
    actual = params[key]
    return [_r("wall_thickness", within(expected, actual, tol),
                f"expected {expected}±{tol:.2f}mm, got {actual}mm")]


def assert_expected_spec_values(ctx: EvalContext) -> list[AssertionResult]:
    """Proves an edit actually took effect: the exact value at a spec path
    after the edit. Complements assert_unchanged_except, which only proves
    everything ELSE did not change -- neither alone proves a correct edit."""
    expected = ctx.case.expected_spec_values
    if not expected:
        return []
    spec = (ctx.response or {}).get("spec") or {}
    out = []
    for path, exp_val in expected.items():
        try:
            actual = _get_path(spec, path)
        except (KeyError, IndexError, TypeError):
            out.append(AssertionResult(f"spec_value:{path}", SKIP, "path not found in response"))
            continue
        if isinstance(exp_val, (int, float)) and not isinstance(exp_val, bool):
            ok = math.isclose(float(actual), float(exp_val), abs_tol=1e-6)
        else:
            ok = actual == exp_val
        out.append(_r(f"spec_value:{path}", ok, f"expected {exp_val!r}, got {actual!r}"))
    return out


def assert_semantic_features(ctx: EvalContext) -> list[AssertionResult]:
    """Checks the design's own `features` list (each entry's `type` field --
    confirmed by direct inspection of design_service output, e.g.
    {"type": "boss", ...} -- NOT `kind`, which does not exist on this DTO
    field) plus `feature_graph_ops` for the feature-graph route."""
    expected = ctx.case.expected_semantic_features
    if not expected:
        return []
    resp = ctx.response or {}
    ops = set(resp.get("feature_graph_ops") or [])
    kinds = set()
    for f in (resp.get("features") or []):
        if isinstance(f, dict) and f.get("type"):
            kinds.add(f["type"])
    present = ops | kinds
    missing = [f for f in expected if f not in present]
    return [_r("semantic_features", not missing,
                f"missing: {missing}" if missing else "")]


# --------------------------------------------------------------------------
# export integrity
# --------------------------------------------------------------------------

def assert_export_and_reimport(ctx: EvalContext) -> list[AssertionResult]:
    expect = ctx.case.export_expectations
    if not expect:
        return []
    out = []
    formats = expect.get("formats", [])
    resp = ctx.response or {}
    have = {e["fmt"] for e in (resp.get("exports") or [])}
    for fmt in formats:
        out.append(_r(f"export_present_{fmt}", fmt in have, f"missing export format {fmt!r}"))
    if expect.get("require_reimport", True) and "step" in formats:
        reimport = ctx.reimport_step()
        out.append(_r("step_reimport_valid",
                       bool(reimport and reimport.get("valid")),
                       (reimport or {}).get("error", "no STEP bytes to re-import")))
    if expect.get("require_reimport", True) and "stl" in formats:
        mesh = ctx.mesh_facts()
        out.append(_r("stl_reimport_readable", mesh is not None,
                       "" if mesh else "STL bytes missing or unreadable"))
    if expect.get("no_partial_file", True):
        # A "partial" export is one that exists in the exports list but is
        # zero bytes -- the exporter's own empty-file guard should make this
        # impossible, but the eval harness checks the DELIVERED artifact
        # independently rather than trusting that guarantee blindly.
        empty = [e["fmt"] for e in (resp.get("exports") or []) if not e.get("size_bytes")]
        out.append(_r("no_partial_export", not empty,
                       f"zero-byte export(s): {empty}" if empty else ""))
    return out


# --------------------------------------------------------------------------
# modification (unchanged-geometry-except-edit)
# --------------------------------------------------------------------------

def assert_unchanged_except(ctx: EvalContext) -> list[AssertionResult]:
    """The core "localized edit" invariant: everything about the part must be
    identical before/after EXCEPT the fields the case explicitly names as
    intentionally changed."""
    if ctx.before_response is None:
        return []
    before_spec = (ctx.before_response or {}).get("spec") or {}
    after_spec = (ctx.response or {}).get("spec") or {}
    if not before_spec or not after_spec:
        return [AssertionResult("unchanged_geometry", SKIP, "no spec on before/after response")]
    changed_allowed = set(ctx.case.allowed_changed_paths)
    out = []

    def _walk(prefix: str, b: object, a: object):
        if prefix in changed_allowed:
            return
        if isinstance(b, dict) and isinstance(a, dict):
            for k in set(b) | set(a):
                _walk(f"{prefix}.{k}" if prefix else k, b.get(k), a.get(k))
        elif isinstance(b, list) and isinstance(a, list):
            if len(b) != len(a):
                out.append(AssertionResult(
                    f"unchanged:{prefix}", FAIL,
                    f"list length changed: {len(b)} -> {len(a)}"))
                return
            for i, (bi, ai) in enumerate(zip(b, a)):
                _walk(f"{prefix}[{i}]", bi, ai)
        else:
            if isinstance(b, float) or isinstance(a, float):
                same = math.isclose(float(b or 0), float(a or 0), abs_tol=1e-6)
            else:
                same = b == a
            if not same:
                out.append(AssertionResult(f"unchanged:{prefix}", FAIL,
                                            f"{b!r} -> {a!r}"))

    _walk("", before_spec, after_spec)
    if not out:
        out.append(AssertionResult("unchanged_geometry", PASS, ""))
    return out


ALL_ASSERTION_FNS = [
    assert_capability_classification,
    assert_clarification_behavior,
    assert_refusal,
    assert_unresolved_dimensions,
    assert_allowed_assumptions,
    assert_object_type,
    assert_capability_level,
    assert_units,
    assert_bounding_dimensions,
    assert_volume_range,
    assert_solid_validity,
    assert_holes,
    assert_feature_positions,
    assert_symmetry,
    assert_wall_thickness,
    assert_semantic_features,
    assert_export_and_reimport,
    assert_unchanged_except,
    assert_expected_spec_values,
]


def run_all(ctx: EvalContext) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    for fn in ALL_ASSERTION_FNS:
        results.extend(fn(ctx))
    return results
