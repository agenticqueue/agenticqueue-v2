# Alert #1 - PostCSS XSS via Unescaped </style> in CSS Stringify Output

- Severity: MODERATE
- Dependency: postcss
- Ecosystem: npm
- Manifest: pnpm-lock.yaml
- Dependabot alert: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/1
- GHSA advisory: https://github.com/advisories/GHSA-qx2v-qp2m-jg93
- Vulnerable range: < 8.5.10
- First patched version: 8.5.10

## Audit Method

`postcss` is a direct `apps/web` dev dependency used by the Next/Tailwind build pipeline. The cheap safe path is a direct package bump, not a dismissal.

Diff stat:

```text
 apps/web/package.json | 2 +-  pnpm-lock.yaml        | 2 +-  2 files changed, 2 insertions(+), 2 deletions(-)
```

## Verification

Web build and lint output, verbatim:

```text
===== apps/web build =====
COMMAND: pnpm --filter @agenticqueue/web build

> @agenticqueue/web@0.1.0 build D:\mmmmm\mmmmm-aq2.0-cap02-work\apps\web
> pnpm gen:types && next build


> @agenticqueue/web@0.1.0 gen:types D:\mmmmm\mmmmm-aq2.0-cap02-work\apps\web
> openapi-typescript ../../tests/parity/openapi.snapshot.json -o app/types/api.ts

✨ openapi-typescript 7.13.0
🚀 ../../tests/parity/openapi.snapshot.json → app/types/api.ts [57.5ms]
   ▲ Next.js 15.5.15

   Creating an optimized production build ...
 ✓ Compiled successfully in 2.4s
   Linting and checking validity of types ...
   Collecting page data ...
   Generating static pages (0/3) ...
 ✓ Generating static pages (3/3)
   Finalizing page optimization ...
   Collecting build traces ...

Route (app)                                 Size  First Load JS
┌ ƒ /                                      141 B         102 kB
├ ○ /_not-found                            991 B         103 kB
├ ƒ /api/actors/me                         141 B         102 kB
├ ƒ /api/health                            141 B         102 kB
├ ƒ /api/version                           141 B         102 kB
├ ƒ /login                                 141 B         102 kB
├ ƒ /logout                                141 B         102 kB
└ ƒ /whoami                                141 B         102 kB
+ First Load JS shared by all             102 kB
  ├ chunks/40431421-4ca4396edd5d645a.js  54.2 kB
  ├ chunks/628-d761323f3500a9ba.js         46 kB
  └ other shared chunks (total)          1.97 kB


ƒ Middleware                               43 kB

○  (Static)   prerendered as static content
ƒ  (Dynamic)  server-rendered on demand

EXIT_CODE: 0
===== apps/web lint =====
COMMAND: pnpm --filter @agenticqueue/web lint

> @agenticqueue/web@0.1.0 lint D:\mmmmm\mmmmm-aq2.0-cap02-work\apps\web
> pnpm gen:types && eslint .


> @agenticqueue/web@0.1.0 gen:types D:\mmmmm\mmmmm-aq2.0-cap02-work\apps\web
> openapi-typescript ../../tests/parity/openapi.snapshot.json -o app/types/api.ts

✨ openapi-typescript 7.13.0
🚀 ../../tests/parity/openapi.snapshot.json → app/types/api.ts [43.2ms]
EXIT_CODE: 0
```

## Decision

Bumped to patched range `postcss ^8.5.10`; `pnpm-lock.yaml` resolves `postcss 8.5.12`.

GitHub API note: Dependabot's REST API does not accept dismissal reason `fixed`. This alert was dismissed with `fix_started` and will be closed by GitHub when the PR merges.

GitHub dismissal URL: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/1

Dismissal comment:

```text
Fix started on branch aq2-cap-02-dependabot-triage by bumping postcss to ^8.5.10 (lock resolves 8.5.12). Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-1.md
```

GitHub alert state verification:

```json
{"dismissed_comment":"Fix started on branch aq2-cap-02-dependabot-triage by bumping postcss to ^8.5.10 (lock resolves 8.5.12). Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-1.md","dismissed_reason":"fix_started","html_url":"https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/1","number":1,"state":"dismissed"}
```
