#!/bin/sh
set -eu

umask 077

normalized_db=$(python - "$CAREER_AGENT_DB_PATH" <<'PY'
import sys

print(sys.argv[1].strip())
PY
)
export CAREER_AGENT_DB_PATH="$normalized_db"

db_path=$(python - "$normalized_db" <<'PY'
import sys
from urllib.parse import unquote, urlsplit

value = sys.argv[1]
path = unquote(urlsplit(value).path) if value.startswith("file:") else value
if "\x00" in path:
    raise SystemExit("CAREER_AGENT_DB_PATH must not contain NUL bytes")
print(path)
PY
)

case "$db_path" in
    /*) ;;
    *) echo "CAREER_AGENT_DB_PATH must resolve to an absolute filesystem path" >&2; exit 64 ;;
esac
data_dir=${db_path%/*}
if [ -z "$data_dir" ]; then
    echo "CAREER_AGENT_DB_PATH must use a writable mounted parent directory" >&2
    exit 64
fi

candidate_port=$(python - "${CAREER_AGENT_CANDIDATE_PORT:-8080}" <<'PY'
import sys

raw = sys.argv[1].strip()
if not raw.isascii() or not raw.isdecimal():
    raise SystemExit("CAREER_AGENT_CANDIDATE_PORT must use ASCII decimal digits")
port = int(raw)
if not 1 <= port <= 65535:
    raise SystemExit("CAREER_AGENT_CANDIDATE_PORT must be between 1 and 65535")
print(port)
PY
)
export CAREER_AGENT_CANDIDATE_PORT="$candidate_port"

normalized_host=$(python - "${CAREER_AGENT_CANDIDATE_HOST:-0.0.0.0}" <<'PY'
import sys

print(sys.argv[1].strip())
PY
)
export CAREER_AGENT_CANDIDATE_HOST="$normalized_host"

# A mounted volume hides the permissions baked into the image. Repair metadata
# on every start and fail closed when the runtime identity cannot secure it.
chmod 0700 "$data_dir"
if [ -e "$db_path" ]; then
    chmod 0600 "$db_path"
fi

exec python -m career_agent_ai.application.runtime.candidate_service
