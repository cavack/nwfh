#!/usr/bin/env bash
#
# Recreate the WaterfallHunter backend and keep the dashboard consistent.
#
# Why this exists
# ---------------
# The Next.js frontend proxies /dashboard/api/* to http://waterfall-backend:8000
# and holds pooled connections plus the resolved container address. Recreating
# the backend container on its own therefore leaves the frontend dialling a dead
# endpoint: other dashboard panels -- /api/candidates, /api/stream and
# /api/recent-signals -- start returning 500 with `connect ECONNREFUSED`
# for a minute or more AFTER the replacement backend is already healthy.
#
# Recreating the frontend in the same operation closes that window. This script
# performs the two steps in the required order and verifies the result through
# the frontend's own proxy path (no credentials, no public edge).
#
# It is intentionally narrow: one backend, one frontend, no other service, no
# compose rebuild, no image build. It never touches the evidence database.
#
# Usage
#   scripts/recreate_backend_safely.sh                  # recreate + verify
#   scripts/recreate_backend_safely.sh --verify-only    # verify, change nothing
#   scripts/recreate_backend_safely.sh --no-frontend    # skip the frontend restart
#
# Rollback
#   Repoint runtime/production-images.override.yml at the previous image and run
#   this script again. Keep the previous image tagged until the change is proven.
#
set -Eeuo pipefail

WFH_DEPLOY_ROOT="${WFH_DEPLOY_ROOT:-/srv/waterfallhunter/app}"
STATE_DIR="${WFH_STATE_DIR:-/srv/waterfallhunter/runtime}"
ENV_FILE="${WFH_ENV_FILE:-/etc/waterfallhunter/waterfallhunter.env}"
PROJECT="${COMPOSE_PROJECT_NAME:-waterfallhunter}"
BACKEND="waterfall-backend"
FRONTEND="waterfall-frontend"

MODE="recreate"
RESTART_FRONTEND=1
for arg in "$@"; do
  case "$arg" in
    --verify-only) MODE="verify" ;;
    --no-frontend) RESTART_FRONTEND=0 ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || fail "required command missing: $1"; }
require docker

compose() {
  COMPOSE_PROJECT_NAME="$PROJECT" \
  COMPOSE_FILE="${WFH_DEPLOY_ROOT}/docker-compose.yml:${STATE_DIR}/production-volumes.override.yml:${STATE_DIR}/production-images.override.yml" \
  WFH_ENV_FILE="$ENV_FILE" \
  docker compose "$@"
}

health_of() {
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$1" 2>/dev/null || echo missing
}

wait_healthy() {
  local name="$1" tries="${2:-24}" i status
  for ((i = 1; i <= tries; i++)); do
    status="$(health_of "$name")"
    printf '  %-22s %s\n' "$name" "$status"
    [[ "$status" == "healthy" ]] && return 0
    [[ "$status" == "missing" ]] && fail "$name is not running"
    sleep 5
  done
  return 1
}

# Exercises the real proxy path: frontend -> waterfall-backend:8000.
verify() {
  local path failed=0
  log "verifying through the frontend proxy"
  for path in /dashboard /dashboard/api/health /dashboard/api/candidates \
              /dashboard/api/recent-signals /dashboard/api/production-evidence; do
    if docker exec "$FRONTEND" wget -q -T 30 -O /dev/null "http://127.0.0.1:3000${path}"; then
      printf '  %-42s ok\n' "$path"
    else
      printf '  %-42s FAILED\n' "$path"
      failed=1
    fi
  done
  (( failed == 0 )) || return 1
  return 0
}

cd "$WFH_DEPLOY_ROOT" || fail "deploy root not found: $WFH_DEPLOY_ROOT"

if [[ "$MODE" == "verify" ]]; then
  log "verify-only: no changes will be made"
  verify || fail "verification failed"
  log "all endpoint checks passed"
  exit 0
fi

log "backend image before: $(docker inspect -f '{{.Config.Image}}' "$BACKEND" 2>/dev/null || echo none)"
log "recreating $BACKEND"
compose up -d --no-deps "$BACKEND"

log "waiting for $BACKEND to become healthy"
wait_healthy "$BACKEND" || fail "$BACKEND did not become healthy; roll back the image override"
log "backend image after: $(docker inspect -f '{{.Config.Image}}' "$BACKEND")"

if (( RESTART_FRONTEND )); then
  # Required: the frontend holds pooled connections to the previous container.
  log "restarting $FRONTEND so it re-resolves the backend"
  docker restart "$FRONTEND" >/dev/null
  log "waiting for $FRONTEND to become healthy"
  wait_healthy "$FRONTEND" 18 || fail "$FRONTEND did not become healthy"
fi

verify || fail "endpoint verification failed after the recreate"
log "done: backend recreated, frontend consistent"
