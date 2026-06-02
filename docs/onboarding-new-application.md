## Onboarding a New Application

This guide describes the steps required to onboard a new application into the `gtr-release-config` deployment pipeline. By the end, your application will be deployed to a GTR target host via rootless Podman, managed through GitHub Actions, and connected via Tailscale.

### Prerequisites

Before starting, ensure the following are in place:

1. **Container images pushed to Tencent Cloud TCR** (`ccr.ccs.tencentyun.com`). Each service component (e.g. backend, frontend) should have its own repository under the appropriate TCR namespace. Images must be tagged with the Git commit SHA or a digest.

2. **Target host provisioned** with rootless Podman, Tailscale, and SSH access. The host should already be a member of the tailnet so that the CI runner can reach it. See `inventories/targets.yaml` for existing target groups.

3. **Service user** decided. Each application runs under a dedicated system user (e.g. `svc-my-app`). The deploy script will create this user automatically if it does not exist.

4. **Healthcheck endpoint** available. The deployed stack must expose an HTTP healthcheck URL that returns a successful status when the service is healthy.

### Step 1: Define the Target Group (if needed)

If your application deploys to an existing target group (e.g. `gtr-core`), skip this step.

To add a new target host, append an entry to `inventories/targets.yaml`:

```yaml
groups:
  my-new-target:
    ssh_host: 100.121.0.XX        # Tailscale IP or DNS of the host
    ssh_user: root                 # SSH user for deployctl
    ssh_port: 22
    require_sudo: false            # true if ssh_user is not root
    service_root: /srv/projects    # where service files live
    secret_root: /srv/project-secrets
    default_healthcheck_timeout_seconds: 60
```

The SSH key used by CI (`DEPLOY_SSH_KEY` secret) must grant access to this host. If the host is only reachable via Tailscale, add its IP or hostname to the `/etc/hosts` step in `.github/workflows/validate-and-deploy.yml`.

### Step 2: Create the Stack Manifest

Create a new YAML file at `environments/<env>/stacks/<service-name>.yaml`. The filename (without extension) becomes the service identifier used throughout the pipeline.

Here is a minimal template:

```yaml
apiVersion: deploy.lynskylate/v1alpha1
kind: DeploymentStack
service_name: my-app                # human-readable service name
target_group: gtr-core              # references inventories/targets.yaml
runtime:
  type: rootless-podman
  network:
    name: my-app                    # podman network name (created automatically)
service_user: svc-my-app            # dedicated system user
exposure: tailscale                 # none | tailscale | envoy
healthcheck:
  url: http://127.0.0.1:8080/healthz
  timeout_seconds: 60
containers:
- service_ref: my-app-backend       # unique identifier for this container
  container_name: my-app-backend    # podman container name
  image_repository: ccr.ccs.tencentyun.com/my-namespace/my-app-backend
  image_digest: sha256:abc123...    # initial digest; updated by release PRs
  image_tag: abc1234                # initial tag; updated by release PRs
  env_profile: my-app-backend       # name of the .env file under secret_root
  container_port: 8080
  host_port: 8080                   # optional; omit if not exposed on host
  volumes:
  - source: /srv/projects/my-app/data
    target: /app/data
    mode: rw
managed_files:                      # optional; non-secret config files
- path: /srv/projects/my-app/config/config.yaml
  mode: '0644'
  content: |
    server:
      port: 8080
rollback_history: []                # populated automatically on each deploy
```

#### Container Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `service_ref` | yes | Unique identifier for this container within the stack. Used in systemd unit names and rollback snapshots. |
| `container_name` | yes | The `--name` argument passed to `podman run`. Must be unique on the target host. |
| `image_repository` | yes | Full repository path on TCR (e.g. `ccr.ccs.tencentyun.com/namespace/repo`). |
| `image_digest` | yes | Immutable digest (`sha256:...`) used for pinning. Updated by each release PR. |
| `image_tag` | no | Mutable tag (usually the Git SHA). If present, takes precedence over `image_digest` at deploy time. |
| `env_profile` | yes | Name of the environment file. The deploy script creates `<secret_root>/<service_user>/<env_profile>.env` if it does not exist. Populate secrets in this file manually after first deploy. |
| `container_port` | yes | The port the application listens on inside the container. |
| `host_port` | no | If set, publishes `127.0.0.1:<host_port>:<container_port>`. |
| `ip_address` | no | Static IP within the podman network. Useful for inter-container communication. |
| `volumes` | no | List of `{source, target, mode}` bind mounts. |
| `depends_on` | no | List of `service_ref` values this container depends on. Systemd `After=` and `Requires=` directives are generated automatically. |
| `extra_hosts` | no | List of `host:ip` entries passed as `--add-host` to podman. |
| `network_aliases` | no | List of DNS aliases on the podman network. |
| `entrypoint` | no | Override the image's default entrypoint. |
| `command` | no | Override the image's default command. Can be a string or list. |
| `resource_limits` | no | Object with optional `memory` (e.g. `"512m"`) and `cpus` (e.g. `"1.0"`) fields. |

