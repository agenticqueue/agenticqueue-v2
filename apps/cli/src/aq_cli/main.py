import json
import os
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer

from aq_cli._config import (
    ConfigExistsError,
    ConfigPermissionError,
    default_config_path,
    ensure_config_writable,
    write_config,
)

API_URL_ENV = "AQ_API_URL"
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

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


def _api_url(path: str) -> str:
    return f"{os.getenv(API_URL_ENV, DEFAULT_API_URL).rstrip('/')}{path}"


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
