from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _assert_redacts(command: list[str], sample: str) -> None:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        input=sample,
        text=True,
    )

    assert "aq2_" not in result.stdout
    assert "$argon2id$" not in result.stdout
    assert "123e4567-e89b-12d3-a456-426614174000" not in result.stdout
    assert "A" * 40 not in result.stdout
    assert "B" * 44 not in result.stdout
    assert "a" * 32 not in result.stdout
    assert "[TOKEN_REDACTED]" in result.stdout
    assert "[ARGON2_REDACTED]" in result.stdout
    assert "[UUID_REDACTED]" in result.stdout


def _bash_available() -> bool:
    try:
        result = subprocess.run(
            ["bash", "-lc", "printf ok"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0 and result.stdout == "ok"


def test_redact_evidence_script_sanitizes_known_secret_patterns() -> None:
    sample = "\n".join(
        [
            "founder_key=aq2_" + ("A" * 40),
            "key_hash=$argon2id$v=19$m=65536,t=2,p=2$abcdef$abcdef",
            "actor_id=123e4567-e89b-12d3-a456-426614174000",
            "lookup_secret=" + ("B" * 44),
            "hex_secret=" + ("a" * 32),
            "",
        ]
    )

    _assert_redacts([sys.executable, str(Path("scripts/redact_evidence.py"))], sample)
    if _bash_available():
        _assert_redacts(["bash", str(Path("scripts/redact-evidence.sh"))], sample)


def test_redact_evidence_script_sanitizes_cap03_evidence_payloads() -> None:
    sample = """
    {
      "project": {"id": "123e4567-e89b-12d3-a456-426614174000"},
      "pipeline": {"id": "223e4567-e89b-12d3-a456-426614174000"},
      "job": {"id": "323e4567-e89b-12d3-a456-426614174000"},
      "setup": {
        "founder_key": "aq2_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "api_key": "aq2_BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "key_hash": "$argon2id$v=19$m=65536,t=2,p=2$abcdef$abcdef"
      },
      "audit": {
        "lookup_secret": "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
      }
    }
    """

    _assert_redacts([sys.executable, str(Path("scripts/redact_evidence.py"))], sample)
    if _bash_available():
        _assert_redacts(["bash", str(Path("scripts/redact-evidence.sh"))], sample)
