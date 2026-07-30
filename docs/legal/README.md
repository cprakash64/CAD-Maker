# Legal document drafts

**These are engineering-authored drafts prepared as a starting point for
qualified legal counsel. They are NOT legal advice, and NONE of them should
be published, linked from the product, or relied upon until a qualified
lawyer (licensed in the relevant jurisdiction(s)) has reviewed, revised, and
approved them.**

Every document in this directory carries the same banner and the same
`[UNRESOLVED — REQUIRES LEGAL INPUT]` markers for the questions engineering
cannot answer: what legal entity operates LunaiCAD, what jurisdiction(s) it's
formed/operates in, governing law and dispute-resolution forum, tax
treatment, and the final allocation of liability. Search each file for that
marker before treating anything here as settled.

What IS reliable in these drafts: the description of what the PRODUCT
actually does technically (what's stored, what's checked, what's blocked,
what the retention/deletion mechanics are) — that part is written from the
real implementation, cross-referenced by file, and should stay accurate as
long as the linked code doesn't drift out from under it. If code changes,
these docs need a re-read, not just a legal read.

| Document | Covers |
|---|---|
| [terms-of-service.md](terms-of-service.md) | Account terms, acceptable use pointer, service description, disclaimers, liability |
| [privacy-policy.md](privacy-policy.md) | What's collected/stored, retention, deletion, third parties (OpenAI), user rights |
| [acceptable-use-policy.md](acceptable-use-policy.md) | Prohibited uses, the safety-policy categories, enforcement |
| [design-ownership.md](design-ownership.md) | Who owns a generated design; LLM-output copyright uncertainty |
| [data-retention-policy.md](data-retention-policy.md) | User-facing retention/deletion policy (the ops-facing mechanics live in `docs/ops/data-retention.md`) |
| [ai-model-data-usage.md](ai-model-data-usage.md) | Whether/how prompts and designs are used to improve models; opt-in mechanics |
| [safety-and-engineering-disclaimer.md](safety-and-engineering-disclaimer.md) | What LunaiCAD's checks do and don't verify; engineering-review requirement |
| [calibration-profile-disclaimer.md](calibration-profile-disclaimer.md) | What a calibration profile is/isn't; fit-guidance scope |

## Cross-references into the codebase

- Technical safety-policy layer: `backend/app/safety/` (categories, classifier, policy, language)
- Privacy controls (opt-in, deletion, summary): `backend/app/services/account_service.py`, `backend/app/routers/auth.py`
- Retention mechanics (ops-facing): `docs/ops/data-retention.md`, `backend/app/ops/retention_sweep.py`
- Ownership/export gating: `backend/app/services/design_service.py` (`product_contract_fields`, `export_eligibility`)
- Precise-language contract (banned claims / approved phrases): `backend/app/safety/language.py`
