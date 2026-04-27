# Alert #5 - FastMCP OpenAPI Provider has an SSRF & Path Traversal Vulnerability

- Severity: CRITICAL
- Dependency: fastmcp
- Ecosystem: pip
- Manifest: uv.lock
- Dependabot alert: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/5
- GHSA advisory: https://github.com/advisories/GHSA-vv7q-7jx5-f767
- Vulnerable range: < 3.2.0
- First patched version: 3.2.0

## Audit Method

Broad source grep requested by AQ2-38:

```text
$ grep -rn "OpenAPI\|from_openapi\|openapi_url" apps/ tests/ scripts/ --include="*.py"
apps/api/src/aq_api/app.py:21:# OpenAPI uses the same env-driven version path as the runtime `/version` surface.
GREP_EXIT_CODE:0
```

The only hit is a FastAPI OpenAPI metadata comment in `app.py`, not a FastMCP OpenAPI provider invocation.

FastMCP-specific provider grep:

```text
$ grep -rn "from_openapi\|openapi_url\|OpenAPIProvider" apps/ tests/ scripts/ --include="*.py"
GREP_EXIT_CODE:1
```

FastMCP server construction trace:

```text
$ grep -n "FastMCP\|@mcp.tool\|@server.tool\|from_openapi\|openapi_url\|OpenAPIProvider" apps/api/src/aq_api/mcp.py
6:from fastmcp import FastMCP
66:def create_mcp_server() -> FastMCP:
68:    server = FastMCP(MCP_NAME, tasks=False)
70:    @server.tool(
81:    @server.tool(
92:    @server.tool(
103:    @server.tool(
128:    @server.tool(
149:    @server.tool(
171:    @server.tool(
209:    # FastMCP owns the exact /mcp path; app.py extends it to avoid redirects.
GREP_EXIT_CODE:0
```

`apps/api/src/aq_api/mcp.py` hand-defines every MCP tool via `@server.tool`; it does not call FastMCP OpenAPI auto-import/provider APIs.

## Decision

Dismissed as vulnerable code path not invoked.

GitHub dismissal URL: https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/5

Dismissal comment:

```text
No FastMCP OpenAPI provider path invoked. Grep: no from_openapi/openapi_url/OpenAPIProvider; mcp.py hand-defines @server.tool. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-5.md
```

GitHub alert state verification:

```json
{"dismissed_comment":"No FastMCP OpenAPI provider path invoked. Grep: no from_openapi/openapi_url/OpenAPIProvider; mcp.py hand-defines @server.tool. Evidence: plans/v2-rebuild/artifacts/cap-02-followup/dependabot/alert-5.md","dismissed_reason":"not_used","html_url":"https://github.com/agenticqueue/agenticqueue-v2/security/dependabot/5","number":5,"state":"dismissed"}
```
