#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${SITES_ENV_READY:-}" != "1" ]]; then
  exec "${script_dir}/sites-env.sh" -- "$0" "$@"
fi

command -v timeout || {
  echo "build-verified.sh requires GNU timeout." >&2
  exit 69
}

vinext="${SITES_PROJECT_ROOT}/node_modules/.bin/vinext"
deploy_config="${SITES_PROJECT_ROOT}/.wrangler/deploy/config.json"
runtime_deploy_config="${SITES_PROJECT_ROOT}/dist/.wrangler-deploy-config.json"

if [[ ! -x "${vinext}" ]]; then
  echo "vinext is unavailable. Run npm run install:ci and wait for it to finish before building." >&2
  exit 69
fi

echo "Running bounded vinext build..."
timeout \
  --signal=TERM \
  --kill-after="${SITES_BUILD_KILL_AFTER:-10s}" \
  "${SITES_BUILD_TIMEOUT:-3m}" \
  "${vinext}" build

if [[ ! -f "${deploy_config}" ]]; then
  echo "Cloudflare Vite build did not generate ${deploy_config}." >&2
  exit 69
fi

install -m 0644 "${deploy_config}" "${runtime_deploy_config}"
