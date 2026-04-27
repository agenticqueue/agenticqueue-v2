import json
import os
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer

from aq_cli._config import (
    ConfigExistsError,
    ConfigPermissionError,
    StoredConfig,
    default_config_path,
    ensure_config_writable,
    read_config,
    write_config,
)

API_URL_ENV = "AQ_API_URL"
API_KEY_ENV = "AQ_API_KEY"
DEFAULT_API_URL = "http://localhost:8001"
DEFAULT_TIMEOUT_SECONDS = 5.0

TimeoutOption = Annotated[float, typer.Option("--timeout", min=0.1)]
ConfigPathOption = Annotated[
    Path | None,
    typer.Option("--config", help="Path for the AQ TOML config."),
]
ForceOption = Annotated[
    bool,
    typer.Option("--force", help="Overwrite an existing AQ config."),
]
ActorKindOption = Annotated[
    str,
    typer.Option("--kind", help="Actor kind: human, agent, script, or routine."),
]

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
actor_app = typer.Typer(add_completion=False, help="Actor identity commands.")
key_app = typer.Typer(add_completion=False, help="API key commands.")


def _api_url(path: str) -> str:
    return f"{os.getenv(API_URL_ENV, DEFAULT_API_URL).rstrip('/')}{path}"


QueryParams = dict[str, str | int | float | bool | None]


def _load_config(config_path: Path | None) -> StoredConfig:
    path = config_path or default_config_path()
    try:
        return read_config(path)
    except (FileNotFoundError, ValueError, OSError) as exc:
        _fail("config_read_failed", str(path), type=type(exc).__name__)


def _authenticated_api_url(path: str, config_path: Path | None) -> str:
    api_url = os.getenv(API_URL_ENV)
    if api_url is None:
        try:
            api_url = read_config(config_path or default_config_path())["api_url"]
        except (FileNotFoundError, ValueError, OSError):
            api_url = DEFAULT_API_URL
    return f"{api_url.rstrip('/')}{path}"


def _auth_headers(config_path: Path | None) -> dict[str, str]:
    api_key = os.getenv(API_KEY_ENV)
    if api_key is None:
        api_key = _load_config(config_path)["api_key"]
    return {"Authorization": f"Bearer {api_key}"}


def _fail(error: str, url: str, **details: object) -> NoReturn:
    payload: dict[str, object] = {"error": error, "url": url}
    payload.update(details)
    typer.echo(json.dumps(payload, separators=(",", ":"), sort_keys=True), err=True)
    raise typer.Exit(code=1)


def _get(path: str, timeout: float) -> str:
    url = _api_url(path)
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.TimeoutException as exc:
        _fail("timeout", url, message=str(exc), type=type(exc).__name__)
    except httpx.HTTPError as exc:
        _fail("request_error", url, message=str(exc), type=type(exc).__name__)

    if not 200 <= response.status_code < 300:
        _fail(
            "http_error",
            url,
            status_code=response.status_code,
            body=response.text,
        )

    return response.text


def _get_auth(
    path: str,
    timeout: float,
    config_path: Path | None,
    *,
    params: QueryParams | None = None,
) -> str:
    url = _authenticated_api_url(path, config_path)
    try:
        if params is None:
            response = httpx.get(
                url,
                headers=_auth_headers(config_path),
                timeout=timeout,
            )
        else:
            response = httpx.get(
                url,
                headers=_auth_headers(config_path),
                params=params,
                timeout=timeout,
            )
    except httpx.TimeoutException as exc:
        _fail("timeout", url, message=str(exc), type=type(exc).__name__)
    except httpx.HTTPError as exc:
        _fail("request_error", url, message=str(exc), type=type(exc).__name__)

    if not 200 <= response.status_code < 300:
        _fail(
            "http_error",
            url,
            status_code=response.status_code,
            body=response.text,
        )

    return response.text


def _post(path: str, body: dict[str, object], timeout: float) -> str:
    url = _api_url(path)
    try:
        response = httpx.post(url, json=body, timeout=timeout)
    except httpx.TimeoutException as exc:
        _fail("timeout", url, message=str(exc), type=type(exc).__name__)
    except httpx.HTTPError as exc:
        _fail("request_error", url, message=str(exc), type=type(exc).__name__)

    if not 200 <= response.status_code < 300:
        _fail(
            "http_error",
            url,
            status_code=response.status_code,
            body=response.text,
        )

    return response.text


