import json

import httpx
from aq_cli.main import API_URL_ENV, app
from pytest import MonkeyPatch
from typer.testing import CliRunner

runner = CliRunner()


def _json_response(url: str, body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=body.encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", url),
    )


def test_health_prints_raw_json(monkeypatch: MonkeyPatch) -> None:
    body = '{"status":"ok","timestamp":"2026-04-26T18:00:00Z"}'
    calls: list[tuple[str, float]] = []

    def fake_get(url: str, *, timeout: float) -> httpx.Response:
        calls.append((url, timeout))
        return _json_response(url, body)

    monkeypatch.setattr("aq_cli.main.httpx.get", fake_get)

    result = runner.invoke(
        app,
        ["health"],
        env={API_URL_ENV: "http://api.test/"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [("http://api.test/healthz", 5.0)]
    assert json.loads(result.stdout)["status"] == "ok"


def test_version_prints_raw_json_with_timeout(monkeypatch: MonkeyPatch) -> None:
    body = '{"version":"0.1.0","commit":"abcdef0","built_at":"2026-04-26T18:00:00Z"}'
    calls: list[tuple[str, float]] = []

    def fake_get(url: str, *, timeout: float) -> httpx.Response:
        calls.append((url, timeout))
        return _json_response(url, body)

    monkeypatch.setattr("aq_cli.main.httpx.get", fake_get)

    result = runner.invoke(
        app,
        ["version", "--timeout", "1.25"],
        env={API_URL_ENV: "http://api.test"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [("http://api.test/version", 1.25)]
    assert {"version", "commit", "built_at"} <= json.loads(result.stdout).keys()


def test_network_error_prints_structured_json_to_stderr(
    monkeypatch: MonkeyPatch,
) -> None:
    def fake_get(url: str, *, timeout: float) -> httpx.Response:
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr("aq_cli.main.httpx.get", fake_get)

    result = runner.invoke(
        app,
        ["health"],
        env={API_URL_ENV: "http://localhost:9"},
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    error = json.loads(result.stderr)
    assert error["error"] == "request_error"
    assert error["type"] == "ConnectError"
    assert error["url"] == "http://localhost:9/healthz"
