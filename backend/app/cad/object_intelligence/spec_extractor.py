"""Extract a structured MechanicalObjectSpec from a ranked source (bounded).

This is the seam where a real implementation parses a datasheet / mechanical
drawing / STEP metadata into dimensions + mounting holes + connector cutouts. It is
offline-safe: with no extractor wired it returns ``None`` so the resolver falls back
to an honest REVIEW/CONCEPT rather than fabricating dimensions.

LEGAL: extraction yields LunaiCAD's OWN parametric dimensions; it never copies or
redistributes proprietary CAD geometry.
"""
from __future__ import annotations

from app.cad.object_intelligence.mechanical_spec import MechanicalObjectSpec
from app.cad.object_intelligence.source_ranker import source_type_for
from app.cad.object_intelligence.source_search import SourceHit


def extract_spec(object_name: str, hit: SourceHit, *,
                 timeout_s: float = 6.0) -> MechanicalObjectSpec | None:
    """Extract dimensions from a source hit, or ``None`` if extraction isn't wired /
    fails / is not permitted by licence. Bounded by ``timeout_s``."""
    if not hit.license_ok:
        return None
    # A real extractor would parse the document here and populate dimensions /
    # mounting_holes / connector_cutouts with an extraction confidence. Unconnected
    # by default → None, so the resolver never invents dimensions from a source.
    _ = (object_name, source_type_for(hit), timeout_s)
    return None
