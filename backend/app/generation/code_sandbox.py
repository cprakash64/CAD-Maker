"""Locked sandbox for generated CadQuery code.

SAFETY MODEL
- Every program is AST-linted first: no imports (the harness supplies `cq`/`math`),
  no open/exec/eval/compile/__import__/getattr/setattr, no os/sys/subprocess/socket
  attribute access, no dunder names, bounded loops.
- UNTRUSTED code (from an LLM) always runs in a subprocess: temp working dir,
  timeout, captured stdout/stderr, output files confined to the temp dir, export
  size cap. The subprocess is the production path.
- TRUSTED code (our own deterministic generators) may run via a restricted
  in-process exec (linted, minimal builtins, only {cq, math}) for CI speed. LLM
  code is NEVER run in-process.

The program assigns `result` (a CadQuery Workplane/solid) and `meta` (a dict).
The trusted harness performs the STL/STEP export and metadata write.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from app.cad.base import CadGenerationError

_MAX_EXPORT_BYTES = 30 * 1024 * 1024
_TIMEOUT_S = 25
_MAX_LOOPS = 50

_ALLOWED_IMPORTS = {"cadquery", "math"}
_FORBIDDEN_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "input", "breakpoint", "memoryview",
}
_FORBIDDEN_ATTR_ROOTS = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "builtins",
    "importlib", "ctypes", "requests", "urllib", "io", "pickle", "marshal",
}


def lint_code(code: str) -> None:
    """Static AST lint. Raises CadGenerationError on anything unsafe."""
    if len(code) > 20000:
        raise CadGenerationError("program too large")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CadGenerationError(f"program does not parse: {exc}") from exc

    loop_count = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name.split(".")[0] for n in node.names] if isinstance(node, ast.Import) \
                else [(node.module or "").split(".")[0]]
            for n in names:
                if n not in _ALLOWED_IMPORTS:
                    raise CadGenerationError(f"import '{n}' is not allowed")
        elif isinstance(node, (ast.For, ast.While)):
            loop_count += 1
            if loop_count > _MAX_LOOPS:
                raise CadGenerationError("too many loops")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise CadGenerationError(f"use of '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                raise CadGenerationError(f"dunder attribute '{node.attr}' is not allowed")
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in _FORBIDDEN_ATTR_ROOTS:
                raise CadGenerationError(f"access to '{root.id}' is not allowed")
        elif isinstance(node, ast.Name) and node.id.startswith("__") and node.id.endswith("__"):
            raise CadGenerationError(f"dunder name '{node.id}' is not allowed")


_SAFE_BUILTINS = {
    "range": range, "len": len, "float": float, "int": int, "abs": abs, "round": round,
    "min": min, "max": max, "list": list, "dict": dict, "tuple": tuple, "set": set,
    "enumerate": enumerate, "zip": zip, "sorted": sorted, "sum": sum, "map": map,
    "True": True, "False": False, "None": None, "bool": bool, "str": str,
}


def run_program(code: str, *, trusted: bool, timeout: int = _TIMEOUT_S) -> tuple[bytes, bytes, dict]:
    """Lint then run; return (stl_bytes, step_bytes, metadata)."""
    lint_code(code)
    if trusted and os.environ.get("CADMAKER_SANDBOX", "subprocess") == "inprocess":
        return _run_inprocess(code)
    return _run_subprocess(code, timeout)


# --- trusted in-process (linted, restricted builtins) ----------------------
def _run_inprocess(code: str) -> tuple[bytes, bytes, dict]:
    import cadquery as cq
    import math

    ns: dict = {"cq": cq, "math": math, "__builtins__": _SAFE_BUILTINS}
    try:
        exec(compile(code, "<cad_program>", "exec"), ns)  # noqa: S102 - linted + restricted
    except Exception as exc:  # noqa: BLE001
        raise CadGenerationError(f"program error: {exc}") from exc
    result = ns.get("result")
    meta = ns.get("meta") or {}
    if result is None:
        raise CadGenerationError("program did not assign `result`")
    with tempfile.TemporaryDirectory(prefix="cqp_") as tmp:
        stl_p, step_p = Path(tmp) / "model.stl", Path(tmp) / "model.step"
        cq.exporters.export(result, str(stl_p))
        cq.exporters.export(result, str(step_p))
        stl, step = stl_p.read_bytes(), step_p.read_bytes()
    _check_exports(stl, step)
    meta = _augment_meta(meta, result)
    return stl, step, meta


def _augment_meta(meta: dict, result) -> dict:
    if "dimensions" not in meta:
        bb = result.val().BoundingBox()
        meta["dimensions"] = {"x": round(bb.xlen, 3), "y": round(bb.ylen, 3), "z": round(bb.zlen, 3)}
    try:
        meta["solid_count"] = len(result.vals())
    except Exception:  # noqa: BLE001
        meta.setdefault("solid_count", 1)
    return meta


# --- untrusted subprocess --------------------------------------------------
_HARNESS = '''\
import cadquery as cq
import math, json
meta = {{}}
def _build():
{body}
    return locals()
_ns = _build()
result = _ns.get("result")
meta = _ns.get("meta", {{}})
if result is None:
    raise SystemExit("no result")
cq.exporters.export(result, "model.stl")
cq.exporters.export(result, "model.step")
bb = result.val().BoundingBox()
meta.setdefault("dimensions", {{"x": round(bb.xlen,3), "y": round(bb.ylen,3), "z": round(bb.zlen,3)}})
try:
    meta["solid_count"] = len(result.vals())
except Exception:
    meta.setdefault("solid_count", 1)
json.dump(meta, open("metadata.json", "w"))
'''


def _run_subprocess(code: str, timeout: int) -> tuple[bytes, bytes, dict]:
    body = "\n".join("    " + line for line in code.splitlines()) or "    result = None"
    script = _HARNESS.format(body=body)
    with tempfile.TemporaryDirectory(prefix="cqp_") as tmp:
        (Path(tmp) / "prog.py").write_text(script)
        try:
            proc = subprocess.run(
                [sys.executable, "prog.py"], cwd=tmp, capture_output=True, timeout=timeout,
                env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": "",
                     "HOME": tmp, "TMPDIR": tmp},
            )
        except subprocess.TimeoutExpired as exc:
            raise CadGenerationError("CAD program timed out") from exc
        if proc.returncode != 0:
            raise CadGenerationError(
                f"CAD program failed: {(proc.stderr or b'').decode('utf-8','replace')[:400]}")
        d = Path(tmp)
        stl_p, step_p, meta_p = d / "model.stl", d / "model.step", d / "metadata.json"
        if not (stl_p.exists() and step_p.exists()):
            raise CadGenerationError("CAD program did not export STL/STEP")
        stl, step = stl_p.read_bytes(), step_p.read_bytes()
        _check_exports(stl, step)
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
        return stl, step, meta


def _check_exports(stl: bytes, step: bytes) -> None:
    if not stl or not step:
        raise CadGenerationError("empty export")
    if len(stl) > _MAX_EXPORT_BYTES or len(step) > _MAX_EXPORT_BYTES:
        raise CadGenerationError("export exceeds size limit")
    if step[:5] != b"ISO-1":
        raise CadGenerationError("STEP export is not valid")
