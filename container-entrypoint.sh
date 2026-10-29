#!/bin/sh
set -eu

umask 077
exec python -m career_agent_ai.application.runtime.container_entrypoint
