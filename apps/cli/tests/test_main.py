import json
import os
import stat
from pathlib import Path

import httpx
from aq_cli.main import API_URL_ENV, app
from pytest import MonkeyPatch
from typer.testing import CliRunner

runner = CliRunner()


def _json_response(
    url: str,
    body: str,
    status_code: int = 200,
    method: str = "GET",
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=body.encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request(method, url),
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


def test_setup_posts_and_writes_redacted_cli_summary(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    body = (
        '{"actor_id":"11111111-1111-4111-8111-111111111111",'
        '"founder_key":"aq2_founder_contract_test_key"}'
    )
    calls: list[tuple[str, dict[str, object], float]] = []

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        calls.append((url, json, timeout))
        return _json_response(url, body, method="POST")

    def fake_restrict(path: object) -> None:
        Path(path).chmod(0o600)

    monkeypatch.setattr("aq_cli.main.httpx.post", fake_post)
    monkeypatch.setattr("aq_cli._config._restrict_file", fake_restrict)

    result = runner.invoke(
        app,
        ["setup", "--config", str(config_path)],
        env={API_URL_ENV: "http://api.test/"},
    )

    assert result.exit_code == 0
    assert calls == [("http://api.test/setup", {}, 10.0)]
    assert "founder_key" not in result.stdout
    assert "aq2_founder_contract_test_key" not in result.stdout
    assert json.loads(result.stdout) == {
        "actor_id": "11111111-1111-4111-8111-111111111111",
        "config_path": str(config_path),
    }
    assert config_path.read_text(encoding="utf-8") == (
        'api_url = "http://api.test"\n'
        'actor_id = "11111111-1111-4111-8111-111111111111"\n'
        'api_key = "aq2_founder_contract_test_key"\n'
    )
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) & 0o077 == 0


def test_setup_refuses_to_overwrite_existing_config(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("api_url = \"http://existing\"\n", encoding="utf-8")

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        raise AssertionError(f"unexpected setup call to {url}")

    monkeypatch.setattr("aq_cli.main.httpx.post", fake_post)

    result = runner.invoke(app, ["setup", "--config", str(config_path)])

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"] == "config_exists"
