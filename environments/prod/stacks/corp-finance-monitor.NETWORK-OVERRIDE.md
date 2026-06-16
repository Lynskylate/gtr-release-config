# ⚠️ MANUAL NETWORK OVERRIDE — deployctl schema gap

**Date:** 2026-06-16
**Owner:** @Pat (deployctl.py) — schema fix pending (task #45 extension)
**Status:** Running config does NOT match `corp-finance-monitor.yaml` source. Do NOT blindly redeploy without re-applying this override.

## Root cause

The CNI bridge network `corp-finance-monitor` (10.89.0.0/24) has **no working internet egress** in this rootless-podman environment — containers cannot resolve external domains (cninfo, hkexnews) and DNS queries time out. This caused a 3-day sync stall (scheduler stuck in DNS retry loop, 4079+ failures) on 2026-06-13 → 2026-06-16.

Host networking resolves this (containers use the host's resolver + network stack directly).

## What's actually running (host networking)

All three containers run with `--network host`, NOT the CNI bridge declared in `corp-finance-monitor.yaml`.

| Container | Image tag | Network | Listen | Proxy |
|-----------|-----------|---------|--------|-------|
| backend | `ae20d7384456e8c8e8e69777bf9b874e60403523` | `--network host` | `0.0.0.0:8191` (explicit `serve --port 8191`; image default Cmd was `--port 8190`) | — |
| frontend | `ea384031787f805f640815f64767e1648372a8ee` | `--network host` | nginx `listen 8190` (sed-rewritten from `listen 80`) | `proxy_pass http://127.0.0.1:8191` (sed-rewritten from `http://backend:8190`) |
| scheduler | `ae20d7384456e8c8e8e69777bf9b874e60403523` | `--network host` | — | — |

## Other config deltas vs source yaml

- `config.yaml` `api.port`: source `8190` → running `8191`
- backend/scheduler `image_tag`: source `5706fb66` → running `ae20d738`
- frontend `image_tag`: source `5706fb66` → running `ea384031787f`
- backend `container_port`: source `8190` → running `8191`
- frontend `extra_hosts backend:10.89.0.10`: removed (host net, not needed)
- CNI `ip_address`, `network_aliases`: ignored under host networking

## deployctl schema fix needed

`tools/deployctl.py:render_service_unit` (around line 191) hardcodes `--network <stack.runtime.network.name>`. To express host networking in source:

1. Add `runtime.network.mode` field (`bridge` | `host`) to the schema
2. When `mode == host`: emit `--network host`, skip `--ip`/`--network-alias`/`--publish`/`--add-host`
3. Frontend nginx rewrites (`listen` port + `proxy_pass` target) need to be templated from `container_port`/backend port, not hardcoded

Until that lands, a redeploy via deployctl will revert to CNI and reintroduce the DNS stall.
