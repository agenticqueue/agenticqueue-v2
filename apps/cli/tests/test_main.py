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
        ["version", "--timeout", "1.25"],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            "http://api.test/version",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            1.25,
        )
    ]
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


def test_project_create_posts_body_and_prints_raw_json(
    monkeypatch: MonkeyPatch,
) -> None:
    body = (
        '{"project":{"id":"44444444-4444-4444-8444-444444444444",'
        '"name":"AQ 2.0 Backlog","slug":"aq-2-backlog",'
        '"description":"Project board","archived_at":null,'
        '"created_at":"2026-04-27T01:00:00Z",'
        '"created_by_actor_id":"11111111-1111-4111-8111-111111111111"}}'
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
        [
            "project",
            "create",
            "--name",
            "AQ 2.0 Backlog",
            "--slug",
            "aq-2-backlog",
            "--description",
            "Project board",
        ],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            "http://api.test/projects",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {
                "name": "AQ 2.0 Backlog",
                "slug": "aq-2-backlog",
                "description": "Project board",
            },
            10.0,
        )
    ]


def test_project_create_derives_slug_when_omitted(
    monkeypatch: MonkeyPatch,
) -> None:
    body = (
        '{"project":{"id":"44444444-4444-4444-8444-444444444444",'
        '"name":"AQ 2.0 Backlog","slug":"aq-2-0-backlog",'
        '"description":null,"archived_at":null,'
        '"created_at":"2026-04-27T01:00:00Z",'
        '"created_by_actor_id":"11111111-1111-4111-8111-111111111111"}}'
    )
    calls: list[dict[str, object]] = []

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        calls.append(json)
        return _json_response(url, body, method="POST")

    monkeypatch.setattr("aq_cli.main.httpx.post", fake_post)

    result = runner.invoke(
        app,
        ["project", "create", "--name", "AQ 2.0 Backlog"],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert calls == [{"name": "AQ 2.0 Backlog", "slug": "aq-2-0-backlog"}]


def test_project_list_sends_archived_query_params(
    monkeypatch: MonkeyPatch,
) -> None:
    body = '{"projects":[],"next_cursor":null}'
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
            "project",
            "list",
            "--limit",
            "10",
            "--cursor",
            "cursor-1",
            "--include-archived",
        ],
        env={API_URL_ENV: "http://api.test/", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            "http://api.test/projects",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {"limit": 10, "cursor": "cursor-1", "include_archived": True},
            5.0,
        )
    ]


