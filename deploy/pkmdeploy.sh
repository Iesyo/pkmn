#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_DIR="/opt/pkmn"
readonly RUNTIME_DIR="/opt/pkmn-runtime"
readonly APP_USER="pkmn"
readonly APP_GROUP="pkmn"
readonly APP_SERVICE="pkmn.service"
readonly BOT_SERVICE="bottrading-web.service"
readonly LINKTREE_SERVICE="iesyh-linktree.service"
readonly TUNNEL_SERVICE="cloudflared.service"
readonly TARGET_BRANCH="main"
readonly APP_URL="http://127.0.0.1:3200"
readonly DATA_ROOT="/var/lib/pkmn"
readonly STATE_DIR="${DATA_ROOT}/state"
readonly BACKUP_ROOT="${DATA_ROOT}/backups"
readonly LOCK_FILE="/run/lock/pkmdeploy.lock"

PREVIOUS_COMMIT=""
TARGET_COMMIT=""
STATE_BACKUP_DIR=""
RUNTIME_STAGE_DIR=""
RUNTIME_BACKUP_DIR=""
STATE_BACKED_UP=0
RUNTIME_SWAPPED=0
ROLLBACK_ARMED=0

die() {
  printf 'ERROR: %s\n' "$*" >&2
  return 1
}

verify_service() {
  local service="$1"
  systemctl is-active --quiet "${service}" \
    || die "${service} is not active; deployment cancelled."
}

