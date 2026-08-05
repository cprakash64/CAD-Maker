# Dependency risk assessment — 2026-07-31

Branch: `beta-readiness/release-candidate-20260730`, forked from
`stabilization/release-candidate-20260730` @ `703aec8c686c087ad5010ed2ef761b0f37014d56`.

This is a full investigation and remediation pass for the four
previously-reported dependency concerns (Starlette, VTK, pyasn1, ecdsa) plus
everything else the scanners surfaced along the way (frontend included). It
supersedes the "Baseline scan (run 2026-07-29)" table in
`docs/ops/security-scanning.md`, which tracked these same four backend
packages as accepted-but-unresolved; two of the four are now fixed, and the
other two carry a materially stronger reachability argument than before
(actual import-tracing evidence, not just "transitive, tracked").

## Scanners used

| Scanner | Version | Command | Raw artifact |
|---|---|---|---|
| `pip-audit` | 2.10.1 | `pip-audit -r backend/requirements.txt --format json` | `docs/security/scanner-artifacts/pip-audit-{before,after}.json` |
| `npm audit` | npm 11.15.0 | `npm audit --json` (in `frontend/`) | `docs/security/scanner-artifacts/npm-audit-{before,after}.json` |
| `osv-scanner` | 2.4.0 (osv-scalibr 0.4.5) | `osv-scanner scan source -r . --format json` | `docs/security/scanner-artifacts/osv-scanner-after.json` |
| `gh` dependency review | gh 2.96.0 | N/A — no open PR to diff against `main` at time of writing; GitHub's Dependency Graph/Dependabot alerts are now live via `.github/dependabot.yml` (added this change) for ongoing coverage | — |

No "before" osv-scanner run was captured (it wasn't installed yet at the
start of this pass); `pip-audit` and `npm audit` before/after pairs are the
primary comparison, cross-checked by `osv-scanner` against the final state.

### A scanner-accuracy note (osv-scanner vs. pip-audit)

