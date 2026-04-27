# Alert #4 - FastMCP OAuth Proxy Callback Confused Deputy

- Severity: HIGH
- Dependency: fastmcp
- Ecosystem: pip
- Manifest: uv.lock
- Dependabot alert: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/4
- GHSA advisory: https://github.com/advisories/GHSA-rww4-4w9c-7733
- Vulnerable range: < 3.2.0
- First patched version: 3.2.0

## Audit Method

FastMCP OAuth proxy grep requested by AQ2-38:

```text
$ grep -rn "OAuthProxy\|oauth_proxy\|oauth_callback" apps/ tests/ scripts/ --include="*.py" --include="*.ts" --include="*.tsx"
GREP_EXIT_CODE:1
```

Web auth implementation trace:

```text
$ grep -rn "iron-session\|getIronSession\|sealData\|unsealData" apps/web --include="*.ts" --include="*.tsx"
apps/web/app/lib/session.ts:1:import { getIronSession, type IronSession, type SessionOptions } from "iron-session";
apps/web/app/lib/session.ts:35:  return getIronSession<WebSessionData>(await cookies(), sessionOptions());
apps/web/app/lib/session.ts:42:  return getIronSession<WebSessionData>(request, response, sessionOptions());
GREP_EXIT_CODE:0
```

FastMCP/OAuth trace:

```text
$ grep -rn "FastMCP\|OAuth\|oauth" apps/api/src/aq_api apps/web --include="*.py" --include="*.ts" --include="*.tsx"
apps/api/src/aq_api/mcp.py:6:from fastmcp import FastMCP
apps/api/src/aq_api/mcp.py:66:def create_mcp_server() -> FastMCP:
apps/api/src/aq_api/mcp.py:68:    server = FastMCP(MCP_NAME, tasks=False)
apps/api/src/aq_api/mcp.py:209:    # FastMCP owns the exact /mcp path; app.py extends it to avoid redirects.
GREP_EXIT_CODE:0
```

Cap-02 web auth uses `iron-session`; no FastMCP OAuth proxy/callback symbols are referenced.

## Decision

Dismissed as vulnerable code path not invoked.

GitHub dismissal URL: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/4

Dismissal comment:

```text
No FastMCP OAuth proxy/callback invoked. Grep: no OAuthProxy/oauth_proxy/oauth_callback; web auth uses iron-session. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-4.md
```

GitHub alert state verification:

```json
{"dismissed_comment":"No FastMCP OAuth proxy/callback invoked. Grep: no OAuthProxy/oauth_proxy/oauth_callback; web auth uses iron-session. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-4.md","dismissed_reason":"not_used","html_url":"https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/4","number":4,"state":"dismissed"}
```