wait_for_app() {
  local attempt
  for attempt in {1..45}; do
    if curl --fail --silent --show-error --output /dev/null "${APP_URL}/" \
      && curl --fail --silent --show-error --output /dev/null "${APP_URL}/api/teams"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

run_as_app() {
  runuser -u "${APP_USER}" -- env \
    CI=1 \
    HOME="${DATA_ROOT}/home" \
    PKMN_PERSIST_PATH="${STATE_DIR}" \
    WRANGLER_WRITE_LOGS=false \
    WRANGLER_LOG_PATH="${DATA_ROOT}/logs/wrangler.log" \
    MINIFLARE_REGISTRY_PATH="${DATA_ROOT}/registry" \
    "$@"
}

install_control_files() {
  install -o root -g root -m 0644 \
    "${APP_DIR}/deploy/pkmn.service" \
    "/etc/systemd/system/${APP_SERVICE}"
  install -o root -g root -m 0755 \
    "${APP_DIR}/deploy/pkmdeploy.sh" \
    /usr/local/bin/pkmdeploy
  systemctl daemon-reload
}

stage_runtime() {
  RUNTIME_STAGE_DIR="$(mktemp -d /opt/pkmn-runtime-new.XXXXXX)"
  tar -C "${APP_DIR}" \
    --exclude='./.git' \
    --exclude='./.wrangler' \
    --exclude='./.sites-runtime' \
    --exclude='./.env*' \
    --exclude='./outputs' \
    --exclude='./work' \
    -cf - . \
    | tar -C "${RUNTIME_STAGE_DIR}" -xf -

  [[ -x "${RUNTIME_STAGE_DIR}/node_modules/.bin/vite" ]] \
    || die "Staged runtime does not contain Vite."
  [[ -d "${RUNTIME_STAGE_DIR}/dist" ]] \
    || die "Staged runtime does not contain the production build."

  chown -R root:"${APP_GROUP}" "${RUNTIME_STAGE_DIR}"
  chmod -R o-rwx "${RUNTIME_STAGE_DIR}"
  chmod 0750 "${RUNTIME_STAGE_DIR}"
}

backup_state() {
  mkdir -p "${BACKUP_ROOT}"
  STATE_BACKUP_DIR="$(mktemp -d "${BACKUP_ROOT}/state.XXXXXX")"
  if [[ -d "${STATE_DIR}" ]]; then
    cp -a "${STATE_DIR}" "${STATE_BACKUP_DIR}/state"
  fi
  STATE_BACKED_UP=1
}

swap_runtime() {
  RUNTIME_BACKUP_DIR="$(mktemp -d /opt/pkmn-runtime-old.XXXXXX)"
  if [[ -d "${RUNTIME_DIR}" ]]; then
    mv "${RUNTIME_DIR}" "${RUNTIME_BACKUP_DIR}/runtime"
  fi
  mv "${RUNTIME_STAGE_DIR}" "${RUNTIME_DIR}"
  RUNTIME_STAGE_DIR=""
  RUNTIME_SWAPPED=1
}

restore_state() {
  [[ "${STATE_BACKED_UP}" -eq 1 ]] || return 0
  rm -rf -- "${STATE_DIR}"
  if [[ -d "${STATE_BACKUP_DIR}/state" ]]; then
    cp -a "${STATE_BACKUP_DIR}/state" "${STATE_DIR}"
  else
    mkdir -p "${STATE_DIR}"
  fi
  chown -R "${APP_USER}:${APP_GROUP}" "${DATA_ROOT}"
}

restore_runtime() {
  [[ "${RUNTIME_SWAPPED}" -eq 1 ]] || return 0
  rm -rf -- "${RUNTIME_DIR}"
  if [[ -d "${RUNTIME_BACKUP_DIR}/runtime" ]]; then
    mv "${RUNTIME_BACKUP_DIR}/runtime" "${RUNTIME_DIR}"
  fi
}

cleanup_artifacts() {
  [[ -z "${RUNTIME_STAGE_DIR}" || ! -d "${RUNTIME_STAGE_DIR}" ]] || rm -rf -- "${RUNTIME_STAGE_DIR}"
  [[ -z "${RUNTIME_BACKUP_DIR}" || ! -d "${RUNTIME_BACKUP_DIR}" ]] || rm -rf -- "${RUNTIME_BACKUP_DIR}"
  [[ -z "${STATE_BACKUP_DIR}" || ! -d "${STATE_BACKUP_DIR}" ]] || rm -rf -- "${STATE_BACKUP_DIR}"
}

rollback() {
  local exit_code="$?"
  trap - ERR

  if [[ "${ROLLBACK_ARMED}" -ne 1 ]]; then
    exit "${exit_code}"
  fi

  printf '\nDeployment failed. Restoring commit %s, runtime and persistent D1 state...\n' "${PREVIOUS_COMMIT}" >&2
  systemctl stop "${APP_SERVICE}" || true
  restore_state || true
  restore_runtime || true

  cd "${APP_DIR}"
  git reset --hard "${PREVIOUS_COMMIT}" || true
  install_control_files || true
  systemctl start "${APP_SERVICE}" || true

  if wait_for_app; then
    printf 'Previous LikeNoOneEverWas runtime is healthy again.\n' >&2
  else
    printf 'WARNING: rollback completed but local health still fails.\n' >&2
    journalctl -u "${APP_SERVICE}" --no-pager -n 100 >&2 || true
  fi

  for service in "${BOT_SERVICE}" "${LINKTREE_SERVICE}" "${TUNNEL_SERVICE}"; do
    systemctl is-active --quiet "${service}" \
      || printf 'WARNING: protected service %s is not active.\n' "${service}" >&2
  done

  cleanup_artifacts
  exit "${exit_code}"
}

main() {
  [[ "${EUID}" -eq 0 ]] || die "Run pkmdeploy as root."
  for command in flock git curl runuser systemctl tar mktemp; do
    command -v "${command}" >/dev/null 2>&1 || die "Required command not found: ${command}"
  done

  exec 9>"${LOCK_FILE}"
  flock -n 9 || die "Another pkmdeploy process is already running."

  [[ -d "${APP_DIR}/.git" ]] || die "Repository not found at ${APP_DIR}."
  [[ -d "${RUNTIME_DIR}" ]] || die "Runtime not found at ${RUNTIME_DIR}."
  [[ -d "${DATA_ROOT}" ]] || die "Persistent data root not found at ${DATA_ROOT}."
  id "${APP_USER}" >/dev/null 2>&1 || die "System user ${APP_USER} does not exist."

  cd "${APP_DIR}"
  [[ "$(git branch --show-current)" == "${TARGET_BRANCH}" ]] \
    || die "The repository must be on branch ${TARGET_BRANCH}."
  [[ -z "$(git status --porcelain)" ]] \
    || die "The repository has local changes; deployment cancelled."

  verify_service "${APP_SERVICE}"
  verify_service "${BOT_SERVICE}"
  verify_service "${LINKTREE_SERVICE}"
  verify_service "${TUNNEL_SERVICE}"
  wait_for_app || die "Current LikeNoOneEverWas runtime is not healthy; deployment cancelled."

  PREVIOUS_COMMIT="$(git rev-parse HEAD)"
  printf '[1/9] Fetching %s from GitHub...\n' "${TARGET_BRANCH}"
  git fetch --prune origin "${TARGET_BRANCH}"
  TARGET_COMMIT="$(git rev-parse "origin/${TARGET_BRANCH}")"

  git merge-base --is-ancestor "${PREVIOUS_COMMIT}" "${TARGET_COMMIT}" \
    || die "origin/${TARGET_BRANCH} is not a fast-forward from the deployed commit."

  if [[ "${PREVIOUS_COMMIT}" == "${TARGET_COMMIT}" ]]; then
    printf 'Already current at %s. Running health checks only.\n' "$(git rev-parse --short HEAD)"
    verify_service "${BOT_SERVICE}"
    verify_service "${LINKTREE_SERVICE}"
    verify_service "${TUNNEL_SERVICE}"
    wait_for_app || die "LikeNoOneEverWas health check failed."
    return 0
  fi

  git merge --ff-only "${TARGET_COMMIT}"
  ROLLBACK_ARMED=1
  trap rollback ERR
  trap cleanup_artifacts EXIT

  printf '[2/9] Installing locked dependencies when needed...\n'
  if [[ ! -x node_modules/.bin/vite ]] \
    || ! git diff --quiet "${PREVIOUS_COMMIT}" "${TARGET_COMMIT}" -- package.json package-lock.json scripts/install-ci.sh scripts/sites-env.sh; then
    npm run install:ci
  else
    printf 'Dependencies unchanged; reusing node_modules.\n'
  fi

  printf '[3/9] Running tests and verified production build...\n'
  npm test

  printf '[4/9] Staging an isolated production runtime...\n'
  stage_runtime

  printf '[5/9] Freezing and backing up persistent D1 state...\n'
  systemctl stop "${APP_SERVICE}"
  backup_state

  printf '[6/9] Applying D1 migrations against persistent VPS state...\n'
  run_as_app "${APP_DIR}/node_modules/.bin/wrangler" \
    d1 migrations apply site-creator-d1 \
    --local \
    --persist-to "${STATE_DIR}" \
    --config "${APP_DIR}/wrangler.local.jsonc"

  printf '[7/9] Swapping runtime and restarting LikeNoOneEverWas...\n'
  install_control_files
  swap_runtime
  systemctl restart "${APP_SERVICE}"
  systemctl is-active --quiet "${APP_SERVICE}"

  printf '[8/9] Verifying page and D1-backed API health...\n'
  wait_for_app || {
    journalctl -u "${APP_SERVICE}" --no-pager -n 100 >&2
    return 1
  }

  printf '[9/9] Verifying protected shared-VPS services...\n'
  verify_service "${BOT_SERVICE}"
  verify_service "${LINKTREE_SERVICE}"
  verify_service "${TUNNEL_SERVICE}"

  trap - ERR
  ROLLBACK_ARMED=0
  cleanup_artifacts
  RUNTIME_BACKUP_DIR=""
  STATE_BACKUP_DIR=""

  printf '\nDeployment complete: %s -> %s\n' \
    "${PREVIOUS_COMMIT:0:7}" "$(git rev-parse --short HEAD)"
  printf 'Local app: %s\n' "${APP_URL}"
  printf 'BotTrading, Linktree and Cloudflare Tunnel remain active.\n'
}

main "$@"
