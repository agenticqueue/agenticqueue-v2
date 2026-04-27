import getpass
import json
import os
import stat
import subprocess
import tomllib
from pathlib import Path
from typing import TypedDict


class StoredConfig(TypedDict):
    api_url: str
    actor_id: str
    api_key: str


class ConfigExistsError(Exception):
    pass


class ConfigPermissionError(Exception):
    pass


def default_config_path() -> Path:
    return Path.home() / ".aq" / "config.toml"


def ensure_config_writable(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise ConfigExistsError(f"{path} already exists")


def read_config(path: Path | None = None) -> StoredConfig:
    config_path = path or default_config_path()
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    return {
        "api_url": _string_value(raw, "api_url"),
        "actor_id": _string_value(raw, "actor_id"),
        "api_key": _string_value(raw, "api_key"),
    }


def write_config(
    path: Path,
    *,
    api_url: str,
    actor_id: str,
    api_key: str,
    force: bool = False,
) -> None:
    ensure_config_writable(path, force=force)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = "\n".join(
        [
            f"api_url = {_toml_string(api_url)}",
            f"actor_id = {_toml_string(actor_id)}",
            f"api_key = {_toml_string(api_key)}",
            "",
        ]
    )

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if not force:
        flags |= os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    _restrict_file(path)


def _string_value(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{key} must be a non-empty string in AQ config")
    return value


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _restrict_file(path: Path) -> None:
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return

    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    user = getpass.getuser()
    completed = subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{user}:F",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ConfigPermissionError(completed.stderr.strip() or completed.stdout)