def test_project_get_update_archive_use_expected_paths(
    monkeypatch: MonkeyPatch,
) -> None:
    project_id = "44444444-4444-4444-8444-444444444444"
    body = (
        '{"project":{"id":"44444444-4444-4444-8444-444444444444",'
        '"name":"AQ 2.0 Backlog","slug":"aq-2-backlog",'
        '"description":null,"archived_at":null,'
        '"created_at":"2026-04-27T01:00:00Z",'
        '"created_by_actor_id":"11111111-1111-4111-8111-111111111111"}}'
    )
    calls: list[tuple[str, str, dict[str, object] | None, float]] = []

    def fake_get(
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        assert headers == {"Authorization": "Bearer aq2_cli_contract_key"}
        calls.append(("GET", url, None, timeout))
        return _json_response(url, body)

    def fake_patch(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        assert headers == {"Authorization": "Bearer aq2_cli_contract_key"}
        calls.append(("PATCH", url, json, timeout))
        return _json_response(url, body, method="PATCH")

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        assert headers == {"Authorization": "Bearer aq2_cli_contract_key"}
        calls.append(("POST", url, json, timeout))
        return _json_response(url, body, method="POST")

    monkeypatch.setattr("aq_cli.main.httpx.get", fake_get)
    monkeypatch.setattr("aq_cli.main.httpx.patch", fake_patch)
    monkeypatch.setattr("aq_cli.main.httpx.post", fake_post)

    get_result = runner.invoke(
        app,
        ["project", "get", project_id],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )
    update_result = runner.invoke(
        app,
        ["project", "update", project_id, "--name", "AQ 2.0 Backlog"],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )
    archive_result = runner.invoke(
        app,
        ["project", "archive", project_id],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert get_result.exit_code == 0
    assert update_result.exit_code == 0
    assert archive_result.exit_code == 0
    assert calls == [
        ("GET", f"http://api.test/projects/{project_id}", None, 5.0),
        (
            "PATCH",
            f"http://api.test/projects/{project_id}",
            {"name": "AQ 2.0 Backlog"},
            10.0,
        ),
        ("POST", f"http://api.test/projects/{project_id}/archive", {}, 10.0),
    ]


def test_pipeline_create_and_list_use_expected_payloads(
    monkeypatch: MonkeyPatch,
) -> None:
    project_id = "44444444-4444-4444-8444-444444444444"
    body = (
        '{"pipeline":{"id":"77777777-7777-4777-8777-777777777777",'
        '"project_id":"44444444-4444-4444-8444-444444444444",'
        '"name":"hotfix-2026-04-28","instantiated_from_workflow_id":null,'
        '"instantiated_from_workflow_version":null,'
        '"created_at":"2026-04-27T01:00:00Z",'
        '"created_by_actor_id":"11111111-1111-4111-8111-111111111111"}}'
    )
    list_body = '{"pipelines":[],"next_cursor":"cursor-2"}'
    post_calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []
    get_calls: list[tuple[str, dict[str, str], dict[str, object], float]] = []

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        post_calls.append((url, headers, json, timeout))
        return _json_response(url, body, method="POST")

    def fake_get(
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        get_calls.append((url, headers, params, timeout))
        return _json_response(url, list_body)

    monkeypatch.setattr("aq_cli.main.httpx.post", fake_post)
    monkeypatch.setattr("aq_cli.main.httpx.get", fake_get)

    create_result = runner.invoke(
        app,
        [
            "pipeline",
            "create",
            "--project",
            project_id,
            "--name",
            "hotfix-2026-04-28",
        ],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )
    list_result = runner.invoke(
        app,
        [
            "pipeline",
            "list",
            "--limit",
            "10",
            "--cursor",
            "cursor-1",
        ],
        env={API_URL_ENV: "http://api.test/", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert create_result.exit_code == 0
    assert create_result.stdout == f"{body}\n"
    assert list_result.exit_code == 0
    assert list_result.stdout == f"{list_body}\n"
    assert post_calls == [
        (
            "http://api.test/pipelines",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {"project_id": project_id, "name": "hotfix-2026-04-28"},
            10.0,
        )
    ]
    assert get_calls == [
        (
            "http://api.test/pipelines",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {"limit": 10, "cursor": "cursor-1"},
            5.0,
        )
    ]


def test_pipeline_get_and_update_use_expected_paths(
    monkeypatch: MonkeyPatch,
) -> None:
    pipeline_id = "77777777-7777-4777-8777-777777777777"
    body = (
        '{"pipeline":{"id":"77777777-7777-4777-8777-777777777777",'
        '"project_id":"44444444-4444-4444-8444-444444444444",'
        '"name":"hotfix-2026-04-28","instantiated_from_workflow_id":null,'
        '"instantiated_from_workflow_version":null,'
        '"created_at":"2026-04-27T01:00:00Z",'
        '"created_by_actor_id":"11111111-1111-4111-8111-111111111111"}}'
    )
    calls: list[tuple[str, str, dict[str, object] | None, float]] = []

    def fake_get(
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        assert headers == {"Authorization": "Bearer aq2_cli_contract_key"}
        calls.append(("GET", url, None, timeout))
        return _json_response(url, body)

    def fake_patch(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        assert headers == {"Authorization": "Bearer aq2_cli_contract_key"}
        calls.append(("PATCH", url, json, timeout))
        return _json_response(url, body, method="PATCH")

    monkeypatch.setattr("aq_cli.main.httpx.get", fake_get)
    monkeypatch.setattr("aq_cli.main.httpx.patch", fake_patch)

    get_result = runner.invoke(
        app,
        ["pipeline", "get", pipeline_id],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )
    update_result = runner.invoke(
        app,
        ["pipeline", "update", pipeline_id, "--name", "hotfix-2026-04-28-updated"],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert get_result.exit_code == 0
    assert update_result.exit_code == 0
    assert calls == [
        ("GET", f"http://api.test/pipelines/{pipeline_id}", None, 5.0),
        (
            "PATCH",
            f"http://api.test/pipelines/{pipeline_id}",
            {"name": "hotfix-2026-04-28-updated"},
            10.0,
        ),
    ]


def test_pipeline_instantiate_uses_expected_path_and_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    project_id = "44444444-4444-4444-8444-444444444444"
    workflow_slug = "ship-a-thing"
    body = (
        '{"pipeline":{"id":"77777777-7777-4777-8777-777777777777",'
        '"project_id":"44444444-4444-4444-8444-444444444444",'
        '"name":"fix-the-thing","instantiated_from_workflow_id":'
        '"88888888-8888-4888-8888-888888888888",'
        '"instantiated_from_workflow_version":2,'
        '"created_at":"2026-04-27T01:00:00Z",'
        '"created_by_actor_id":"11111111-1111-4111-8111-111111111111"},'
        '"jobs":[{"id":"99999999-9999-4999-8999-999999999999",'
        '"pipeline_id":"77777777-7777-4777-8777-777777777777",'
        '"project_id":"44444444-4444-4444-8444-444444444444",'
        '"state":"ready","title":"scope","description":null,'
        '"contract_profile_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
        '"instantiated_from_step_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",'
        '"labels":[],"claimed_by_actor_id":null,"claimed_at":null,'
        '"claim_heartbeat_at":null,'
        '"created_at":"2026-04-27T01:00:00Z",'
        '"created_by_actor_id":"11111111-1111-4111-8111-111111111111"}]}'
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
        [
            "pipeline",
            "instantiate",
            "--workflow",
            workflow_slug,
            "--project",
            project_id,
            "--name",
            "fix-the-thing",
        ],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            f"http://api.test/pipelines/from-workflow/{workflow_slug}",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {
                "project_id": project_id,
                "pipeline_name": "fix-the-thing",
            },
            10.0,
        )
    ]


def test_label_register_posts_body_and_prints_raw_json(
    monkeypatch: MonkeyPatch,
) -> None:
    project_id = "44444444-4444-4444-8444-444444444444"
    body = (
        '{"label":{"id":"55555555-5555-4555-8555-555555555555",'
        '"project_id":"44444444-4444-4444-8444-444444444444",'
        '"name":"area:web","color":"#336699",'
        '"created_at":"2026-04-27T01:00:00Z","archived_at":null}}'
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
        [
            "label",
            "register",
            "--project",
            project_id,
            "--name",
            "area:web",
            "--color",
            "#336699",
        ],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            f"http://api.test/projects/{project_id}/labels",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {"name": "area:web", "color": "#336699"},
            10.0,
        )
    ]


def test_label_attach_and_detach_use_expected_paths(
    monkeypatch: MonkeyPatch,
) -> None:
    job_id = "66666666-6666-4666-8666-666666666666"
    body = (
        '{"job_id":"66666666-6666-4666-8666-666666666666",'
        '"labels":["area:web"]}'
    )
    calls: list[tuple[str, str, dict[str, object] | None, float]] = []

    def fake_post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        assert headers == {"Authorization": "Bearer aq2_cli_contract_key"}
        calls.append(("POST", url, json, timeout))
        return _json_response(url, body, method="POST")

    def fake_delete(
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        assert headers == {"Authorization": "Bearer aq2_cli_contract_key"}
        calls.append(("DELETE", url, None, timeout))
        return _json_response(url, body, method="DELETE")

    monkeypatch.setattr("aq_cli.main.httpx.post", fake_post)
    monkeypatch.setattr("aq_cli.main.httpx.delete", fake_delete)

    attach_result = runner.invoke(
        app,
        ["label", "attach", job_id, "--name", "area:web"],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )
    detach_result = runner.invoke(
        app,
        ["label", "detach", job_id, "--name", "area:web"],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert attach_result.exit_code == 0
    assert detach_result.exit_code == 0
    assert calls == [
        (
            "POST",
            f"http://api.test/jobs/{job_id}/labels",
            {"label_name": "area:web"},
            10.0,
        ),
        ("DELETE", f"http://api.test/jobs/{job_id}/labels/area:web", None, 10.0),
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


def test_audit_sends_filter_query_params(monkeypatch: MonkeyPatch) -> None:
    body = '{"entries":[],"next_cursor":null}'
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
            "audit",
            "--actor",
            "11111111-1111-4111-8111-111111111111",
            "--op",
            "create_actor",
            "--since",
            "2026-04-27T00:00:00Z",
            "--until",
            "2026-04-27T23:59:59Z",
            "--limit",
            "10000",
            "--cursor",
            "cursor-1",
            "--json",
        ],
        env={API_URL_ENV: "http://api.test", API_KEY_ENV: "aq2_cli_contract_key"},
    )

    assert result.exit_code == 0
    assert result.stdout == f"{body}\n"
    assert calls == [
        (
            "http://api.test/audit",
            {"Authorization": "Bearer aq2_cli_contract_key"},
            {
                "limit": 10000,
                "actor": "11111111-1111-4111-8111-111111111111",
                "op": "create_actor",
                "since": "2026-04-27T00:00:00Z",
                "until": "2026-04-27T23:59:59Z",
                "cursor": "cursor-1",
            },
            5.0,
        )
    ]
