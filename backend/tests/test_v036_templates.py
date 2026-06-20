"""v0.3.6 new templates + trusted feature-graph interpreter."""
import pytest

from app.cad.base import CadGenerationError
from app.cad.feature_graph import build_feature_graph
from app.export.exporter import generate
from app.schemas.complex_cad import CADFeatureGraph
from app.schemas.design_spec import DesignSpec


def test_flanged_pipe_branch_exports():
    gen = generate(DesignSpec(object_type="flanged_pipe_branch", dimensions={}))
    assert len(gen.stl_bytes) > 0 and gen.step_bytes[:5] == b"ISO-1"
    assert gen.preview.triangle_count > 0


def test_gear_and_pulley_export():
    g = generate(DesignSpec(object_type="simple_gear_or_pulley", dimensions={"tooth_count": 20}))
    assert len(g.stl_bytes) > 0
    p = generate(DesignSpec(object_type="simple_gear_or_pulley", dimensions={}))  # pulley
    assert len(p.stl_bytes) > 0


def test_feature_graph_builds_and_exports():
    graph = CADFeatureGraph(
        operations=[
            {"op": "box", "id": "b", "params": {"width": 40, "depth": 40, "height": 10}},
            {"op": "cylinder", "id": "c", "params": {"radius": 8, "height": 24}, "at": (0, 0, 5)},
            {"op": "boolean_union", "id": "u", "target": "b", "tool": "c"},
            {"op": "cut_hole", "id": "h", "target": "u", "params": {"radius": 3, "depth": 40}, "at": (0, 0, -5)},
        ],
        result_id="h",
    )
    solid = build_feature_graph(graph)
    verts, tris = solid.val().tessellate(0.3)
    assert len(tris) > 0


def test_feature_graph_rejects_unknown_op():
    with pytest.raises(CadGenerationError):
        build_feature_graph(CADFeatureGraph(operations=[{"op": "exec_python", "id": "x"}]))


def test_feature_graph_rejects_out_of_range_param():
    with pytest.raises(CadGenerationError):
        build_feature_graph(
            CADFeatureGraph(operations=[
                {"op": "box", "id": "b", "params": {"width": 999999, "depth": 10, "height": 10}}
            ], result_id="b")
        )


def test_flanged_pipe_branch_routes_from_prompt():
    from app.parsing.prompt_parser import parse_prompt
    r = parse_prompt("flanged pipe branch, 90mm main pipe, 8 bolts per flange")
    assert r.spec is not None
    assert r.spec.object_type == "flanged_pipe_branch"


def test_gear_routes_from_prompt():
    from app.parsing.prompt_parser import parse_prompt
    r = parse_prompt("a 24 teeth spur gear, 80mm diameter")
    assert r.spec is not None and r.spec.object_type == "simple_gear_or_pulley"
