# Terms of Service — DRAFT

> **DRAFT — REQUIRES QUALIFIED LEGAL REVIEW. THIS IS NOT LEGAL ADVICE.**
> Prepared by engineering as a factual starting point (what the product
> actually does) for counsel to turn into an enforceable agreement. Do not
> publish or present this to users as-is.

## 1. Who this agreement is between

`[UNRESOLVED — REQUIRES LEGAL INPUT: the legal entity name, form (LLC/Inc/
sole proprietorship/etc.), and jurisdiction of formation that operates
LunaiCAD. Nothing below can be finalized without this.]`

## 2. What LunaiCAD is

LunaiCAD is a software service that generates 3D CAD (computer-aided design)
models from natural-language prompts or uploaded drawings/images, and lets
users inspect, edit, and export those models (STL, STEP, GLB) for downstream
use (e.g. 3D printing).

Generation is performed by a combination of deterministic geometry templates,
a parametric feature-graph compiler, and (for some requests) a third-party AI
model provider (see [ai-model-data-usage.md](ai-model-data-usage.md) and
`[UNRESOLVED — REQUIRES LEGAL INPUT: name the current model provider(s) by
legal/contractual name once counsel reviews the vendor agreement]`).

## 3. Nature of the output — read alongside the Safety Disclaimer

**This is the single most important section for counsel to get right.**
LunaiCAD's automated checks are geometric: they verify that a model is
watertight, dimensionally matches the interpreted specification, and (where
applicable) meets basic 3D-printability heuristics. They are **not**
structural, safety, regulatory, or manufacturing certifications of any kind.
Full detail: [safety-and-engineering-disclaimer.md](safety-and-engineering-disclaimer.md).

Certain categories of request (weapon components, vehicle steering/braking
components, aerospace flight components, medical devices, and others) are
refused, restricted to non-manufacturable concept output, or gated behind an
explicit user acknowledgment — see
[acceptable-use-policy.md](acceptable-use-policy.md). Circumventing or
attempting to circumvent these controls (through rewording, indirect
description, file upload, or direct API use) is itself a violation of these
Terms.

## 4. Accounts

Users must provide a valid email and password to create an account. Users are
responsible for maintaining the confidentiality of their credentials and for
all activity under their account.
`[UNRESOLVED — REQUIRES LEGAL INPUT: minimum age / capacity-to-contract
requirement, and whether business/organizational accounts are in scope.]`

## 5. Ownership of generated designs

See [design-ownership.md](design-ownership.md) for the full treatment,
including the unresolved question of AI-generated-output copyright status.
Summary for this section: subject to that document's caveats, a user's
account retains exclusive access to and control over designs they generate,
consistent with the technical ownership/visibility model described there.

## 6. Fees

`[UNRESOLVED — REQUIRES LEGAL INPUT: pricing model, refund policy, and
whether this version of the product is free/beta. No billing code exists in
the current codebase as of this draft — confirm before publishing any
pricing language.]`

## 7. Disclaimers and limitation of liability

`[UNRESOLVED — REQUIRES LEGAL INPUT: standard "AS IS", warranty disclaimer,
and liability-cap language, drafted and scoped by counsel for the operating
entity's jurisdiction(s) and risk tolerance. Given the safety-sensitive
nature of some output categories (see Section 3), counsel should pay
particular attention to whether any disclaimer is enforceable against claims
arising from a user manufacturing an item this service explicitly warned
requires engineering review.]`

## 8. Termination

`[UNRESOLVED — REQUIRES LEGAL INPUT: grounds for suspension/termination by
either party. Note: self-service account deletion exists —
see data-retention-policy.md — engineering has NOT implemented an
operator-initiated suspension/ban mechanism as of this draft; flag to
counsel if the Terms should promise one.]`

## 9. Governing law and dispute resolution

`[UNRESOLVED — REQUIRES LEGAL INPUT: governing law, venue, arbitration
clause (if any).]`

## 10. Changes to these Terms

`[UNRESOLVED — REQUIRES LEGAL INPUT: notice mechanism and effective-date
policy for amendments.]`
