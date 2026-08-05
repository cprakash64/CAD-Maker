# Calibration-Profile Disclaimer — DRAFT

> **DRAFT — REQUIRES QUALIFIED LEGAL REVIEW. THIS IS NOT LEGAL ADVICE.**
> Written from `docs/calibration.md` (the canonical technical description)
> and `backend/app/services/design_service.py::CALIBRATION_PROVENANCE_NOTICE`
> (the exact in-product disclosure string). If either changes, re-check this
> document against them.

## What a calibration profile is

A calibration profile records physical measurements from a specific
printer/material/process combination (e.g. hole clearance for a specific
nozzle, filament, and slicer settings), so LunaiCAD can adjust generated
clearances/fits toward what that specific setup actually produces, instead
of a generic estimate.

## What "validated" means — and what it doesn't

A profile can only reach `validated` status after real physical
measurements (multiple raw samples per measurement) and an explicit human
review step. A profile that has not gone through this — including the
built-in `generic_estimate` default every design starts from — is always
disclosed as an **estimate**, never presented as measured.

"Validated" means: this specific profile's numbers came from real
measurements of a real print, reviewed by a human. It does **not** mean:

- That the measurements are accurate for any printer/material combination
  other than the one profiled.
- That a part generated using this profile will fit correctly — a profile
  narrows the estimate; it does not eliminate the need to test-fit a
  physical print.
- Any certification of the profile's accuracy by LunaiCAD.

## What every generated design discloses

Every design's fit/clearance values carry an explicit provenance disclosure,
shown to the user, one of:

- *"Not calibrated to a specific printer/material profile — clearance and
  fit dimensions use generic engineering estimates, not a physically
  measured profile."*
- *"From validated profile [name] (N samples)."*

This is never silent or implied — the product always states which case
applies for a given design.

## Liability

`[UNRESOLVED — REQUIRES LEGAL INPUT: as with the general safety disclaimer,
whether this factual/technical disclosure is legally sufficient on its own,
or needs to be paired with an explicit waiver/acknowledgment for parts
manufactured using clearance data from either an unvalidated estimate or a
user-submitted (not LunaiCAD-verified) validated profile.]`
