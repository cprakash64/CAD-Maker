# Privacy Policy — DRAFT

> **DRAFT — REQUIRES QUALIFIED LEGAL REVIEW. THIS IS NOT LEGAL ADVICE.**
> The "what we store / how long / who can see it" sections below are written
> directly from the current implementation and are the reliable part of this
> draft. The jurisdiction-specific rights language (GDPR/CCPA/etc.) is a
> placeholder for counsel — do not publish as-is.

## What we collect and store

| Data | Where | Purpose |
|---|---|---|
| Email + password (hashed) | `users` table | Account authentication |
| Prompts (text) | `designs.prompt` | The request that produced a design |
| Uploaded drawings/images | Processed transiently during interpretation; the interpreted geometry (not the raw uploaded file) is what's persisted | Drawing → CAD generation |
| Generated design data (spec, dimensions, validation results, safety classification) | `designs` table and related rows | The core product record |
| Exported files (STL/STEP/GLB) | Object storage, referenced by `export_files` | Downloadable manufacturing files |
| IP address, request metadata | Structured logs (redacted — see below) | Abuse prevention, debugging, rate limiting |

Live counts and your current settings for your own account are available at
any time via **Settings → Privacy & data** in the product (backed by
`GET /api/auth/privacy-summary`).

## What we do NOT log

Structured application logs never contain: raw passwords, authorization
tokens/JWTs, full uploaded drawing files, or (as of the redaction work
described in `docs/ops/observability.md`) raw prompt text — prompts are
logged as a one-way hash (`prompt_hash`) and user identifiers are
pseudonymized before being written to ordinary logs.

## Third parties

Some generation requests are sent to a third-party AI model provider for
processing. `[UNRESOLVED — REQUIRES LEGAL INPUT: name the provider(s) by
legal name, link their DPA/subprocessor terms, and confirm what a request
payload to them does/doesn't include once counsel has reviewed the vendor
agreement.]`

Whether prompts/designs are additionally used to *improve* LunaiCAD's own
models is a separate, user-controlled choice — see
[ai-model-data-usage.md](ai-model-data-usage.md). It defaults to **off** for
every account.

## Retention and deletion

See [data-retention-policy.md](data-retention-policy.md) for the full
policy. In short: design records are kept until you delete them or your
account; exported files beyond a retention window are reclaimed
automatically (the design record itself is not); account deletion is
immediate, self-service, and permanent (`DELETE /api/auth/me`).

## Your choices

- **Data-improvement opt-in**: toggle at any time in Settings (default off).
- **Account deletion**: self-service, permanent, requires password
  confirmation.
- **Design visibility**: every design is private to your account by default
  — access is enforced by ownership check on every route, not merely hidden
  from a listing.

## Your rights (jurisdiction-specific)

`[UNRESOLVED — REQUIRES LEGAL INPUT: GDPR (access/rectification/erasure/
portability/objection), CCPA/CPRA (know/delete/opt-out of sale — note: this
product does not sell personal data as far as engineering is aware, confirm
with counsel), and any other applicable regional framework. Map each right
to the existing technical mechanism where one exists (e.g. "right to
erasure" -> `DELETE /api/auth/me`) and flag any right with NO existing
mechanism (e.g. data portability/export-my-data has no dedicated endpoint
today) so counsel can decide whether one must be built before publishing.]`

## Contact

`[UNRESOLVED — REQUIRES LEGAL INPUT: privacy contact email/address, and
(if applicable) Data Protection Officer designation.]`
