"""DesignSpec validation: reject impossible/negative dimensions and bad holes."""
import pytest
from pydantic import ValidationError

from app.schemas.design_spec import DesignSpec, Hole


def test_negative_dimension_rejected():
    with pytest.raises(ValidationError):
        DesignSpec(object_type="rectangular_bracket", dimensions={"width": -10})


def test_zero_dimension_rejected():
    with pytest.raises(ValidationError):
        DesignSpec(object_type="spacer", dimensions={"outer_diameter": 0})


def test_absurdly_large_dimension_rejected():
    with pytest.raises(ValidationError):
        DesignSpec(object_type="rectangular_bracket", dimensions={"width": 99999})


def test_negative_hole_diameter_rejected():
    with pytest.raises(ValidationError):
        Hole(diameter=-2, x=0, y=0)


def test_counterbore_must_exceed_hole():
    with pytest.raises(ValidationError):
        Hole(diameter=6, x=0, y=0, counterbore_diameter=5, counterbore_depth=2)


def test_counterbore_requires_depth():
    with pytest.raises(ValidationError):
        Hole(diameter=6, x=0, y=0, counterbore_diameter=10)


def test_unit_conversion_to_mm():
    spec = DesignSpec(object_type="spacer", units="inch", dimensions={"length": 1})
    assert abs(spec.to_mm(1) - 25.4) < 1e-9


def test_invalid_object_type_rejected():
    with pytest.raises(ValidationError):
        DesignSpec(object_type="spaceship", dimensions={})
