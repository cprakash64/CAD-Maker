# Physical calibration

This document is the single canonical description of LunaiCAD's physical
calibration system: the versioned profile schema, the ten calibration
coupons, the data workflow, how profile values are consumed by generation,
and the exact physical printing/measurement procedure for each coupon.

## Honesty rules (non-negotiable)

- **A profile is never "physically validated" without physical
  measurements.** `CalibrationProfile.status` only becomes `validated`
  through `app.services.calibration_service.review_and_validate`, which
  refuses unless `eligible_for_validation()` returns no errors: real
  measurements (≥3 raw samples each), complete process fields, and an
  explicit human review (`reviewed=True`, `reviewed_by_user_id` set).
- **`source_type=generic_estimate` can never become `validated`.** By
  definition it is a built-in default, not a physical measurement — see
  `CalibrationProfile.eligible_for_validation`.
- **No fit guarantee is promised.** A resolved clearance is either "from a
  generic estimate, not physically measured" or "from validated profile X (N
  samples)" — every consumer states this explicitly (`ResolvedCalibrationValue
  .is_estimate` / `.provenance`), never silently.
- **Only a validated profile can be activated as a tested profile**
  (`CalibrationProfile.eligible_for_activation`) — a draft or rejected
  profile, however complete, is never picked up automatically by generation.

## Profile schema (versioned)

`app/schemas/calibration.py`, `SCHEMA_VERSION = 1`.

`CalibrationProfile`:

| Field | Meaning |
|---|---|
| `id`, `version` | Identity + revision. A **validated** profile is immutable (`calibration_service.update_profile` refuses edits); to change it, `fork_profile` creates a new draft with `derived_from_id` pointing back and `version + 1`. |
| `source_type` | `generic_estimate` \| `lunaicad_tested` \| `vendor_supplied` \| `community_submitted` \| `user_calibrated` |
| `status` | `draft` \| `validated` \| `rejected` |
| `printer`, `firmware`, `nozzle_mm` | Machine identity |
| `material_type`, `material_brand`, `material_product`, `material_color` | Material identity |
| `slicer`, `slicer_version`, `line_width_mm`, `layer_height_mm`, `nozzle_temp_c`, `bed_temp_c`, `flow_pct`, `wall_count`, `cooling_pct` | Process settings |
| `test_date`, `measurements`, `notes` | The test record |
| `reviewed`, `reviewed_by_user_id`, `reviewed_at` | Human review gate |
| `active` | Whether the resolver may pick this up automatically for its (printer, material, nozzle) context |

`CalibrationMeasurement` — one measured quantity, always distinguished by
`measurement_type` (never conflate these):

| `measurement_type` | Meaning |
|---|---|
| `radial_clearance` | Per-side (single-face) gap between a hole and a mating pin/shaft, mm |
| `diametral_clearance` | Total (diameter-to-diameter) gap — measured independently, not assumed to be exactly 2× radial (ovality/squish differ by axis) |
| `dimensional_correction` | A systematic printer offset: nominal minus actual, mm (signed; negative shrinks) |
| `functional_fit_recommendation` | A qualitative recommendation (`fit_class`) carrying the clearance range that achieves it |

`fit_class`: `press` (interference) \| `snug` \| `normal` \| `loose`.

`sample_count`, `median_mm`, `range_mm`, `stddev_mm`, `confidence` are always
**server-derived from `raw_samples_mm`**
(`CalibrationMeasurement.recompute_statistics`) — a client can submit raw
samples, never the aggregate directly, so a profile can never claim
statistics that don't match its own raw data. `confidence` rewards more
samples and penalizes spread, capped at 0.95 — a human review is still
required for `validated` regardless of how high it is.

## Calibration artifacts

Ten deterministic templates (`app/cad/templates/calibration_coupons.py`),
registered in the same trusted template registry as every other part
(`app/cad/registry.py`), each with a `CADFamily` entry
(`maturity=experimental`, `app/cad/families.py`). Generation goes through the
**same validated export path** as any other design
(`app.export.exporter.generate` → `_assert_valid_solid` → STL/STEP), via
`POST /api/calibration/coupons/{coupon_type}/generate`.

| Coupon | `object_type` | What it measures |
|---|---|---|
| Master coupon | `calibration_master_coupon` | Baseline XY/Z accuracy (one hole, one boss vs. nominal) |
| Vertical hole gauge | `calibration_vertical_hole_gauge` | XY hole-diameter compensation (holes on the print axis) |
| Horizontal hole plate | `calibration_horizontal_hole_plate` | Hole accuracy with a bed-parallel (bridged) axis |
| Clearance & press-fit ladder | `calibration_fit_ladder` | Finds the press/snug/normal/loose transition diameters |
| Wall / pin / gap coupon | `calibration_wall_pin_gap_coupon` | Minimum printable wall, pin, and gap size |
| Overhang & bridge tower | `calibration_overhang_bridge_tower` | Maximum reliable overhang angle and bridge span |
| Fastener plate | `calibration_fastener_plate` | Real-world M3-M6 clearance/pilot hole fit |
| Snap-fit kit | `calibration_snap_fit_kit` | Thickest cantilever beam that still snaps without cracking |
| Mechanism (rotating fit) coupon | `calibration_mechanism_coupon` | Verifies a profile's rotating-fit clearance recommendation |
| Text legibility plate | `calibration_text_plate` | Smallest legible engraved text |

## Data workflow

All under `/api/calibration` (`app/routers/calibration.py`,
`app/services/calibration_service.py`):

- **CSV import**: `POST /profiles/{id}/measurements/import-csv` (raw text) or
  `.../import-csv-file` (multipart upload). Columns: `measurement_type,
  feature, fit_class, nominal_mm, samples, notes` — `samples` is
  semicolon-separated raw mm values. **All-or-nothing**: every row is
  validated before any row is written; one bad row rejects the whole import.
- **Manual entry**: `POST /profiles/{id}/measurements` with `raw_samples_mm`.
- **Validation**: both paths route through the same
  `CalibrationMeasurement` Pydantic validators (type-specific requirements,
  finite-sample check) — no second, drifting copy of the rules.
- **Storage of raw samples**: `raw_samples_mm` (JSON) on
  `calibration_measurements`, one row per measurement, FK to the profile.
- **Profile comparison**: `GET /compare?ids=a,b,c` — aligned, per-
  `(measurement_type, feature, fit_class)` side-by-side statistics.
- **Provenance display**: every `CalibrationProfile` response includes
  `provenance_summary()` (id, version, label, source_type, status, reviewed,
  sample_count, printer, material_type); every design built with a resolved
  calibration value states its source in `assumptions` and/or a
  `calibration_profile_provenance` check (see Consumption).
- **Draft vs. validated status**: `POST /profiles/{id}/review` (validates,
  or refuses with the specific missing requirements),
  `POST /profiles/{id}/reject`, `POST /profiles/{id}/activate` /
  `/deactivate`.

## Consumption

`app/cad/calibration/resolver.py::resolve_measurement()` is the one place
every fit/clearance decision resolves through — never a bare literal. It
prefers a matching **active, validated** profile (exact printer+material+
nozzle match preferred, then most samples, then highest confidence) and
falls back to `GENERIC_ESTIMATE`, a named, typed profile carrying today's
pre-calibration-system defaults (so behavior is unchanged until a real
profile is validated) with `confidence=0.0` and `is_estimate=True` always
returned to the caller.

Wired in:

- `app.cad.object_intelligence.resolver._resolve_bearing` — press/slip
  bearing-seat diametral clearance (previously a bare `-0.02`/`0.05` literal).
- `app.cad.object_intelligence.resolver._resolve_phone` — holder case
  clearance (previously a bare `1.5` literal).
- `app.cad.plan.dimension_report._compensation_notes` — printer XY
  dimensional correction note.
- `app.cad.templates.calibration_coupons.MechanismCoupon` — bore clearance,
  with a **real, DB-backed override**:
  `app.routers.calibration.generate_coupon` resolves against the caller's
  `printer`/`material_type`/`nozzle_mm` via
  `calibration_service.resolve_for_generation` *before* building the spec,
  so an active validated profile genuinely changes the generated geometry
  (not just a label) — this is the one place a real profile lookup reaches
  generation end-to-end in this phase.
- `app.manufacturability.checks._calibration_provenance_check` — surfaces,
  as a `calibration_profile_provenance` check on `bearing_holder` and
  `phone_holder` designs, whether the fit value used was a generic estimate
  (`severity=warning`) or a named validated profile (`severity=info`) — this
  is what makes provenance appear in user-facing validation results
  (`DesignDTO.checks`).

### Standards vs. printer compensation (kept separate, on purpose)

The ISO/metric screw clearance-hole table
(`app.cad.standards.defaults.METRIC_CLEARANCE_HOLES`) is a **fastener
standard**, not a print-tolerance number — it stays a fixed reference table.
Three near-duplicate copies of it existed before this phase
(`app.cad.standards.defaults`, `app.cad.plan.defaults`,
`app.cad.hex_standoff`); they are now consolidated to the one canonical
table, with the other two delegating to it. A **printer-specific**
dimensional correction (shrink/growth compensation) is a *separate*,
calibration-profile-driven value layered on top where relevant — never mixed
into the standards table itself.

## Exact physical printing and measurement procedure

For every coupon: print at your NORMAL settings for the printer/material/
nozzle you are calibrating (the ones you'll enter as the profile's process
fields) — do not special-case the calibration print. Let the part cool fully
before measuring; PLA/PETG especially can still be slightly out of round
warm. Use a digital caliper (±0.01mm resolution) for everything below;
pin/plug gauges are a helpful cross-check for the fit ladder and mechanism
coupon but not required.

1. **Master coupon** — Measure the block's X, Y, and Z with calipers against
   the 40/40/9mm nominal (per-run bbox echoed in the design's
   `bounding_box_mm`). Measure the hole diameter (calipers, or a pin gauge)
   and the boss diameter/height. Record hole and boss diameter as
   `dimensional_correction` measurements (`feature="hole_diameter"` /
   `"boss_diameter"`, `nominal_mm` = the value from the design's dimensions,
   sample = nominal − measured).

2. **Vertical hole gauge** — For each of the 8 holes, try to pass drill bits
   or pin gauges of the NOMINAL diameter (labeled by position, smallest to
   largest) through; measure the actual diameter with calipers where
   possible (small holes: use pin gauges/drill bit go/no-go). Record one
   `dimensional_correction` measurement per hole
   (`feature=f"vertical_hole_{nominal:g}mm"`, `nominal_mm=<that hole's
   nominal>`).

3. **Horizontal hole plate** — Same procedure as #2, but for the
   bed-parallel holes on the standing wall — expect MORE distortion/sag on
   the larger holes since they print bridged. Record as
   `feature=f"horizontal_hole_{nominal:g}mm"`.

4. **Clearance & press-fit ladder** — Insert the fixed reference pin into
   each hole in order, from tightest to loosest. Note which hole is: too
   tight to insert, insertable with force (press), insertable with light
   hand pressure and self-retaining (snug), free sliding with slight play
   (normal), loose/rattling (loose). Record ONE
   `functional_fit_recommendation` measurement per transition you find, e.g.
   `feature="hole_pin_clearance"`, `fit_class="press"`,
   `raw_samples_mm=[<that hole's designed clearance, repeated 3x if you only
   tested once — see note below>]`. **Best practice**: print and test the
   ladder 3 times (or on 3 identical prints) so each `fit_class` transition
   has ≥3 independent samples, since `eligible_for_validation` requires it.

5. **Wall / pin / gap coupon** — Flex/inspect each fin from thinnest to
   thickest; find the thinnest one that is fully solid (no pinholes, doesn't
   flex/crack immediately). Do the same for the pin row (thinnest pin that
   survives handling) and the slot row (narrowest slot a 0.1mm feeler gauge
   or the edge of a blade can actually enter). Record as three
   `dimensional_correction` measurements: `feature="min_wall_thickness"`,
   `feature="min_pin_diameter"`, `feature="min_gap_width"`, each
   `nominal_mm` = the smallest surviving/resolvable step's designed size.

6. **Overhang & bridge tower** — Inspect each staircase step's underside for
   sagging/drooping filament; find the last CLEAN step. Inspect each bridge
   span's underside for sag/stringing; find the longest clean span. Record
   `feature="max_clean_overhang_step"` and `feature="max_clean_bridge_span_mm"`
   as `dimensional_correction` measurements (`nominal_mm` = the designed
   step/span reached).

7. **Fastener plate** — Thread the actual screws (M3-M6) through both the
   clearance and pilot holes. Note which clearance holes are free-running and
   which pilot holes hold the screw without splitting the plastic. Record any
   deviation as a `dimensional_correction` measurement per size
   (`feature=f"M{n}_clearance_hole"`, `nominal_mm` = the standards clearance
   diameter used).

8. **Snap-fit kit** — Deflect each beam by hand (a slow, firm push, not a
   snap-test yank) 10-20 times; find the thickest beam that survives without
   whitening/cracking at the root. Record as a
   `functional_fit_recommendation` (`feature="snap_fit_beam"`,
   `fit_class="normal"`, sample = that beam's designed thickness, repeated
   across ≥3 kits/beams of the same thickness for a real sample size).

9. **Mechanism coupon** — Insert the shaft peg into the bore; confirm it
   rotates freely without excessive play. If it's too tight or too loose,
   measure both features with calipers and record the actual diametral gap
   (bore ID − peg OD) as a `diametral_clearance` measurement,
   `feature="holder_case_clearance"`, `fit_class="normal"` — this is what
   feeds back into the mechanism coupon's own resolver call for future
   prints.

10. **Text legibility plate** — Under normal lighting (not raking light,
    which flatters engraving), read each row from farthest to nearest;
    find the smallest size that is unambiguously legible without a
    magnifier. Record `feature="min_legible_engraved_text_mm"` as a
    `dimensional_correction` measurement, `nominal_mm` = that row's font
    size.

After entering ≥3 samples per measurement and completing the profile's
process fields (printer, nozzle, material, layer height, line width),
`POST /profiles/{id}/review` to validate, then `/activate` so generation
picks it up automatically for that printer/material/nozzle.

## Acceptance criteria → where each is enforced

- **No critical fit value is silently hardcoded where a profile lookup is
  required** — every fit/clearance call site listed under Consumption routes
  through `resolve_measurement()`; the three duplicate clearance tables are
  consolidated to one canonical standards source.
- **Invalid or incomplete profiles cannot be activated as tested profiles**
  — `CalibrationProfile.eligible_for_activation()` /
  `calibration_service.activate_profile` (tested:
  `tests/test_calibration.py::test_invalid_incomplete_profile_cannot_be_activated_via_api`).
- **Coupon exports pass geometry and export checks** — all ten coupons
  produce a single valid manifold solid and export non-empty STL+STEP
  through the real API (tested:
  `test_every_coupon_generates_a_single_valid_solid_and_exports`,
  parametrized over all ten).
- **Profile provenance appears in user-facing validation results** —
  `calibration_profile_provenance` check on `DesignDTO.checks` (tested:
  `test_bearing_holder_design_shows_calibration_provenance`).
- **Documentation includes the exact physical printing and measurement
  procedure** — this section.
