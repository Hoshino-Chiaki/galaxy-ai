#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
umask 077
mkdir -p "$TASK_ROOT/session_research/private"
exec flock -n -E 24 "$TASK_ROOT/session_research/private/youzan-login.lock" \
  node "$TASK_ROOT/session_research/youzan-login.cjs" "$@"
