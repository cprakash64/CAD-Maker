"""Proves the eval harness actually catches injected regressions, not just
that it runs. Three kinds, matching the acceptance criterion directly:
dimension, feature/hole-count, and authorization.

The authorization case cannot use eval.executors.run_security's real
subprocess.run (a monkeypatch in this test process would not reach a
separate pytest subprocess) -- it instead runs the SAME target test
in-process via pytest.main(), which shares process state with monkeypatch.
This proves the identical thing the security suite's pytest_node_id wrapping
depends on: that the underlying test detects the regression.
"""
from __future__ import annotations

from eval.assertions import FAIL, PASS, assert_bounding_dimensions, assert_holes
from eval.context import EvalContext
from eval.schema import EvalCase


def _case(**overrides) -> EvalCase:
    base = dict(
        case_id="regress_probe", suite="known_families", workflow="text_to_cad",
        complexity="simple", source="test", data_split="development",
        prompt="x",
    )
    base.update(overrides)
    return EvalCase(**base)


class _FakeShape:
    def __init__(self, x, y, z):
        self._x, self._y, self._z = x, y, z

    def isValid(self):
        return True


def test_dimension_regression_is_detected(monkeypatch):
    """If the built geometry's bbox drifted from what was requested, the
    bounding-dimensions assertion must FAIL, not silently pass."""
    case = _case(required_dimensions={"x": 80, "y": 40, "z": 5})
    ctx = EvalContext(case=case, ok=True, response={"spec": {"units": "mm"}},
                      step_bytes=b"fake")
    # Simulate a regression: the "generated" part measures 999mm wide instead
    # of the requested 80mm.
    monkeypatch.setattr(ctx, "reimport_step",
                        lambda: {"valid": True, "bbox_mm": {"x": 999.0, "y": 40.0, "z": 5.0},
                                "volume_mm3": 1000.0})
    results = assert_bounding_dimensions(ctx)
    by_name = {r.name: r for r in results}
    assert by_name["bbox_x"].status == FAIL
    assert by_name["bbox_y"].status == PASS
    assert by_name["bbox_z"].status == PASS
    assert "999" in by_name["bbox_x"].detail


def test_feature_hole_count_regression_is_detected():
    """If a hole silently disappeared from the generated part, the hole-count
    assertion must FAIL, not silently pass."""
    case = _case(geometric_assertions={"holes": {"count": 4, "diameter_mm": 6.6}})
    # Regression: only 3 of the requested 4 holes are present.
    holes = [{"diameter_mm": 6.6, "center": [x, 0, 0]} for x in (-30, -10, 10)]
    ctx = EvalContext(case=case, ok=True, response={"selectable_holes": holes})
    results = assert_holes(ctx)
    by_name = {r.name: r for r in results}
    assert by_name["hole_count"].status == FAIL
    assert "4" in by_name["hole_count"].detail and "3" in by_name["hole_count"].detail
    assert by_name["hole_diameter"].status == PASS  # the remaining holes are still correct


def test_authorization_regression_is_detected(monkeypatch):
    """Reverting the query-boundary ownership fix (hardening phase 3) back to
    a fetch-by-id-only lookup must make the cross-user authorization test
    fail. This is the same test the security suite's
    sec_cross_user_design_access_001 case wraps."""
    import pytest

    from app.database import SessionLocal
    from app.models import Design
    from app.services import design_service

    def _broken_get_owned_design(db, design_id, user_id):
        # The pre-hardening bug: return the row regardless of ownership.
        return db.get(Design, design_id)

    monkeypatch.setattr(design_service, "get_owned_design", _broken_get_owned_design)

    exit_code = pytest.main([
        "-q", "--no-header", "-p", "no:cacheprovider",
        "tests/test_phase2_authorization.py::test_second_user_cannot_touch_another_users_design",
    ])
    assert exit_code != 0, (
        "expected the cross-user authorization test to FAIL once ownership "
        "enforcement is reverted -- the eval harness's security suite must "
        "be able to detect exactly this class of regression"
    )
