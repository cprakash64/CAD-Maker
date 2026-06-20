"""Expose the template catalog so the UI can show what can be built."""
from __future__ import annotations

from fastapi import APIRouter

from app.cad.registry import all_templates

router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.get("")
def list_templates() -> list[dict]:
    out = []
    for object_type, tmpl in all_templates().items():
        out.append(
            {
                "object_type": object_type,
                "name": tmpl.name,
                "description": tmpl.description,
                "parameters": [
                    {
                        "name": d.name,
                        "label": d.label,
                        "default": d.default,
                        "min": d.min,
                        "max": d.max,
                        "unit": d.unit,
                        "required": d.required,
                    }
                    for d in tmpl.dimensions
                ],
            }
        )
    return out
