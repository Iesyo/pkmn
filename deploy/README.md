# VPS deployment

LikeNoOneEverWas runs on the shared VPS as an isolated systemd service.

- Source checkout: `/opt/pkmn`
- Staged production runtime: `/opt/pkmn-runtime`
- Service: `pkmn.service`
- Loopback endpoint: `http://127.0.0.1:3200`
- Persistent local D1 state: `/var/lib/pkmn/state`
- Update command: `/usr/local/bin/pkmdeploy`

The runtime uses `vite preview` with the Cloudflare Vite plugin so the application executes with Workers-compatible bindings, including local D1. `PKMN_PERSIST_PATH` points both the Vite plugin and Wrangler migrations at the same state directory.

`pkmdeploy` only accepts a clean `main` checkout and fast-forward updates from `origin/main`. It runs the locked dependency install when needed, the full test/build gate, stages a separate runtime, stops the app only for the D1 backup/migration/runtime swap, then checks `/` and the D1-backed `/api/teams` route. It also verifies that `bottrading-web.service`, `iesyh-linktree.service`, and `cloudflared.service` remain active. A failed deployment restores the prior runtime and D1 snapshot.

The first installation is performed from the source checkout with `sudo bash /opt/pkmn/deploy/install-vps.sh`. Public hostname and Cloudflare Tunnel routing are intentionally configured separately after the local service has passed its smoke tests.
