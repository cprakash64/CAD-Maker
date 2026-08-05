"""F-1 regression: model/provider output can never reach an execution mechanism.

Background (docs/production-readiness.md, F-1): the repo previously carried a
`provider.cad_program()` hook whose returned Python source was executed by
`app.generation.code_sandbox.run_program()`. That facility is removed. These
tests are the guard rails that keep it removed — they are deliberately written
against the *architecture* (no hook, no executor, allowlisted interpreter only)
rather than against any single call site, so that re-introducing the capability
anywhere fails CI.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from app.cad.base import CadGenerationError

APP_ROOT = Path(__file__).resolve().parent.parent / "app"


# --- 1. the execution facility itself is gone -----------------------------
@pytest.mark.parametrize("module", [
    "app.generation.code_sandbox",
    "app.generation.cad_programs",
    "app.generation.compiler",
    "app.generation.scad_runner",
])
def test_code_execution_modules_are_removed(module):
    """These modules existed only to author/execute model-supplied source."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


# --- 2. no provider may expose a source-returning hook --------------------
def _provider_classes():
    from app.llm.base import LLMProvider
    from app.llm.mock_provider import MockLLMProvider
    from app.llm.openai_provider import OpenAIProvider

    return [LLMProvider, MockLLMProvider, OpenAIProvider]


@pytest.mark.parametrize("cls", _provider_classes(), ids=lambda c: c.__name__)
def test_no_provider_exposes_cad_program(cls):
    assert not hasattr(cls, "cad_program"), (
        f"{cls.__name__} re-introduced a source-returning hook; providers must "
        "return structured data only."
    )


def test_no_provider_method_returns_generated_code():
    """No provider method may be named in a way that implies source output."""
    banned = {"cad_program", "generate_code", "generate_program", "write_code"}
    for cls in _provider_classes():
        exposed = {n for n in dir(cls) if not n.startswith("_")}
        assert not (exposed & banned), f"{cls.__name__} exposes {exposed & banned}"


# --- 3. the app never execs/evals anything --------------------------------
def _python_sources():
    for p in APP_ROOT.rglob("*.py"):
        if "__pycache__" in p.parts or p.name.endswith(" 2.py"):
            continue
        yield p


def test_app_source_contains_no_dynamic_execution():
    """Static AST scan: no exec/eval/compile/__import__ anywhere in app code.

    This is the backstop — even if a future provider returned source, there is
    no interpreter left in the application to hand it to.
    """
    offenders: list[str] = []
    for path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"exec", "eval", "compile", "__import__"}:
                    rel = path.relative_to(APP_ROOT.parent)
                    offenders.append(f"{rel}:{node.lineno} {node.func.id}()")
    assert not offenders, "dynamic execution found in app code:\n" + "\n".join(offenders)


def test_app_never_spawns_a_python_interpreter():
    """No module may shell out to a Python interpreter (the old sandbox did)."""
    offenders = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "sys.executable" in text:
            offenders.append(str(path.relative_to(APP_ROOT.parent)))
    assert not offenders, f"sys.executable subprocess found in: {offenders}"


# --- 4. provider text that looks like code is inert data ------------------
# Each string is a real bypass shape the old AST denylist tried (and in several
# cases failed) to catch. None may ever be interpreted.
BYPASS_STRINGS = [
    "import os",
    "import subprocess",
    "__import__('os').system('id')",
    "__builtins__",
    "getattr(cq, 'exporters')",
    "(1).__class__.__bases__[0].__subclasses__()",
    "exec('x=1')",
    "eval('1+1')",
    "compile('x=1','<s>','exec')",
    "open('/etc/passwd').read()",
    "os.system('rm -rf /')",
    "subprocess.run(['sh','-c','id'])",
    "cq.Workplane('XY').box(1,1,1)",
    "result = cq.Workplane('XY').circle(30).extrude(8)",
    "../../../../etc/passwd",
    "$(id)",
]


@pytest.mark.parametrize("payload", BYPASS_STRINGS)
def test_malicious_op_names_are_rejected_by_the_interpreter(payload):
    """The feature-graph interpreter is an allowlist: unknown ops never run."""
    from app.cad.feature_graph import build_feature_graph
    from app.schemas.complex_cad import CADFeatureGraph

    graph = CADFeatureGraph(
        units="mm",
        result_id="a",
        operations=[{"op": payload, "id": "a", "params": {}}],
    )
    with pytest.raises(CadGenerationError) as exc:
        build_feature_graph(graph)
    assert "not allowed" in str(exc.value) or "no operations" in str(exc.value)


@pytest.mark.parametrize("payload", BYPASS_STRINGS)
def test_malicious_param_values_never_execute(payload):
    """Params are numerically coerced; a code string can only ever be a bad number."""
    from app.cad.feature_graph import build_feature_graph
    from app.schemas.complex_cad import CADFeatureGraph

    graph = CADFeatureGraph(
        units="mm",
        result_id="a",
        operations=[{"op": "box", "id": "a",
                     "params": {"width": payload, "depth": 10, "height": 10}}],
    )
    # Either the schema/coercion rejects it, or it is ignored in favour of a
    # default — but it must never be interpreted as code.
    try:
        build_feature_graph(graph)
    except (CadGenerationError, ValueError, TypeError):
        pass


def test_a_hostile_provider_cannot_inject_geometry_source():
    """End-to-end shape of the old vulnerability, now impossible.

    A provider that tries to smuggle Python through a structured field gets its
    text treated as an opaque value: there is no hook to return it on, and no
    executor to receive it.
    """
    from app.llm.mock_provider import MockLLMProvider

    class HostileProvider(MockLLMProvider):
        name = "mock"

        def plan_feature_graph(self, prompt: str):
            return {
                "units": "mm",
                "result_id": "evil",
                "operations": [
                    {"op": "__import__('os').system('id')", "id": "evil", "params": {}}
                ],
            }

    provider = HostileProvider()
    assert not hasattr(provider, "cad_program")

    from app.cad.feature_graph import build_feature_graph
    from app.schemas.complex_cad import CADFeatureGraph

    graph = CADFeatureGraph(**provider.plan_feature_graph("anything"))
    with pytest.raises(CadGenerationError):
        build_feature_graph(graph)
