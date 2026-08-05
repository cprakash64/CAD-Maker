# Generated-Design Ownership — DRAFT

> **DRAFT — REQUIRES QUALIFIED LEGAL REVIEW. THIS IS NOT LEGAL ADVICE.**
> This is the document where the gap between "what the software technically
> enforces" and "what is legally true" is widest. Read carefully.

## What the software technically enforces today

- Every design is associated with exactly one owning account (via
  project → user), and every read/write/export route checks that
  association before permitting access — a user cannot see, edit, or
  download another user's design (`backend/tests/test_ownership_export_hardening.py`).
- Deleting an account permanently deletes every design it owns
  (`backend/app/services/account_service.py::delete_account`).
- There is no sharing, team, or organization concept in the current product
  — ownership is strictly one account per design.

This is an **access-control** guarantee, not a **legal ownership/copyright**
determination — the two are not the same thing, which is exactly why this
document exists separately.

## The unresolved legal question

A meaningful fraction of LunaiCAD's generated geometry is produced (in whole
or part) by a third-party AI model, from a user's prompt. Copyright law's
treatment of AI-generated or AI-assisted output is unsettled and varies by
jurisdiction, and depends on facts like: how much of the final geometry is
deterministic/template-driven (which is more clearly ordinary software
output) versus AI-model-generated (which is the contested category), and how
much creative input the user's prompt contributed.

`[UNRESOLVED — REQUIRES LEGAL INPUT: the operating entity's position on (a)
what rights, if any, it claims in generated designs vs. assigns/licenses to
the user, (b) what representation (if any) can be made to users about their
ability to copyright or exclusively own the output, and (c) how this
interacts with the AI provider's own terms of service for model output
ownership — see ai-model-data-usage.md for the provider relationship this
depends on.]`

## What engineering can say with confidence

Regardless of the copyright analysis above, a design a user's account owns
is theirs to access, edit, export, and delete exclusively, for as long as
their account exists (or until they delete the design themselves) — that
part is a software access-control fact, not a legal claim, and doesn't wait
on the unresolved question above.

## Uploaded drawings

A user who uploads a drawing represents that they have the right to do so.
`[UNRESOLVED — REQUIRES LEGAL INPUT: standard representation/warranty
language for user-uploaded content, and what obligations (if any) exist
around third-party IP that might appear in an uploaded drawing.]`
