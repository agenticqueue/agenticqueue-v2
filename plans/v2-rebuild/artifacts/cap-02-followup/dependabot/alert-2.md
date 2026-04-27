# Alert #2 - DiskCache has unsafe pickle deserialization

- Severity: MODERATE
- Dependency: diskcache
- Ecosystem: pip
- Manifest: uv.lock
- Dependabot alert: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/2
- GHSA advisory: https://github.com/advisories/GHSA-w8v5-vhqr-4h9v
- Vulnerable range: <= 5.6.3
- First patched version: none listed by Dependabot

## Audit Method

Dependency parent path and app/test/script grep output:

```text
===== uv tree parent path =====
COMMAND: uv tree | grep -B2 diskcache
│   │   │   ├── beartype v0.22.9
│   │   │   └── typing-extensions v4.15.0
│   │   ├── diskcache v5.6.3 (extra: disk)
EXIT_CODE: 0
===== source grep =====
COMMAND: grep -rn "diskcache\|Cache(" apps/ tests/ scripts/ --include="*.py"
EXIT_CODE: 1
===== uv tree inverted diskcache path =====
COMMAND: uv tree --invert --package diskcache
diskcache v5.6.3
Resolved 111 packages in 2ms
└── py-key-value-aio v0.3.0 (extra: disk)
    ├── fastmcp[disk, keyring, memory] v2.14.7
    │   └── aq-api v0.1.0
    │       └── agenticqueue-v2 v0.1.0
    └── pydocket[memory, redis] v0.18.2
        └── fastmcp v2.14.7 (*)
(*) Package tree already displayed
EXIT_CODE: 0
```

`diskcache` is present transitively through FastMCP's `disk` extra path (`py-key-value-aio`, also under `pydocket`), but application, test, and script code has zero direct `diskcache` or `Cache(` call sites.

## Decision

Dismissed as vulnerable code path not invoked.

GitHub dismissal URL: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/2

Dismissal comment:

```text
diskcache is transitive via FastMCP disk extra; app/test/script grep shows no diskcache or Cache( call sites. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-2.md
```

GitHub alert state verification:

```json
{"dismissed_comment":"diskcache is transitive via FastMCP disk extra; app/test/script grep shows no diskcache or Cache( call sites. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-2.md","dismissed_reason":"not_used","html_url":"https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/2","number":2,"state":"dismissed"}
```
