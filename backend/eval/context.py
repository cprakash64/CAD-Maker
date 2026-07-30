"""What one case execution produces, before assertions run over it.

Kept deliberately provider-agnostic: an `EvalContext` is built the same way
whether the request went through the mock provider or a live OpenAI call —
assertions never know or care which.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from eval.schema import EvalCase


@dataclass
class EvalContext:
    case: EvalCase
    ok: bool                              # the workflow call completed (no 5xx/crash)
    http_status: Optional[int] = None
    response: Optional[dict] = None       # JSON body (DesignDTO or endpoint payload)
    before_response: Optional[dict] = None  # modification: the setup design's DTO
    error: Optional[str] = None
    stl_bytes: Optional[bytes] = None
    step_bytes: Optional[bytes] = None
    latency_ms: float = 0.0
    provider: str = "mock"
    model: Optional[str] = None
    retries: int = 0
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    estimated_cost_usd: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    _reimport_cache: Optional[dict] = field(default=None, repr=False)
    _mesh_cache: Optional[dict] = field(default=None, repr=False)

    def reimport_step(self) -> Optional[dict]:
        """Re-import the exported STEP bytes as a fresh solid and measure it —
        this is the ground truth that the file we handed the user is really a
        usable, valid, correctly-dimensioned part, not just "some bytes"."""
        if self._reimport_cache is not None:
            return self._reimport_cache
        if not self.step_bytes:
            return None
        import cadquery as cq

        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(self.step_bytes)
        try:
            wp = cq.importers.importStep(str(tmp_path))
            shape = wp.val()
            valid = bool(shape.isValid())
            bb = shape.BoundingBox()
            try:
                volume = float(shape.Volume())
            except Exception:  # noqa: BLE001
                volume = 0.0
            result = {
                "valid": valid,
                "bbox_mm": {"x": round(bb.xlen, 3), "y": round(bb.ylen, 3),
                            "z": round(bb.zlen, 3)},
                "volume_mm3": round(volume, 3),
            }
        except Exception as exc:  # noqa: BLE001 - a failed reimport IS the finding
            result = {"valid": False, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            tmp_path.unlink(missing_ok=True)
        self._reimport_cache = result
        return result

    def mesh_facts(self) -> Optional[dict]:
        if self._mesh_cache is not None:
            return self._mesh_cache
        if not self.stl_bytes:
            return None
        from app.cad.measure import mesh_facts

        self._mesh_cache = mesh_facts(self.stl_bytes)
        return self._mesh_cache
