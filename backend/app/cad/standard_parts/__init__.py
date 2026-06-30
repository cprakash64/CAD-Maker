"""Deterministic Standard Part Resolver for catalog mechanical parts.

A recognized standard / catalog part (e.g. "M12 hex nut", "DIN 934 M12 nut")
must NOT fall into the generic "missing dimensions" clarification gate — its
dimensions come from a published standard table, not from the user. This package
resolves such a prompt to a concrete, dimensioned standard part BEFORE the
clarification gate runs.

LEGAL / SOURCING NOTE:
McMaster CAD files must not be scraped, cached, redistributed, or used as source
geometry unless LunaiCAD has explicit commercial permission. All geometry here is
generated parametrically from public dimensional standards (ISO / DIN nominal
tables), never copied from a vendor CAD library.
"""
from __future__ import annotations

from app.cad.standard_parts.resolver import (
    StandardPartResolution,
    resolve_standard_part,
)

__all__ = ["StandardPartResolution", "resolve_standard_part"]