#### Managed Files

The `managed_files` section allows you to deploy non-secret configuration files alongside the containers. Each entry specifies:

- `path`: absolute path on the target host
- `content`: the file content (inline YAML string)
- `mode` (optional): file permission (e.g. `'0644'`)
- `owner` / `group` (optional): defaults to `service_user`

For secret values (API keys, database passwords), use the `.env` file mechanism instead. The deploy script creates empty `.env` files at `<secret_root>/<service_user>/<env_profile>.env` on first deploy. Populate them manually via SSH after the initial deployment.

### Step 3: Validate Locally

Before pushing, run the validation suite:

```bash
pip install -e .
python -m unittest discover -s tests -p "test_*.py" -v
python scripts/validate_repo.py
python tools/deployctl.py validate my-app prod
python tools/deployctl.py apply my-app prod --dry-run
```

The `--dry-run` flag prints the generated deploy script and SSH target without actually connecting.

### Step 4: Submit a Pull Request

Commit the new stack manifest and open a PR. The CI pipeline will:

1. **validate** — run unit tests and validate all stack manifests against the schema
2. **plan-deploy** — detect changed stack files and build a deployment matrix
3. **deploy** — for each changed stack, connect via Tailscale and run `deployctl apply`

The deploy step uses the `DEPLOY_SSH_KEY` secret to SSH into the target host. The target host pulls container images directly from TCR using credentials stored in `TCR_USERNAME` and `TCR_PASSWORD` secrets.

### Step 5: Post-Deploy Configuration

After the first successful deployment, SSH into the target host and populate the environment files:

```bash
ssh root@gtr.tail414c32.ts.net
# Find the env file location
ls /srv/project-secrets/svc-my-app/
# Edit the env file with your secrets
vi /srv/project-secrets/svc-my-app/my-app-backend.env
# Restart the service
runuser -u svc-my-app -- systemctl --user restart my-app-backend.service
```

### How Deployment Works

The deployment pipeline follows this flow:

1. A release PR modifies the stack YAML (updating `image_tag` and/or `image_digest`)
2. On merge to `main`, GitHub Actions triggers the deploy workflow
3. The CI runner connects to the tailnet via `tailscale/github-action@v3`
4. `deployctl.py apply` generates a self-contained shell script that:
   - Creates the service user (if needed)
   - Sets up subuid/subgid for rootless Podman
   - Writes managed files and systemd unit files
   - Logs into TCR via `podman login`
   - Pulls container images directly from TCR
   - Enables and restarts systemd user units
   - Runs the healthcheck until it passes or times out
5. The script is piped over SSH to the target host for execution

No images are transferred through the CI runner. The target host pulls images directly from TCR, which typically completes in under a minute.

### Registry Authentication

The CI pipeline authenticates to TCR using three environment variables:

| Variable | Description |
|----------|-------------|
| `DEPLOYCTL_REGISTRY` | Registry hostname (default: `ghcr.io`, set to `ccr.ccs.tencentyun.com` in CI) |
| `DEPLOYCTL_REGISTRY_USERNAME` | Registry username (from `TCR_USERNAME` secret) |
| `DEPLOYCTL_REGISTRY_TOKEN` | Registry password/token (from `TCR_PASSWORD` secret) |

These are embedded into the deploy script payload and used by `podman login` on the target host.

### Rollback

To roll back to a previous revision:

```bash
python tools/deployctl.py rollback my-app prod --revision 0 --dry-run
python tools/deployctl.py rollback my-app prod --revision 0
```

Revision `0` is the most recent snapshot. The `rollback_history` in the stack YAML records each deployment's container digests automatically.

### Multi-Container Stacks

For applications with multiple components (e.g. backend + frontend), define all containers in the same stack file. Use `depends_on` to express startup ordering, `ip_address` and `extra_hosts` for inter-container networking, and `network_aliases` for DNS resolution within the podman network.

See `environments/prod/stacks/corp-finance-monitor.yaml` for a complete multi-container example.
