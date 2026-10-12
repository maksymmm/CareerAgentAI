#!/bin/sh
set -eu

umask 077

db_path=$(python - "$CAREER_AGENT_DB_PATH" <<'PY'
import sys
from urllib.parse import unquote, urlsplit

value = sys.argv[1]
path = unquote(urlsplit(value).path) if value.startswith("file:") else value
print(path)
PY
)

case "$db_path" in
    /*) ;;
    *) echo "CAREER_AGENT_DB_PATH must resolve to an absolute filesystem path" >&2; exit 64 ;;
esac
data_dir=${db_path%/*}

case "${CAREER_AGENT_CANDIDATE_PORT:-8080}" in
    0) echo "CAREER_AGENT_CANDIDATE_PORT=0 is not supported in the container" >&2; exit 64 ;;
esac

# A mounted volume hides the permissions baked into the image. Repair metadata
# on every start and fail closed when the runtime identity cannot secure it.
chmod 0700 "$data_dir"
if [ -e "$db_path" ]; then
    chmod 0600 "$db_path"
fi

exec python -m career_agent_ai.application.runtime.candidate_service
