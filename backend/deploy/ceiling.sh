#!/usr/bin/env bash
#
# Set the global Apify spend ceiling and let the running workers see it.
#
# Usage:
#   ceiling.sh 10      Allow $10 across every event
#   ceiling.sh 0       No global ceiling
#   ceiling.sh         Report the current one
#
# Note:
#   The worker reads its environment when the container starts, so changing the file is only
#   half the job — without the recreate below, raising the ceiling would look like it did
#   nothing until the next deploy. Workers that are not up are left alone.
set -euo pipefail

ROOT=${ROOT:-/opt/ayudagente}
ENV_FILE=$ROOT/.env
KEY=HARVEST_SPEND_TOTAL_CEILING_USD

current() {
    grep "^$KEY=" "$ENV_FILE" | cut -d= -f2
}

main() {
    test -f "$ENV_FILE" || { echo "missing $ENV_FILE" >&2; exit 1; }

    if [ $# -eq 0 ]; then
        echo "$KEY=$(current)"
        exit 0
    fi

    local usd=$1
    case $usd in
        ''|*[!0-9.]*) echo "not an amount: $usd" >&2; exit 2 ;;
    esac

    if grep -q "^$KEY=" "$ENV_FILE"; then
        sed -i "s|^$KEY=.*|$KEY=$usd|" "$ENV_FILE"
    else
        echo "$KEY=$usd" >> "$ENV_FILE"
    fi
    echo "$KEY=$(current)"

    local running
    running=$("$ROOT/dc.sh" --profile workers ps --status running --services 2>/dev/null || true)
    if [ -z "$running" ]; then
        echo "no workers up; the new ceiling applies the next time they start"
        exit 0
    fi

    echo "recreating the workers so they read it"
    # harvest-worker above all: it is the one that runs a job and therefore reads the ceiling
    "$ROOT/dc.sh" --profile workers up -d --force-recreate worker harvest-worker beat
}

main "$@"
