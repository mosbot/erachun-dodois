#!/usr/bin/env bash
# Cron-invoked wrapper that runs the eRačun sync inside the web container.
#
# Installed at /opt/erachun-dodois/sync_invoices.sh, called every 30 min by
# the `ask` user's crontab:
#
#     */30 * * * * /opt/erachun-dodois/sync_invoices.sh
#
# Behaviour:
#   * Uses `flock` so overlapping runs are impossible (the sync can take a
#     while when 10+ new invoices arrive).
#   * All output is appended to /var/log/erachun-sync.log with timestamps
#     so a broken cron never disappears silently again.
#   * Exits with the Python CLI's exit code.

set -euo pipefail

PROJECT_DIR="/opt/erachun-dodois"
LOCK_FILE="/tmp/erachun-sync.lock"
LOG_FILE="/var/log/erachun-sync.log"
COMPOSE="docker compose -f ${PROJECT_DIR}/docker-compose.yaml"

# Ensure the log file exists and is writable by the cron user.
if [[ ! -e "${LOG_FILE}" ]]; then
  sudo -n touch "${LOG_FILE}" 2>/dev/null || touch "${LOG_FILE}" 2>/dev/null || LOG_FILE="${PROJECT_DIR}/sync.log"
  sudo -n chown "$(id -un)":"$(id -gn)" "${LOG_FILE}" 2>/dev/null || true
fi

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

run() {
  echo "[$(ts)] sync start"
  # -T disables TTY allocation (required under cron).
  ${COMPOSE} exec -T web python scripts/sync_eracun.py
  local rc=$?
  echo "[$(ts)] sync done rc=${rc}"

  # Post Wolt/Glovo invoices to PlanFact. Runs under the same flock, so it
  # never overlaps itself and always sees freshly synced invoices. A failure
  # here must not mask a sync failure, so we protect it from set -e and
  # return the sync's rc regardless of PlanFact's outcome.
  echo "[$(ts)] planfact start"
  if ${COMPOSE} exec -T web python scripts/post_to_planfact.py; then
    local pf_rc=0
  else
    local pf_rc=$?
  fi
  echo "[$(ts)] planfact done rc=${pf_rc}"

  return ${rc}
}

# Single-instance guard: non-blocking lock, exit cleanly if another run is in progress.
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  echo "[$(ts)] another sync in progress, skipping" >>"${LOG_FILE}"
  exit 0
fi

run >>"${LOG_FILE}" 2>&1
