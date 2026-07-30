"""Precise user-facing language contract.

Two things live here:

1. ``BANNED_CLAIMS`` -- phrases that overstate what LunaiCAD's geometric
   checks actually verify. Used by ``scripts/audit_banned_language.py`` (grep
   audit across backend + frontend source) and by
   ``tests/test_safety_language.py`` (regression guard on the strings this
   module and the safety/trust surfaces themselves emit).
2. ``PRECISE_PHRASES`` -- the approved vocabulary for describing outcomes.
   Prefer these over ad-hoc wording when adding new user-facing copy.

A printability/manufacturability pass is a GEOMETRIC check (watertight,
correct dimensions, printable wall thickness / hole size). It is never a
structural, safety, or regulatory certification, and no user-facing string
anywhere in the product may describe it as one.
"""
from __future__ import annotations

import re

# (pattern, human reason) -- matched case-insensitively against user-facing
# string literals. Deliberately phrase-level, not single-word, to avoid
# flagging legitimate internal/technical uses (e.g. the `production_ready`
# capability-level ENUM NAME, or "not guaranteed to fit" -- a hedge, not a
# claim).
BANNED_CLAIMS: list[tuple[str, str]] = [
    (r"\bproduction[-\s]ready\b(?!\s+template)",
     "implies the DESIGN is ready to manufacture, not just that the tool's "
     "template maturity is high"),
    (r"\bguaranteed\s+fit\b", "no physical fit is guaranteed without a real "
     "calibration measurement and engineering review"),
    (r"\bmanufactur(?:ing|er)[-\s]certified\b",
     "LunaiCAD performs no manufacturing certification"),
    (r"\bsafe\s+for\s+load[-\s]?bearing\s+use\b",
     "a geometric pass is not a structural safety determination"),
    (r"\bperfect\s+cad\b", "overstates output quality/fidelity"),
    (r"\baccurate\s+from\s+any\s+drawing\b",
     "drawing interpretation has documented fidelity limits (see "
     "docs/drawing-to-cad-beta.md)"),
    (r"\bstructurally\s+certified\b",
     "LunaiCAD performs no structural certification"),
    (r"\bsafety[-\s]certified\b", "LunaiCAD performs no safety certification"),
    (r"\bpassed\s+(?:structural|safety)\s+(?:certification|review)\b",
     "a printability/geometric pass must never be described as a structural "
     "or safety certification"),
    (r"\bengineer(?:ing)?[-\s]approved\b",
     "no human engineer reviews generated output"),
    (r"\bready\s+to\s+manufacture\b",
     "manufacturability is not asserted without engineering review"),
]

_BANNED_RE = [(re.compile(p, re.IGNORECASE), why) for p, why in BANNED_CLAIMS]


def find_banned_claims(text: str) -> list[tuple[str, str]]:
    """Every (matched phrase, reason) found in `text`."""
    hits: list[tuple[str, str]] = []
    for regex, why in _BANNED_RE:
        m = regex.search(text)
        if m:
            hits.append((m.group(0), why))
    return hits


PRECISE_PHRASES = {
    "geometric_pass": "Passed LunaiCAD geometric checks",
    "dimensions_match": "Dimensions match the interpreted specification",
    "fit_guidance": "Fit guidance is based on the selected calibration profile",
    "review_required": "Engineering review is required before manufacture",
    "printability_scope": (
        "This is a printability and geometry check, not a structural or "
        "safety certification."
    ),
}
