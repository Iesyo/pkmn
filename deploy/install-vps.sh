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
readonly APP_URL="http://127.0.0.1:3200"
readonly DATA_ROOT="/var/lib/pkmn"
readonly STATE_DIR="${DATA_ROOT}/state"

RUNTIME_STAGE_DIR=""

die() {
  printf 'ERROR: %s\n' "$*" >&2
  return 1
}

verify_service() {
  local service="$1"
  systemctl is-active --quiet "${service}" \
    || die "${service} is not active; bootstrap cancelled."
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

cleanup_stage() {
  [[ -z "${RUNTIME_STAGE_DIR}" || ! -d "${RUNTIME_STAGE_DIR}" ]] || rm -rf -- "${RUNTIME_STAGE_DIR}"
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

main() {
  [[ "${EUID}" -eq 0 ]] || die "Run install-vps.sh as root."

  for command in git curl flock runuser systemctl ss node npm tar mktemp getent groupadd useradd; do
    command -v "${command}" >/dev/null 2>&1 || die "Required command not found: ${command}"
  done

  node --input-type=module <<'NODE'
const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 13)) {
  console.error(`Node >=22.13.0 is required; found ${process.versions.node}.`);
  process.exit(1);
}
NODE

  [[ -d "${APP_DIR}/.git" ]] || die "Repository not found at ${APP_DIR}."
  cd "${APP_DIR}"
  [[ "$(git branch --show-current)" == "main" ]] || die "The repository must be on branch main."
  [[ -z "$(git status --porcelain)" ]] || die "The repository has local changes; bootstrap cancelled."

  verify_service "${BOT_SERVICE}"
  verify_service "${LINKTREE_SERVICE}"
  verify_service "${TUNNEL_SERVICE}"

  if systemctl is-active --quiet "${APP_SERVICE}"; then
    die "${APP_SERVICE} is already active; use sudo pkmdeploy for updates."
  fi

  if ! getent group "${APP_GROUP}" >/dev/null; then
    groupadd --system "${APP_GROUP}"
  fi
  if ! id "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --gid "${APP_GROUP}" --home-dir "${DATA_ROOT}" --shell /usr/sbin/nologin "${APP_USER}"
  fi

  mkdir -p \
    "${STATE_DIR}" \
    "${DATA_ROOT}/logs" \
    "${DATA_ROOT}/registry" \
    "${DATA_ROOT}/home" \
    "${DATA_ROOT}/backups"
  chown -R "${APP_USER}:${APP_GROUP}" "${DATA_ROOT}"
  chmod 0750 "${DATA_ROOT}" "${STATE_DIR}" "${DATA_ROOT}/logs" "${DATA_ROOT}/registry" "${DATA_ROOT}/home" "${DATA_ROOT}/backups"

  if ss -ltnH | awk '{print $4}' | grep -Eq ':3200$'; then
    die "TCP port 3200 is already in use by another service."
  fi

  trap cleanup_stage EXIT

  printf '[1/8] Installing locked dependencies...\n'
  npm run install:ci

  printf '[2/8] Running tests and verified production build...\n'
  npm test

  printf '[3/8] Staging an isolated production runtime...\n'
  stage_runtime

  printf '[4/8] Applying D1 migrations to persistent VPS state...\n'
  run_as_app "${APP_DIR}/node_modules/.bin/wrangler" \
    d1 migrations apply site-creator-d1 \
    --local \
    --persist-to "${STATE_DIR}" \
    --config "${APP_DIR}/wrangler.local.jsonc"

  printf '[5/8] Installing systemd unit and pkmdeploy command...\n'
  install -o root -g root -m 0644 \
    "${APP_DIR}/deploy/pkmn.service" \
    "/etc/systemd/system/${APP_SERVICE}"
  install -o root -g root -m 0755 \
    "${APP_DIR}/deploy/pkmdeploy.sh" \
    /usr/local/bin/pkmdeploy
  systemctl daemon-reload

  printf '[6/8] Activating the staged runtime...\n'
  systemctl stop "${APP_SERVICE}" 2>/dev/null || true
  rm -rf -- "${RUNTIME_DIR}"
  mv "${RUNTIME_STAGE_DIR}" "${RUNTIME_DIR}"
  RUNTIME_STAGE_DIR=""
  systemctl enable --now "${APP_SERVICE}"
  systemctl is-active --quiet "${APP_SERVICE}"

  printf '[7/8] Verifying page and D1-backed API health...\n'
  wait_for_app || {
    journalctl -u "${APP_SERVICE}" --no-pager -n 100 >&2
    return 1
  }

  printf '[8/8] Verifying protected shared-VPS services...\n'
  verify_service "${BOT_SERVICE}"
  verify_service "${LINKTREE_SERVICE}"
  verify_service "${TUNNEL_SERVICE}"

  printf '\nVPS bootstrap complete at commit %s.\n' "$(git rev-parse --short HEAD)"
  printf 'Local app: %s\n' "${APP_URL}"
  printf 'Future updates: sudo pkmdeploy\n'
  printf 'Public hostname is intentionally not configured by this script.\n'
}

main "$@"
