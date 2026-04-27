#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  set -- plans/v2-rebuild/artifacts/cap-02/*
fi

for file in "$@"; do
  [ -f "$file" ] || continue
  perl -0pi -e 's/\$argon2id\$[^\s"'\''<>]+/[ARGON2_REDACTED]/g; s/\baq2_[A-Za-z0-9_-]{20,}\b/[TOKEN_REDACTED]/g; s/\b[A-Fa-f0-9]{32,}\b/[TOKEN_REDACTED]/g' "$file"
done
