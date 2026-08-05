# Acceptable Use Policy — DRAFT

> **DRAFT — REQUIRES QUALIFIED LEGAL REVIEW. THIS IS NOT LEGAL ADVICE.**
> The category table below is engineering's current, technically-enforced
> policy (`backend/app/safety/categories.py`) — it is a real, running system,
> not just draft text. Counsel should review the SEVERITY assignments (are
> weapon components correctly refused? is structural/load-bearing correctly
> only acknowledgment-gated rather than refused?) and the legal sufficiency
> of the disclaimer/acknowledgment language, not just wordsmith it.

## Prohibited and restricted uses

LunaiCAD automatically classifies generation and edit requests against the
categories below (by keyword/pattern detection over the prompt, edit text,
and any drawing notes — see `backend/app/safety/classifier.py` for the exact
detection logic and its documented limitations). The system enforces one of
five behaviors per category:

| Behavior | What happens |
|---|---|
| **Refuse** | The request is rejected outright. No design is generated. |
| **Conceptual only** | A geometric model may be generated for visualization, but no manufacturable file (STEP/STL) is ever offered for export. |
| **Require acknowledgment** | Generation and geometric checks proceed normally; manufacturable export is blocked until the user explicitly acknowledges that engineering review is required. |
| **Warn — review mandatory** | Generation and export proceed; a mandatory review notice is attached. |

| Category | Current behavior | Why |
|---|---|---|
| Weapon components | Refuse | No conceptual value offsets the risk of a lookalike manufacturable file existing |
| Vehicle steering or braking components | Refuse | Failure can cause loss of vehicle control |
| Aerospace flight components | Refuse | Flight-critical failure modes |
| Medical devices | Refuse | Regulatory + patient-safety scope outside this tool |
| Lifting equipment | Conceptual only | Visualization has value; unreviewed manufacturable output does not |
| Child-safety products | Conceptual only | Regulatory requirements (choking hazard, flammability, etc.) this tool does not check |
| Structural / load-bearing components | Require acknowledgment | Extremely common in ordinary mechanical work — outright blocking would make the product unusable for legitimate use |
| Pressure-containing parts | Require acknowledgment | Same reasoning — common, but burst/pressure-rating is unchecked |
| Fire-safety equipment | Require acknowledgment | Same reasoning |
| Mains-electrical-safety components | Require acknowledgment | Same reasoning |
| Other high-consequence applications | Warn — review mandatory | Floor/catch-all for anything else that reads as high-stakes |

**This classification is sticky per design**: once a design is flagged, later
edits cannot silently downgrade or remove the flag — the system re-evaluates
and keeps the most restrictive category ever detected. Users may not attempt
to bypass a classification by rewording a request, describing the same part
indirectly, uploading a drawing instead of typing a prompt, or calling the
API directly instead of using the product UI; doing so is itself a violation
of this policy (see the adversarial test suite,
`backend/tests/test_safety_policy.py`, for the specific bypass vectors this
is tested against).

## Known limitation — disclose to users, don't overstate to counsel as solved

Detection is text/pattern-based. A request built up entirely through numeric
parameter edits with no descriptive text, or a drawing upload with no notes
whose geometry isn't independently recognized as risky, is a real,
acknowledged gap — not something this system currently catches. Counsel
should weigh whether this gap needs disclosure in the public-facing policy
text (as a "we do our best, this is not a content-moderation guarantee"
caveat) versus purely an internal risk note.

## Other prohibited uses

`[UNRESOLVED — REQUIRES LEGAL INPUT: standard AUP boilerplate not specific to
the CAD-safety system above — e.g. prohibitions on abuse of the service
(scraping, credential sharing, reverse-engineering, excessive automated
load beyond documented rate limits), illegal use generally, and
infringement of third-party IP in uploaded drawings.]`

## Enforcement

`[UNRESOLVED — REQUIRES LEGAL INPUT: as noted in terms-of-service.md Section
8, no operator-initiated account suspension/ban mechanism exists in the
current codebase. Decide whether the AUP should promise enforcement actions
the product cannot yet technically perform, or whether that capability
should be built first.]`
