import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const serviceUrl = new URL("../deploy/pkmn.service", import.meta.url);
const buildScriptUrl = new URL("../scripts/build-verified.sh", import.meta.url);

test("keeps the production runtime read-only except for Vite config scratch", async () => {
  const service = await readFile(serviceUrl, "utf8");

  assert.match(service, /User=pkmn/);
  assert.match(service, /Group=pkmn/);
  assert.match(service, /ExecStartPre=\+\/usr\/bin\/rm -rf -- \/opt\/pkmn-runtime\/node_modules\/\.vite-temp/);
  assert.match(service, /ExecStartPre=\+\/usr\/bin\/install -d -o root -g pkmn -m 0770 \/opt\/pkmn-runtime\/node_modules\/\.vite-temp/);
  assert.match(service, /ExecStartPre=\+\/usr\/bin\/install -d -o root -g pkmn -m 0750 \/opt\/pkmn-runtime\/\.wrangler\/deploy/);
  assert.match(service, /ExecStartPre=\+\/usr\/bin\/install -o root -g pkmn -m 0640 \/opt\/pkmn-runtime\/dist\/\.wrangler-deploy-config\.json \/opt\/pkmn-runtime\/\.wrangler\/deploy\/config\.json/);
  assert.match(service, /ExecStart=\/opt\/pkmn-runtime\/deploy\/start-vps\.sh/);
  assert.doesNotMatch(service, /chmod\s+-R\s+[^\n]*g\+w[^\n]*\/opt\/pkmn-runtime/);
});

test("promotes Cloudflare's generated deploy redirect into the immutable runtime build", async () => {
  const build = await readFile(buildScriptUrl, "utf8");

  assert.match(build, /deploy_config="\$\{SITES_PROJECT_ROOT\}\/\.wrangler\/deploy\/config\.json"/);
  assert.match(build, /runtime_deploy_config="\$\{SITES_PROJECT_ROOT\}\/dist\/\.wrangler-deploy-config\.json"/);
  assert.match(build, /if \[\[ ! -f "\$\{deploy_config\}" \]\]; then/);
  assert.match(build, /install -m 0644 "\$\{deploy_config\}" "\$\{runtime_deploy_config\}"/);
});
