#!/usr/bin/env bash
#
# Redeploy the API from the tip of origin/main.
#
# Usage:
#   deploy.sh              Pull, rebuild, recreate, migrate, collect static
#   deploy.sh --reset      Drop the database volume first, then deploy onto an empty one
#
# Note:
#   The body sits in main() so bash parses the whole script before `git reset` rewrites it.
#
#   Deploying creates no rows. It leaves a migrated database and nothing else, so reference
#   data and fixtures stay deliberate acts: `make prod.taxonomy`, `make prod.gazetteer` and
#   `make prod.seed`. Note that `arm_event` refuses an event whose country has no gazetteer.
#
#   Harvesting is never started here: the workers profile stays down until brought up by hand.
set -euo pipefail

ROOT=${ROOT:-/opt/ayudagente}
APP=$ROOT/app
ENV_FILE=$ROOT/.env
BRANCH=${BRANCH:-main}

compose() {
    docker compose --env-file "$ENV_FILE" -f "$APP/docker-compose.prod.yml" "$@"
}

install_helpers() {
    local script
    for script in deploy.sh dc.sh ceiling.sh; do
        chmod +x "$APP/deploy/$script"
        ln -sfn "$APP/deploy/$script" "$ROOT/$script"
    done
}

# Reports drift, never fails: a deploy stopped by a cosmetic difference is worse than the drift
reconcile_env() {
    local template=$APP/deploy/env.prod.example
    test -f "$template" || return 0

    echo "==> reconciling $ENV_FILE against the template"
    local key value deployed missing=0 drifted=0

    while IFS='=' read -r key value; do
        case $key in ''|\#*) continue ;; esac

        if ! grep -q "^$key=" "$ENV_FILE"; then
            echo "    MISSING  $key${value:+  (template says $value)}"
            missing=$((missing + 1))
            continue
        fi

        # An empty template value marks a secret, and secrets are only ever compared by presence
        test -n "$value" || continue

        deployed=$(grep "^$key=" "$ENV_FILE" | head -1 | cut -d= -f2-)
        if [ "$deployed" != "$value" ]; then
            echo "    DRIFTED  $key: deployed $deployed, template $value"
            drifted=$((drifted + 1))
        fi
    done < "$template"

    while IFS='=' read -r key _; do
        case $key in ''|\#*) continue ;; esac
        grep -q "^$key=" "$template" || echo "    EXTRA    $key is not in the template"
    done < "$ENV_FILE"

    if [ "$missing" = 0 ] && [ "$drifted" = 0 ]; then
        echo "    in step with the template"
    fi
}

main() {
    local reset=0
    for arg in "$@"; do
        case $arg in
            --reset) reset=1 ;;
            *) echo "unknown flag: $arg" >&2; exit 2 ;;
        esac
    done

    test -f "$ENV_FILE" || { echo "missing $ENV_FILE" >&2; exit 1; }

    echo "==> fetching origin/$BRANCH"
    git -C "$APP" fetch --prune origin
    git -C "$APP" reset --hard "origin/$BRANCH"
    echo "    now at $(git -C "$APP" rev-parse --short HEAD) $(git -C "$APP" log -1 --format=%s)"

    install_helpers
    reconcile_env

    if [ "$reset" = 1 ]; then
        echo "==> dropping the database volume"
        compose down -v
    fi

    echo "==> building"
    compose build

    echo "==> starting the data services"
    compose up -d db redis

    echo "==> migrating"
    compose run --rm web python manage.py migrate --noinput

    echo "==> collecting static files"
    compose run --rm web python manage.py collectstatic --noinput

    echo "==> starting the API"
    compose up -d web
    compose ps
}

main "$@"
