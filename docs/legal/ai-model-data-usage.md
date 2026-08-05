# AI / Model Data Usage — DRAFT

> **DRAFT — REQUIRES QUALIFIED LEGAL REVIEW. THIS IS NOT LEGAL ADVICE.**

## Two separate things this document covers

1. **Sending your data to a third-party model provider to generate YOUR
   design.** This happens for every request that needs the AI planning step
   (not every request does — many route through deterministic templates with
   no model call at all). This is necessary to provide the service you asked
   for and is not a separate "choice."
2. **Using prompts/designs to improve LunaiCAD's OWN models/product.** This
   is a distinct, optional use — separate from (1) — and is opt-**IN**,
   defaulting to **off** for every account.

## The opt-in control

`User.data_improvement_opt_in` (default `false`) is a per-account setting,
changeable at any time via **Settings → Privacy & data** in the product, or
`PUT /api/auth/me/data-improvement-opt-in`. When off (the default):

- Prompts and designs are not used for any model-improvement or evaluation
  purpose beyond generating that user's own requested output.

When a user turns it on, they are consenting to their prompts/designs being
used to improve LunaiCAD's models.
`[UNRESOLVED — REQUIRES LEGAL INPUT: the EXACT scope of "improve LunaiCAD's
models" that should be represented to users once counsel has reviewed what
this will actually be used for — e.g. is it limited to LunaiCAD's own
fine-tuning/evaluation pipeline, or could it also flow to the third-party
model provider's own training data under THEIR terms? These may be
different scopes requiring different consent language.]`

## Third-party model provider

Some generation requests are sent to a third-party AI model provider.
`[UNRESOLVED — REQUIRES LEGAL INPUT: name the provider(s), link to their API
data-usage/training policy, and confirm (from the actual vendor agreement,
not assumption) whether API requests are used by the PROVIDER for their own
model training by default, and whether that default can/has been
contractually disabled for this product's usage tier. This is a material
fact for the privacy policy and should not be asserted without confirming
the actual vendor terms in effect.]`

## What we can say with confidence today

- The opt-in flag is real, stored, and independently queryable/changeable at
  any time (not a cosmetic setting) — verified by
  `backend/tests/test_privacy_controls.py`.
- It defaults to off for every new account; nothing flips it on except an
  explicit user action.
- Raw prompt text is never written to ordinary application logs regardless
  of this setting (see `docs/ops/observability.md`) — the opt-in governs
  model-improvement USE, not baseline logging, which is separately
  redacted either way.
