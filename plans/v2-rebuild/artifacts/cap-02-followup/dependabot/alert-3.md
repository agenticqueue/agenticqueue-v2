# Alert #3 - FastMCP Command Injection vulnerability - Gemini CLI

- Severity: MODERATE
- Dependency: fastmcp
- Ecosystem: pip
- Manifest: uv.lock
- Dependabot alert: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/3
- GHSA advisory: https://github.com/advisories/GHSA-m8x7-r2rg-vh5g
- Vulnerable range: < 3.2.0
- First patched version: 3.2.0

## Audit Method

Gemini CLI grep requested by AQ2-38:

```text
$ grep -rn "Gemini\|google_genai\|gemini_cli" apps/ tests/ scripts/ --include="*.py"
GREP_EXIT_CODE:1
```

There are no Gemini CLI or Google GenAI integration references in the Python app, test, or script paths.

## Decision

Dismissed as vulnerable code path not invoked.

GitHub dismissal URL: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/3

Dismissal comment:

```text
No FastMCP Gemini CLI integration invoked. Grep: no Gemini/google_genai/gemini_cli references. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-3.md
```

GitHub alert state verification:

```json
{"dismissed_comment":"No FastMCP Gemini CLI integration invoked. Grep: no Gemini/google_genai/gemini_cli references. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-3.md","dismissed_reason":"not_used","html_url":"https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/3","number":3,"state":"dismissed"}
```
