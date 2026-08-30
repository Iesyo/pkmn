#!/usr/bin/env bash
set -Eeuo pipefail

readonly RUNTIME_DIR="/opt/pkmn-runtime"
readonly DATA_ROOT="/var/lib/pkmn"
readonly STATE_DIR="${DATA_ROOT}/state"

cd "${RUNTIME_DIR}"

export HOME="${DATA_ROOT}/home"
export PKMN_PERSIST_PATH="${STATE_DIR}"
export WRANGLER_WRITE_LOGS="false"
export WRANGLER_LOG_PATH="${DATA_ROOT}/logs/wrangler.log"
export MINIFLARE_REGISTRY_PATH="${DATA_ROOT}/registry"

exec "${RUNTIME_DIR}/node_modules/.bin/vite" preview \
  --host 127.0.0.1 \
  --port 3200 \
  --strictPort
