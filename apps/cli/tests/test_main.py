import json
import os
import stat
from pathlib import Path

import httpx
from aq_cli.main import API_KEY_ENV, API_URL_ENV, app
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


def test_whoami_sends_bearer_and_prints_raw_json(monkeypatch: MonkeyPatch) -> None:
    body = (
        '{"actor":{"id":"11111111-1111-4111-8111-111111111111",'
        '"name":"founder","kind":"human",'
        '"created_at":"2026-04-27T01:00:00Z","deactivated_at":null}}'
    )
    calls: list[tuple[str, dict[str, str], float]] = []

    def fake_get(
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        calls.append((url, headers, timeout))
        return _json_response(url, body)

    monkeypatch.setattr("aq_cli.main.httpx.get", fake_get)

    result = runner.invoke(
        app,
        ["whoami"],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            "http://api.test/actors/me",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            5.0,
        )
    ]


def test_actor_list_sends_query_params(monkeypatch: MonkeyPatch) -> None:
    body = '{"actors":[],"next_cursor":"cursor-2"}'
    calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def fake_get(
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        calls.append((url, headers, params, timeout))
        return _json_response(url, body)

    monkeypatch.setattr("aq_cli.main.httpx.get", fake_get)

    result = runner.invoke(
        app,
        [
            "actor",
            "list",
            "--limit",
            "2",
            "--cursor",
            "cursor-1",
            "--include-deactivated",
        ],
        env={API_URL_ENV: "http://api.test/", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            "http://api.test/actors",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {
                "limit": 2,
                "cursor": "cursor-1",
                "include_deactivated": True,
            },
            5.0,
        )
    ]


def test_actor_create_posts_body_and_prints_one_shot_key(
    monkeypatch: MonkeyPatch,
) -> None:
    body = (
        '{"actor":{"id":"22222222-2222-4222-8222-222222222222",'
        '"name":"worker","kind":"agent",'
        '"created_at":"2026-04-27T01:00:00Z","deactivated_at":null},'
        '"api_key":{"id":"33333333-3333-4333-8333-333333333333",'
        '"actor_id":"22222222-2222-4222-8222-222222222222",'
        '"name":"default","prefix":"aq2_cont",'
        '"created_at":"2026-04-27T01:00:00Z","revoked_at":null},'
        '"key":"aq2_contract_plaintext_key"}'
    )
    calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        calls.append((url, headers, json, timeout))
        return _json_response(url, body, method="POST")

    monkeypatch.setattr("aq_cli.main.httpx.post", fake_post)

    result = runner.invoke(
        app,
        ["actor", "create", "--name", "worker", "--kind", "agent"],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert json.loads(result.stdout)["key"] == "aq2_contract_plaintext_key"
    assert calls == [
        (
            "http://api.test/actors",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {"name": "worker", "kind": "agent", "key_name": "default"},
            10.0,
        )
    ]


def test_key_revoke_deletes_with_bearer_and_prints_raw_json(
    monkeypatch: MonkeyPatch,
) -> None:
    api_key_id = "33333333-3333-4333-8333-333333333333"
    body = (
        '{"api_key":{"id":"33333333-3333-4333-8333-333333333333",'
        '"actor_id":"22222222-2222-4222-8222-222222222222",'
        '"name":"default","prefix":"aq2_cont",'
        '"created_at":"2026-04-27T01:00:00Z",'
        '"revoked_at":"2026-04-27T02:00:00Z"}}'
    )
    calls: list[tuple[str, dict[str, str], float]] = []

    def fake_delete(
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        calls.append((url, headers, timeout))
        return _json_response(url, body, method="DELETE")

    monkeypatch.setattr("aq_cli.main.httpx.delete", fake_delete)

    result = runner.invoke(
        app,
        ["key", "revoke", api_key_id],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            f"http://api.test/api-keys/{api_key_id}",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            5.0,
        )
    ]
