# gtr-release-config

`gtr-release-config` is the deployment control repository for GTR-hosted services.

It does not store application source code. It stores:

- deployment stack manifests for each environment
- target-host inventory and deployment policy
- non-secret managed runtime files such as service config
- the `deployctl` CLI used by the controlled runner
- validation and deployment workflows for release PRs

## Repository Layout

```text
.
|- contracts/                  # schemas for project and stack manifests
|- environments/
|  `- prod/
|     `- stacks/              # one stack manifest per deployed service
|- inventories/               # target host groups and deployment settings
|- scripts/
|  |- release_matrix.py       # maps changed stack files to deployment jobs
|  `- validate_repo.py        # repository-wide validation entrypoint
|- tests/
|- tools/
|  `- deployctl.py            # apply/status/rollback CLI
`- .github/workflows/
```

## Operating Model

- Project repositories declare only minimal service contracts and build images.
- Project GitHub Actions push immutable image digests and open a PR against this repo.
- This repo owns the environment-specific deployment stack and is the only source of truth for production rollout.
- A controlled self-hosted runner deploys merged stack changes with `deployctl`.
- `gtr-services` remains responsible for host baselines, Tailscale, Envoy, monitoring, and shared runtime setup.

## Quick Start

```bash
python -m pip install -e .
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/validate_repo.py
python tools/deployctl.py validate corp-finance-monitor prod
python tools/deployctl.py apply corp-finance-monitor prod --dry-run
```

## Key Files

- `contracts/project-service.schema.json` defines the contract that project repos must publish.
- `contracts/deployment-stack.schema.json` defines the release-layer stack manifest.
- `inventories/targets.yaml` defines `gtr-core`, `edge-aliyun`, and `edge-tencent`.
- `environments/prod/stacks/corp-finance-monitor.yaml` is the first sample stack.
- `.github/workflows/validate-and-deploy.yml` validates PRs and deploys merged stack changes through a controlled runner.
- `managed_files` in a stack can seed non-secret files such as `/srv/projects/<service>/config/config.yaml`.

## Runtime Assumptions

- Python 3.11+
- SSH access from the controlled runner to deployment targets
- Passwordless `sudo` on targets where `require_sudo: true`
- Rootless Podman baseline already prepared by `gtr-services`