def _post_auth(
    path: str,
    body: dict[str, object],
    timeout: float,
    config_path: Path | None,
) -> str:
    url = _authenticated_api_url(path, config_path)
    try:
        response = httpx.post(
            url,
            headers=_auth_headers(config_path),
            json=body,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        _fail("timeout", url, message=str(exc), type=type(exc).__name__)
    except httpx.HTTPError as exc:
        _fail("request_error", url, message=str(exc), type=type(exc).__name__)

    if not 200 <= response.status_code < 300:
        _fail(
            "http_error",
            url,
            status_code=response.status_code,
            body=response.text,
        )

    return response.text


def _delete_auth(
    path: str,
    timeout: float,
    config_path: Path | None,
) -> str:
    url = _authenticated_api_url(path, config_path)
    try:
        response = httpx.delete(
            url,
            headers=_auth_headers(config_path),
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        _fail("timeout", url, message=str(exc), type=type(exc).__name__)
    except httpx.HTTPError as exc:
        _fail("request_error", url, message=str(exc), type=type(exc).__name__)

    if not 200 <= response.status_code < 300:
        _fail(
            "http_error",
            url,
            status_code=response.status_code,
            body=response.text,
        )

    return response.text


@app.command()
def health(timeout: TimeoutOption = DEFAULT_TIMEOUT_SECONDS) -> None:
    """Print the HealthStatus JSON payload."""
    typer.echo(_get("/healthz", timeout))


@app.command()
def version(timeout: TimeoutOption = DEFAULT_TIMEOUT_SECONDS) -> None:
    """Print the VersionInfo JSON payload."""
    typer.echo(_get("/version", timeout))


@app.command()
def setup(
    timeout: TimeoutOption = 10.0,
    config: ConfigPathOption = None,
    force: ForceOption = False,
) -> None:
    """Bootstrap the founder actor and write the local AQ config."""
    config_path = config or default_config_path()
    try:
        ensure_config_writable(config_path, force=force)
    except ConfigExistsError:
        _fail("config_exists", str(config_path))

    response_text = _post("/setup", {}, timeout)
    body = json.loads(response_text)
    actor_id = str(body["actor_id"])
    founder_key = str(body["founder_key"])

    try:
        write_config(
            config_path,
            api_url=os.getenv(API_URL_ENV, DEFAULT_API_URL).rstrip("/"),
            actor_id=actor_id,
            api_key=founder_key,
            force=force,
        )
    except (ConfigExistsError, ConfigPermissionError, OSError) as exc:
        _fail("config_write_failed", str(config_path), type=type(exc).__name__)

    typer.echo(
        json.dumps(
            {
                "actor_id": actor_id,
                "config_path": str(config_path),
            },
            separators=(",", ":"),
        )
    )


@app.command()
def whoami(
    timeout: TimeoutOption = DEFAULT_TIMEOUT_SECONDS,
    config: ConfigPathOption = None,
) -> None:
    """Print the authenticated Actor JSON payload."""
    typer.echo(_get_auth("/actors/me", timeout, config))


@app.command()
def audit(
    timeout: TimeoutOption = DEFAULT_TIMEOUT_SECONDS,
    config: ConfigPathOption = None,
    actor: Annotated[str | None, typer.Option("--actor")] = None,
    op: Annotated[str | None, typer.Option("--op")] = None,
    since: Annotated[str | None, typer.Option("--since")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1)] = 50,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print raw JSON; this is the default."),
    ] = False,
) -> None:
    """Print the filtered AuditLogPage JSON payload."""
    params: QueryParams = {"limit": limit}
    if actor is not None:
        params["actor"] = actor
    if op is not None:
        params["op"] = op
    if since is not None:
        params["since"] = since
    if until is not None:
        params["until"] = until
    if cursor is not None:
        params["cursor"] = cursor
    # JSON is the only output mode; --json is accepted for script compatibility.
    _ = json_output
    typer.echo(_get_auth("/audit", timeout, config, params=params))


@actor_app.command("list")
def actor_list(
    timeout: TimeoutOption = DEFAULT_TIMEOUT_SECONDS,
    config: ConfigPathOption = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 50,
    cursor: Annotated[str | None, typer.Option("--cursor")] = None,
    include_deactivated: Annotated[
        bool,
        typer.Option("--include-deactivated"),
    ] = False,
) -> None:
    """Print the paginated ListActorsResponse JSON payload."""
    params: QueryParams = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    if include_deactivated:
        params["include_deactivated"] = True
    typer.echo(_get_auth("/actors", timeout, config, params=params))


@actor_app.command("create")
def actor_create(
    name: Annotated[str, typer.Option("--name")],
    kind: ActorKindOption,
    timeout: TimeoutOption = 10.0,
    config: ConfigPathOption = None,
    key_name: Annotated[str, typer.Option("--key-name")] = "default",
) -> None:
    """Create an Actor and print its one-shot key response."""
    typer.echo(
        _post_auth(
            "/actors",
            {"name": name, "kind": kind, "key_name": key_name},
            timeout,
            config,
        )
    )


app.add_typer(actor_app, name="actor")


@key_app.command("revoke")
def key_revoke(
    api_key_id: Annotated[str, typer.Argument(help="API key UUID to revoke.")],
    timeout: TimeoutOption = DEFAULT_TIMEOUT_SECONDS,
    config: ConfigPathOption = None,
) -> None:
    """Revoke one of the authenticated Actor's API keys."""
    typer.echo(_delete_auth(f"/api-keys/{api_key_id}", timeout, config))


app.add_typer(key_app, name="key")
