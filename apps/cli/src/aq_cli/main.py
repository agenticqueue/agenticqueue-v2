import json
import os
from typing import Annotated, NoReturn

import httpx
import typer

API_URL_ENV = "AQ_API_URL"
DEFAULT_API_URL = "http://localhost:8001"
DEFAULT_TIMEOUT_SECONDS = 5.0

TimeoutOption = Annotated[float, typer.Option("--timeout", min=0.1)]

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


@app.command()
def health(timeout: TimeoutOption = DEFAULT_TIMEOUT_SECONDS) -> None:
    """Print the HealthStatus JSON payload."""
    typer.echo(_get("/healthz", timeout))


@app.command()
def version(timeout: TimeoutOption = DEFAULT_TIMEOUT_SECONDS) -> None:
    """Print the VersionInfo JSON payload."""
    typer.echo(_get("/version", timeout))
