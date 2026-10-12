#!/bin/sh
set -eu

umask 077

data_dir=${CAREER_AGENT_DB_PATH%/*}
if [ "$data_dir" = "$CAREER_AGENT_DB_PATH" ]; then
    data_dir=.
fi

# A mounted volume hides the permissions baked into the image. Repair metadata
# on every start and fail closed when the runtime identity cannot secure it.
chmod 0700 "$data_dir"
if [ -e "$CAREER_AGENT_DB_PATH" ]; then
    chmod 0600 "$CAREER_AGENT_DB_PATH"
fi

exec python -m career_agent_ai.application.runtime.candidate_service