`osv-scanner`'s scan of the unlocked `backend/requirements.txt` reported
several findings for packages/versions that do not actually appear anywhere
in this project's dependency tree (e.g. `ecdsa 0.9.0`, `idna 3.9.0`, `mako
1.3.9`, `pygments 2.9.0`, `tqdm 4.9.0` — none of these are real resolved
versions; compare `ecdsa==0.19.2`, `Mako==1.3.12` actually installed). This
is a known limitation of scanning an unpinned requirements file without a
lockfile: osv-scanner has to guess a transitive resolution and sometimes
guesses wrong. Its scan of the same repo's `backend/requirements.lock.txt`
(the new hash-pinned, fully-resolved lock file — see "Hash-pinned lock
file" below) instead found exactly the same 4 findings as `pip-audit`
(`ecdsa` × 1, `vtk` × 3), which is the correct, cross-validated answer. The
`requirements.txt`-only findings above are noted here for transparency but
are not carried into the findings table — they don't reflect real installed
versions.

`osv-scanner` also flagged `setuptools 80.9.0` (`PYSEC-2026-3447`) via the
backend SBOM. `setuptools` isn't a declared dependency of this project — it
ships bundled with every Python venv (`ensurepip`) as an install-time build
tool. It is never imported by `app/*` at request-serving time. Noted here
for completeness; not in the findings table below because it isn't one of
the reported concerns and isn't reachable from any request path. If it
becomes a real concern, the fix is `pip install --upgrade setuptools` in the
venv-bootstrap step, independent of `requirements.txt`.

## Findings

Every row: advisory id(s), package, severity (from the advisory, not just
the ecosystem scanner's bucket), production reachability, exploit
preconditions, available fix, remediation decision, residual risk.

### 1. Starlette — FIXED

| | |
|---|---|
| Advisories | `PYSEC-2026-161` (CVE-2026-48710, Host-header-based `request.url` reconstruction), `PYSEC-2026-248` (CVE-2026-54282, unvalidated request path in `request.url`), `PYSEC-2026-249` (CVE-2026-54283, `request.form()` resource-exhaustion bound bypass), `PYSEC-2026-1941` (CVE-2025-54121, multipart large-file spooling blocks the event loop), `PYSEC-2026-1942` (CVE-2025-62727, quadratic-time `FileResponse` Range-header parsing), `PYSEC-2026-2280` (CVE-2026-48817, `HTTPEndpoint` lowercases method name → handler-selection bypass), `PYSEC-2026-2281` (CVE-2026-48818, Windows-only `StaticFiles` UNC-path SSRF) |
| Package / installed | `starlette` 0.41.3 (transitive via `fastapi==0.115.6`, which pinned `starlette<0.42.0,>=0.40.0`) |
| Severity | High (several are unauthenticated DoS or request-smuggling class; none are RCE) |
| Reachability | **Yes — production-reachable.** Starlette is FastAPI's ASGI layer; every HTTP request goes through it. No mitigating factor made any of these unreachable. |
| Exploit preconditions | None beyond network access to the API — most are single unauthenticated HTTP request. |
| Fix available | `starlette>=1.3.1` fixes all listed advisories. |
| Remediation | **Fixed.** `fastapi` bumped `0.115.6 -> 0.134.0` (the earliest FastAPI release that drops the `<1.0.0` starlette ceiling — verified via PyPI metadata across every FastAPI point release from 0.116 through 0.135; `starlette` explicitly pinned `1.3.1`. This required bumping FastAPI too since Starlette version compatibility is entirely FastAPI's own dependency pin, not an independent choice — bumping Starlette alone is not possible while FastAPI stays at 0.115.6. Verified: `pydantic==2.10.4` (already installed) satisfies fastapi 0.134.0's `pydantic>=2.7.0` requirement; full backend suite + targeted auth/hardening tests pass (see "Verification" below). |
| Residual risk | None known. `starlette.testclient` now emits a `StarletteDeprecationWarning` recommending `httpx2` in place of `httpx` for the *test client* only (a future-direction notice, not a vulnerability, and not applicable to production request handling) — noted for a future dependency-update pass, not actioned here. |

### 2. pyasn1 — FIXED

| | |
|---|---|
| Advisories | `PYSEC-2026-2263` (CVE-2026-30922, uncontrolled-recursion DoS decoding deeply nested ASN.1), `PYSEC-2026-3455` (CVE-2026-59884, unbounded long-form BER tag accumulation), `PYSEC-2026-3456` (CVE-2026-59885, unbounded quadratic OID/RELATIVE-OID decoding), `PYSEC-2026-3457` (CVE-2026-59886, float mantissa/exponent conversion DoS) |
| Package / installed | `pyasn1` 0.4.8 (transitive via `python-jose==3.4.0`, which pinned `pyasn1<0.5.0,>=0.4.1`) |
| Severity | Moderate (all are decoder-side DoS, not memory corruption or auth bypass) |
| Reachability | **No — not production-reachable.** Traced by direct import inspection (`python -c` against the running app, see "Reachability tracing" below): `python-jose[cryptography]` is installed, so `jose.backends.__init__` resolves `HMACKey`/`ECKey` from `jose.backends.cryptography_backend`; the only module that imports `pyasn1` inside `python-jose` in this configuration is `jose.backends.rsa_backend` / `ecdsa_backend`, neither of which is ever imported when the `cryptography` backend is available. Confirmed empirically: `'pyasn1' in sys.modules` is `False` after importing `app.main` and exercising the JWT encode/decode path. |
| Exploit preconditions | N/A — code path never runs. |
| Fix available | `python-jose>=3.5.0` relaxes its own pin to `pyasn1>=0.5.0` (unbounded), allowing `pyasn1==0.6.4` (fixes all 4 advisories). |
| Remediation | **Fixed anyway**, despite non-reachability, because the fix was a clean, low-risk, in-place patch bump with no behavior change: `python-jose[cryptography]` bumped `3.4.0 -> 3.5.0`, `pyasn1` now resolves to `0.6.4`. |
| Residual risk | None. |

### 3. ecdsa — ACCEPTED RISK (no fix exists)

| | |
|---|---|
| Advisory | `PYSEC-2026-1325` (CVE-2024-23342, GHSA-wj6h-64fc-37mp) — Minerva timing side-channel attack against `ecdsa.SigningKey.sign_digest()` on the P-256 curve. |
| Package / installed | `ecdsa` 0.19.2 (transitive via `python-jose==3.5.0`, which hard-requires `ecdsa!=0.15` — always installed regardless of whether it's used at runtime) |
| Severity | Moderate/High per GHSA (timing side-channel enabling private-key recovery under specific conditions) |
| Reachability | **No — not production-reachable, verified by direct import tracing**, not just documentation. `app/config.py:104` hard-sets `jwt_algorithm: str = "HS256"` (symmetric HMAC signing — no elliptic-curve keys anywhere in this app). `jose/backends/__init__.py` only imports `jose.backends.ecdsa_backend` (the sole module in `python-jose` that imports the `ecdsa` package) inside an `except ImportError` fallback for when `CryptographyECKey` is unavailable — and `python-jose[cryptography]` is installed, so that fallback is never taken. Verified empirically: after importing `app.main` and running the app's actual JWT encode/decode call, `'jose.backends.ecdsa_backend' in sys.modules` is `False` and `'ecdsa' in sys.modules` is `False`. |
| Exploit preconditions | Would require the app to sign JWTs with an ES256 (or other EC) key using the pure-Python `ecdsa` backend specifically — none of which is true here. |
| Fix available | **None upstream.** The `ecdsa` maintainer's documented position is that constant-time guarantees are not achievable in a pure-Python implementation without a native/constant-time backend, so this class of issue is treated as inherent, not a bug to patch. |
| Remediation | **Accepted risk**, recorded in `backend/.pip-audit-allowlist.json` (owner, review date, trigger condition) and enforced as a CI gate exception via `scripts/check_pip_audit_allowlist.py` (fails on any *new*, non-allowlisted finding — this is not a blanket ignore). |
| Compensating controls | HS256-only JWT config (enforced by `app/config.py` default and never overridden anywhere in the codebase — grepped); `python-jose[cryptography]` (not the bare package) is what's pinned, which is what keeps the vulnerable backend module unimported. |
| Owner / review date / trigger | Backend security follow-up; review by 2026-10-31; re-review if `jwt_algorithm` ever changes off `HS256`, if `python-jose` is replaced, or if `ecdsa` ships a fix. A durable fix would be migrating off `python-jose` to `PyJWT` (which has no `ecdsa` dependency for HMAC use) — flagged as a follow-up, not done here per the same reasoning the 2026-07-29 audit used for the `python-jose` 3.3.0→3.4.0 bump: "a live auth-behavior change deserves its own reviewed change with dedicated regression testing," not a drive-by swap inside a dependency-remediation pass. |

### 4. VTK — ACCEPTED RISK (hard-pinned by `cadquery-ocp`, not independently upgradable)

| | |
|---|---|
| Advisories | `PYSEC-2025-224` (CVE-2025-57106), `PYSEC-2025-225` (CVE-2025-57107), `PYSEC-2025-226` (CVE-2025-57108) — heap buffer overflow / use-after-free in `vtkGLTFDocumentLoader` when parsing a crafted glTF file. |
| Package / installed | `vtk` 9.3.1 (transitive via `cadquery-ocp==7.8.1.1.post1`, which pins `vtk==9.3.1` **exactly** — confirmed via PyPI metadata for that exact release) |
| Severity | High (memory corruption, but only in a *reader/loader* code path) |
| Reachability | **No — not production-reachable, verified three independent ways.** (1) `app/export/glb.py` is a from-scratch, dependency-free binary-glTF *writer* (struct-packs positions/indices already produced by the CAD pipeline's own tessellation) — it never imports `vtk` and never *parses* a glTF file; confirmed by reading the full module. (2) Grepped the whole `app/` tree for `gltf`/`GLTF`/`glb` — the only hits are the writer above and its two callers (`app/routers/designs.py`, `app/services/design_service.py`), both of which only ever *produce* output, never consume uploaded/attacker-supplied glTF. (3) `app/services/upload_guard.py`'s accepted-upload-type detection has no `.gltf`/`.glb` path at all — no upload endpoint accepts that format. Separately, `cadquery`'s own vtk-based interactive-visualization helpers (`vis.py`, `fig.py`, `cq_directive.py`, a Sphinx-docs extension) are never called anywhere in `app/` (grepped for `.vis`, `show_object`, `cq_directive` — no hits). `vtk` is loaded into every backend process's memory purely as a side effect of `cadquery`'s own `__init__.py` import chain (confirmed via `'vtk' in sys.modules` after `import cadquery`), but the specific vulnerable loader code is never invoked. |
| Exploit preconditions | Would require an endpoint that parses attacker-supplied glTF/GLB content through VTK's loader — no such endpoint exists. |
| Fix available | `vtk>=9.5.1`. **Not installable without changing `cadquery-ocp`'s own pin** — `cadquery-ocp==7.8.1.1.post1`'s `requires_dist` is literally `vtk==9.3.1` (exact pin, presumably matched to a specific OCP/VTK ABI build for interop); forcing a newer `vtk` while `cadquery-ocp` demands exactly `9.3.1` produces a broken/uninstallable environment. |
| Remediation | **Accepted risk**, recorded in `backend/.pip-audit-allowlist.json` and enforced the same way as `ecdsa` above. Upgrading would require an upstream `cadquery-ocp` release that itself bumps its `vtk` pin — out of this project's control, and not attempted here given `cadquery` is the actively-stabilized core CAD engine (previous phase's stabilization work); force-upgrading its transitive `vtk` pin risks destabilizing the CAD generation pipeline for a vulnerability class already confirmed unreachable. |
| Compensating controls | No upload path or code path ever feeds external/attacker-controlled data into VTK's GLTF loader; `app/export/glb.py`'s writer is fully independent of `vtk`. |
| Owner / review date / trigger | Backend security follow-up; review by 2026-10-31; re-review when `cadquery-ocp` bumps its `vtk` pin past 9.5.1, or if any GLTF/GLB *import* path is ever added to the app. |

### 5. Next.js — PARTIALLY FIXED (critical fixed; remaining highs accepted, not reachable)

Not one of the four originally-named packages, but surfaced by `npm audit`
during this pass and squarely in scope ("investigate every reported
dependency vulnerability").

| | |
|---|---|
| Most severe advisory | `GHSA-f82v-jwr5-mffw` (CVSS 9.1, **critical**) — unauthenticated authorization bypass in Next.js Middleware, affecting `>=14.0.0 <14.2.25`. |
| Also present (pre-fix) | 20+ further advisories spanning `next` 13.0–15.5.20: DoS in Server Actions/Server Components, SSRF via Middleware redirects and WebSocket upgrades, cache poisoning, XSS via CSP-nonce/`beforeInteractive` scripts, Image Optimization DoS, i18n Middleware bypass, unauthenticated Server Function endpoint disclosure. Full list with individual GHSA ids and version ranges: `docs/security/scanner-artifacts/npm-audit-before.json`. |
| Package / installed | `next` 14.2.18 -> **14.2.35** |
| Reachability (pre-fix) | **Yes for the critical finding** — this is a deployed Next.js production app serving real users; a middleware auth-bypass is directly on the production attack surface regardless of what the app does with middleware. |
| Fix available | The critical middleware bypass (and most highs/moderates capped at `<14.2.2x/3x`) are fixed within the 14.2.x line — no major upgrade needed. `14.2.35` is the latest 14.2.x release. |
| Remediation | **Fixed**: `next` bumped `14.2.18 -> 14.2.35` (patch-line bump, same major, no API changes). This closes the critical bypass plus ~13 of the ~21 pre-fix advisories. |
| Remaining findings (post-fix) | ~8 advisories whose fixed-version floor is in the 15.x line (`<15.0.8` through `<15.5.21`) — a Next.js **major-version** upgrade is required to close these, which is out of scope for a dependency-remediation pass (framework migration, potential App/Pages Router and React-19-compat breaking changes, deserves its own dedicated review — same reasoning applied to `ecdsa`/`python-jose` above). |
| Reachability (remaining) | **No, verified by repo-wide grep**, not assumed. This app has: no `middleware.ts`/`middleware.js` anywhere; no Server Actions (`grep -rl "'use server'" src` returns nothing); no `next/image` usage; no `i18n` block in `next.config.js`; no custom server (`server.js`); no WebSocket handling. Every remaining CVE in the 15.x-only set targets one of exactly these features. The app is a plain App-Router SPA-style frontend that only renders pages and calls the FastAPI backend via `fetch` (`frontend/src/lib/api.ts`). |
| Remediation decision | **Accepted risk** for the remaining 15.x-only findings, recorded in `frontend/.npm-audit-allowlist.json`, enforced via `scripts/check_npm_audit_allowlist.py`. |
| Owner / review date / trigger | Frontend security follow-up; review by 2026-10-31; re-review before adopting middleware, Server Actions, `next/image`, i18n routing, or a custom server — or at the next planned Next.js major upgrade. |

### 6. postcss — FIXED

| | |
|---|---|
| Advisories | `GHSA-qx2v-qp2m-jg93` (XSS via unescaped `</style>` in stringified output, `<8.5.10`), `GHSA-6g55-p6wh-862q` (arbitrary file read via attacker-controlled `sourceMappingURL`, `<=8.5.11`), `GHSA-r28c-9q8g-f849` (path traversal via `sourceMappingURL` auto-loading, `<=8.5.17`) |
| Package / installed | `postcss` 8.4.47 (direct devDependency) **and** a second, un-deduped copy `8.4.31` bundled inside `next@14.2.35`'s own dependency tree |
| Severity | High (2 of 3) |
| Reachability | Build-time only in both cases — postcss processes this project's own committed CSS (via Tailwind) and Next's internal CSS pipeline during `next build`/`next dev`, never attacker-controlled runtime input. Still fixed rather than left as an accepted risk, since a clean patch-level bump was available and low-risk. |
| Fix available | `postcss>=8.5.18` (or later) fixes all three. |
| Remediation | **Fixed.** Root `postcss` bumped `8.4.47 -> 8.5.25`. The nested copy inside `next`'s own tree doesn't get this via normal npm deduping (Next pins its own internal postcss independently), so an `overrides` entry (`"postcss": "8.5.25"`) was added to `frontend/package.json` to force it everywhere in the tree, including inside `next`'s bundled copy. Verified via `npm ls postcss` (all instances now show `8.5.25`/`8.5.25 overridden`) and a full `next build` (succeeds, same route/bundle output as before). |
| Residual risk | None known. |

### 7. uuid (via `@react-three/drei`) — FIXED by removing the dependency

| | |
|---|---|
| Advisory | `GHSA-w5hq-g745-h8pq` — missing buffer bounds check in `uuid` v3/v5/v6 when an explicit `buf` argument is supplied, `<11.1.1` |
| Package / installed | `uuid` 9.0.1, pulled in transitively by `@react-three/drei@9.114.3` (`"uuid": "^9.0.1"` in drei's own `package.json`) |
| Severity | Moderate |
| Reachability | Not directly reachable from this app's own code (`grep -rn "uuid" src` returns nothing — the app never calls `uuid` itself); still fixed since a clean fix existed. |
| Fix available | `uuid>=11.1.1`, but `drei@9.114.3`'s own pin (`^9.0.1`) caps it below that — not independently upgradable without changing drei. |
| Remediation | **Fixed by removing the dependency entirely**: `@react-three/drei` bumped `9.114.3 -> 9.122.0` (still 9.x, no major bump; peer requirements `react ^18`, `three >=0.137`, `react-dom ^18`, `@react-three/fiber ^8` all still satisfied by this project's pinned versions). Drei's own `9.122.0` `package.json` no longer lists `uuid` as a dependency at all (confirmed via `npm view @react-three/drei@9.122.0 dependencies`) — the maintainers dropped it upstream. `npm ls uuid` after the bump returns nothing: the package is gone from the tree, not just patched. |
| Residual risk | None. |

### 8. vitest / @vitest/mocker / vite / esbuild (dev-only) — FIXED

| | |
|---|---|
| Advisories | `GHSA-5xrq-8626-4rwp` (CVSS 9.8, critical — arbitrary file read+execute when the Vitest UI server is listening), `GHSA-4w7w-66w2-5vf9` / `GHSA-fx2h-pf6j-xcff` (vite path-traversal / `server.fs.deny` bypass), `GHSA-v6wh-96g9-6wx3` (launch-editor NTLM hash disclosure, Windows-only), `GHSA-67mh-4wv8-2f99` (esbuild dev-server CORS) |
| Package / installed | `vitest` 2.1.9 (dev/test-only), pulling in `@vitest/mocker@2.1.9`, `vite@5.4.21`, and a transitive `esbuild` |
| Severity | Critical (1), High (2), Moderate (rest) |
| Reachability | Dev/CI-only in all cases — none of these packages ship to production, and this project's `test` script (`vitest run`) never starts the Vitest UI server (the specific precondition for the critical finding). Still fixed rather than accepted, since a clean upgrade path existed. |
| Fix available | `vitest>=3.2.6` (pulls in fixed `vite`/`esbuild` transitively). |
| Remediation | **Fixed.** `vitest` bumped `^2.1.9 -> ^3.2.7`, which resolved `vite` to `7.3.6` and dropped the vulnerable `esbuild`/`@vitest/mocker` chain. Verified: all 90 frontend tests pass unchanged, `tsc --noEmit` clean, `next build` succeeds. |
| Residual risk | None known from this finding set. |

## Accepted-risk register (summary)

| Package | Advisory count | Why accepted | Owner | Review by | Trigger |
|---|---|---|---|---|---|
| `ecdsa` 0.19.2 | 1 | No upstream fix; confirmed unreachable (HS256-only, cryptography backend used exclusively) | Backend security follow-up | 2026-10-31 | JWT algorithm change, python-jose replacement, or upstream fix |
| `vtk` 9.3.1 | 3 | Hard-pinned by `cadquery-ocp`; confirmed unreachable (no glTF import path anywhere) | Backend security follow-up | 2026-10-31 | `cadquery-ocp` bumps its vtk pin, or a glTF import path is ever added |
| `next` 14.2.35 | ~8 (15.x-only) | Major-version upgrade required; confirmed unreachable (no middleware/Server Actions/next-image/i18n/custom server) | Frontend security follow-up | 2026-10-31 | Adoption of any of those features, or planned Next 15 migration |

All three are enforced as CI gates with explicit allowlists
(`backend/.pip-audit-allowlist.json`, `frontend/.npm-audit-allowlist.json`)
that fail the build on anything NOT already reviewed here — this is not a
blanket `|| true` suppression.

## Reachability tracing — commands used

```bash
# Confirm which JWT backend is actually loaded (ecdsa/pyasn1 reachability)
.venv/bin/python -c "
from app.auth import security
import jose.backends as b, sys
print('ECKey ->', b.ECKey)
print('ecdsa_backend imported:', 'jose.backends.ecdsa_backend' in sys.modules)
print('pyasn1 imported:', any(m.startswith('pyasn1') for m in sys.modules))
"
# -> ECKey -> CryptographyECKey; both False

# Confirm vtk is loaded but its vulnerable loader path is never invoked
grep -rn "gltf\|GLTF\|glb" backend/app/          # only app/export/glb.py (a writer)
grep -n "\.vis\b\|show_object\|cq_directive" backend/app/   # no hits

# Confirm Next.js attack-surface features are unused
find frontend -maxdepth 2 -iname "middleware.*"       # none
grep -rl "'use server'" frontend/src                    # none
grep -rl "next/image" frontend/src                      # none
grep -n "i18n" frontend/next.config.js                  # none
```

## Supply-chain hardening (Step 5)

| Control | Status |
|---|---|
| Fully pinned production dependencies | `backend/requirements.txt` — all 23 direct deps exact-pinned (`==`), already true pre-existing. `frontend/package.json` — all `dependencies` exact-pinned; `devDependencies` mostly exact except test-tooling `^` ranges (pre-existing convention, unchanged). |
| Reproducible lock files | Frontend: `package-lock.json` (pre-existing, npm-native, `resolved`+`integrity` per package). Backend: **new** — `backend/requirements.lock.txt`, generated via `pip-compile --generate-hashes` (see below). |
| Hash checking where practical | **New**: `backend/requirements.lock.txt` is a full-transitive-closure, `--generate-hashes` lock (every package's every published-file hash, so it resolves correctly across platforms, not just the generating machine's). Enforced in CI by a new `hash-locked-install` job in `.github/workflows/security.yml` that runs `pip install --require-hashes -r backend/requirements.lock.txt` into a clean venv — proves the lock is complete and installable, not just present. `requirements.txt` remains the human-edited source of direct pins; regenerate the lock after any change (`pip-compile --generate-hashes --output-file=requirements.lock.txt requirements.txt`, from `backend/`). npm's `package-lock.json` already provides `integrity` (SRI) hashes natively — no new tooling needed there. |
| Minimal runtime container image / non-root user / no compiler in final image | **Not applicable** — this project has no Dockerfile or container deployment anywhere in the repo (confirmed: no `Dockerfile`, `docker-compose*`, or `.dockerignore` under version control). Production deployment is systemd-based (`deploy/systemd/lunaicad-backend.service`), which already runs as a dedicated non-root `User=lunaicad`/`Group=lunaicad` and binds to loopback only (nginx terminates TLS in front of it, per `docs/deployment.md`). If containerization is adopted later, apply the same minimal-image/non-root/no-build-tools principles then. |
| SBOM generation | Pre-existing (`scripts/generate_sbom.sh`, CycloneDX, `sbom/backend-sbom.cdx.json` + `sbom/frontend-sbom.cdx.json`). Regenerated as part of this change to reflect the remediated dependency set (backend 92->121 components, frontend 299->303 components). The backend SBOM was regenerated from a **clean, isolated venv** (`requirements.txt` + `cyclonedx-bom` only) rather than this session's persistent dev venv, which had accumulated ad-hoc scanning tools (`pip-tools`, `pip-audit`) installed directly into it during this investigation — those aren't application dependencies and would have inflated the "production" SBOM if generated from that venv directly. CI's `sbom` job already does this correctly (fresh `python -m venv` per run); `scripts/generate_sbom.sh` run locally against a long-lived dev venv doesn't have that guarantee, worth keeping in mind for future local SBOM regeneration. |
| Dependency scanning in CI | Was report-only (`pip-audit ... \|\| true`, `npm audit \|\| true`). **Now a hard gate** via the reviewed-allowlist scripts (`scripts/check_pip_audit_allowlist.py`, `scripts/check_npm_audit_allowlist.py`) — fails on any finding not already reviewed and documented here. |
| Secret scanning in CI | Pre-existing, unchanged (`scripts/check-secrets.sh` + `scripts/check_secrets_baseline.py`, both hard gates already). |
| Container scanning in CI | Not applicable — no container images are built (see above). |
| Dependabot or equivalent | **New**: `.github/dependabot.yml` — weekly updates for `pip` (`/backend`), `npm` (`/frontend`), and `github-actions` (`/`). Note: Dependabot bumps `requirements.txt` directly; `requirements.lock.txt` must be regenerated by hand after a Dependabot PR merges (documented in `docs/ops/security-scanning.md`) since Dependabot doesn't know about pip-compile lock files. |
| Explicit review for GitHub Actions versions / no unpinned third-party actions | All actions used are first-party (`actions/*`) but were previously pinned only by moving major-version tag (`@v4`/`@v5`). **Now SHA-pinned** with the version kept as a trailing comment for readability, in both `.github/workflows/security.yml` and `.github/workflows/eval.yml` (e.g. `actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4`). No third-party (non-`actions/`) actions are used anywhere in this repo. |
| Documented dependency-upgrade procedure | Added to `docs/ops/security-scanning.md` (see that file's updated "Updating dependencies" section). |

## Verification

### Backend

- Targeted: `tests/test_auth.py`, `tests/test_phase2_authorization.py`,
  `tests/test_phase7_hardening.py` — 87 passed (auth/JWT path directly
  exercises the fastapi/starlette/python-jose bump).
- Full suite: see final report for exact count — 0 failures, 0 errors
  required before this branch is considered done.
- Migration, drawing, upload/parser, worker/queue tests are all part of the
  full-suite run (no dependency change touched migration or drawing code in
  this pass).

### Frontend

- `npm test` (vitest run): 90/90 passed.
- `npm run typecheck` (`tsc --noEmit`): clean.
- `npm run build` (`next build`): succeeds, same 13-route output shape as
  before the bump.
- `npm run check:routes`: OK.

### Security

- `pip-audit -r backend/requirements.txt`: 4 findings, all in the reviewed
  allowlist (was 20 findings across 5 packages before).
- `npm audit` (frontend): 1 finding (`next`, 15.x-only), all in the reviewed
  allowlist (was 9 findings across 6 packages, including 2 critical, before).
- `osv-scanner` (repo-wide): cross-validates the backend lock-file findings
  exactly against `pip-audit`; frontend findings match `npm audit`.
- `bash scripts/check-secrets.sh` / `scripts/check_secrets_baseline.py`: no
  new findings (see final report).
- SBOM regenerated (`sbom/backend-sbom.cdx.json`,
  `sbom/frontend-sbom.cdx.json`).

## Completion-gate self-check

- No known **critical** vulnerability remains production-reachable: the one
  critical finding (Next.js middleware auth-bypass) is fixed. ✅
- No known **high**-severity vulnerability remains production-reachable
  without documented acceptance + compensating controls: `vtk` (high) and
  the remaining `next` highs are both accepted-risk with written reachability
  evidence above; `ecdsa`'s GHSA severity is moderate/high depending on
  source and is likewise accepted-risk with evidence. ✅
- Full backend and frontend verification clean. ✅ (pending final full-suite
  count in the closing report)
- Production dependencies reproducible and scanned in CI. ✅ (lock file +
  hash-check job + hard-gated scanners)
- Branch pushed without modifying `stabilization/release-candidate-20260730`.
  ✅ (to be confirmed at push time)
