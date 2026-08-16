#!/usr/bin/env bash
#
# docker compose against the deployed stack, from anywhere on the host.
#
# Usage:
#   dc.sh ps                          Container status
#   dc.sh logs -f web                 Follow the API log
#   dc.sh run --rm web python manage.py events
#   dc.sh --profile workers up -d worker beat
#
# Note:
#   The env lives outside the clone so `git reset --hard` cannot take it, which means every
#   compose call has to name it. That is the whole reason this wrapper exists.
set -euo pipefail

ROOT=${ROOT:-/opt/ayudagente}

exec docker compose --env-file "$ROOT/.env" -f "$ROOT/app/docker-compose.prod.yml" "$@"
