from __future__ import annotations

import argparse
import base64
import copy
import json
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
VALID_EXPOSURES = {"none", "tailscale", "envoy"}


class ValidationError(ValueError):
    """Raised when a manifest is invalid."""


@dataclass
class TargetGroup:
    name: str
    ssh_host: str
    ssh_user: str
    ssh_port: int
    require_sudo: bool
    service_root: str
    secret_root: str
    default_healthcheck_timeout_seconds: int


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError(f"{path} must contain a YAML object")
    return data


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def validate_project_service(data: dict[str, Any], path: Path | None = None) -> None:
    required = [
        "apiVersion",
        "kind",
        "service_name",
        "dockerfile",
        "internal_port",
        "healthcheck_path",
        "exposure",
        "env_profile",
    ]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValidationError(f"{path or 'service manifest'} missing keys: {missing}")
    if data["apiVersion"] != "deploy.lynskylate/v1alpha1":
        raise ValidationError(f"{path or 'service manifest'} has unsupported apiVersion")
    if data["kind"] != "ProjectService":
        raise ValidationError(f"{path or 'service manifest'} has unsupported kind")
    if not isinstance(data["internal_port"], int) or not 1 <= data["internal_port"] <= 65535:
        raise ValidationError(f"{path or 'service manifest'} internal_port must be 1..65535")
    if data["exposure"] not in VALID_EXPOSURES:
        raise ValidationError(f"{path or 'service manifest'} exposure must be one of {sorted(VALID_EXPOSURES)}")


def validate_stack(data: dict[str, Any], path: Path | None = None) -> None:
    required = [
        "apiVersion",
        "kind",
        "service_name",
        "target_group",
        "runtime",
        "service_user",
        "exposure",
        "healthcheck",
        "containers",
        "rollback_history",
    ]
    missing = [key for key in required if key not in data]
    if missing:
        raise ValidationError(f"{path or 'stack manifest'} missing keys: {missing}")
    if data["apiVersion"] != "deploy.lynskylate/v1alpha1":
        raise ValidationError(f"{path or 'stack manifest'} has unsupported apiVersion")
    if data["kind"] != "DeploymentStack":
        raise ValidationError(f"{path or 'stack manifest'} has unsupported kind")
    if data["exposure"] not in VALID_EXPOSURES:
        raise ValidationError(f"{path or 'stack manifest'} exposure must be one of {sorted(VALID_EXPOSURES)}")
    runtime = data["runtime"]
    if not isinstance(runtime, dict) or runtime.get("type") != "rootless-podman":
        raise ValidationError(f"{path or 'stack manifest'} runtime.type must be rootless-podman")
    containers = data["containers"]
    if not isinstance(containers, list) or not containers:
        raise ValidationError(f"{path or 'stack manifest'} containers must be a non-empty list")
    seen = set()
    for container in containers:
        for key in [
            "service_ref",
            "container_name",
            "image_repository",
            "image_digest",
            "env_profile",
            "container_port",
        ]:
            if key not in container:
                raise ValidationError(f"{path or 'stack manifest'} container missing {key}")
        if container["service_ref"] in seen:
            raise ValidationError(f"{path or 'stack manifest'} duplicate service_ref {container['service_ref']}")
        seen.add(container["service_ref"])
        if not isinstance(container["container_port"], int):
            raise ValidationError(f"{path or 'stack manifest'} container_port must be integer")
        if "host_port" in container and not isinstance(container["host_port"], int):
            raise ValidationError(f"{path or 'stack manifest'} host_port must be integer when present")
    healthcheck = data["healthcheck"]
    if not isinstance(healthcheck, dict) or "url" not in healthcheck:
        raise ValidationError(f"{path or 'stack manifest'} healthcheck.url is required")
    managed_files = data.get("managed_files", [])
    if managed_files and not isinstance(managed_files, list):
        raise ValidationError(f"{path or 'stack manifest'} managed_files must be a list")
    for managed_file in managed_files:
        if not isinstance(managed_file, dict):
            raise ValidationError(f"{path or 'stack manifest'} managed_files entries must be objects")
        for key in ["path", "content"]:
            if key not in managed_file:
                raise ValidationError(f"{path or 'stack manifest'} managed_file missing {key}")


def load_targets(root: Path = ROOT) -> dict[str, TargetGroup]:
    raw = read_yaml(root / "inventories" / "targets.yaml").get("groups", {})
    groups: dict[str, TargetGroup] = {}
    for name, value in raw.items():
        groups[name] = TargetGroup(
            name=name,
            ssh_host=value["ssh_host"],
            ssh_user=value["ssh_user"],
            ssh_port=int(value.get("ssh_port", 22)),
            require_sudo=bool(value.get("require_sudo", False)),
            service_root=value["service_root"],
            secret_root=value["secret_root"],
            default_healthcheck_timeout_seconds=int(value.get("default_healthcheck_timeout_seconds", 60)),
        )
    return groups


def stack_path(service: str, environment: str, root: Path = ROOT) -> Path:
    return root / "environments" / environment / "stacks" / f"{service}.yaml"


def load_stack(service: str, environment: str, root: Path = ROOT) -> dict[str, Any]:
    path = stack_path(service, environment, root)
    if not path.exists():
        raise FileNotFoundError(path)
    data = read_yaml(path)
    validate_stack(data, path)
    return data


def validate_repo(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for path in sorted((root / "environments").glob("*/*/*.yaml")):
        try:
            validate_stack(read_yaml(path), path)
        except Exception as exc:
            errors.append(str(exc))
    return errors


def render_quadlet(stack: dict[str, Any], container: dict[str, Any], target: TargetGroup) -> str:
    dependencies = container.get("depends_on", [])
    unit_lines = [
        "[Unit]",
        f"Description={stack['service_name']} - {container['service_ref']}",
        "After=network-online.target",
        "Wants=network-online.target",
    ]
    for dependency in dependencies:
        unit_lines.append(f"After={dependency}.service")
        unit_lines.append(f"Requires={dependency}.service")

    container_lines = [
        "",
        "[Container]",
        f"Image={container['image_repository']}@{container['image_digest']}",
        f"ContainerName={container['container_name']}",
        f"Network={stack['runtime']['network']['name']}",
        f"EnvironmentFile={target.secret_root}/{stack['service_user']}/{container['env_profile']}.env",
    ]
    if "host_port" in container:
        container_lines.append(
            f"PublishPort=127.0.0.1:{container['host_port']}:{container['container_port']}"
        )
    aliases = container.get("network_aliases", [])
    podman_args: list[str] = []
    if aliases:
        podman_args.extend(f"--network-alias={alias}" for alias in aliases)
    limits = container.get("resource_limits", {})
    if limits.get("memory"):
        container_lines.append(f"Memory={limits['memory']}")
    if limits.get("cpus"):
        podman_args.append(f"--cpus={limits['cpus']}")
    if podman_args:
        container_lines.append(f"PodmanArgs={' '.join(podman_args)}")
    for volume in container.get("volumes", []):
        container_lines.append(
            f"Volume={volume['source']}:{volume['target']}:{volume.get('mode', 'rw')}"
        )

    service_lines = [
        "",
        "[Service]",
        "Restart=always",
        "TimeoutStartSec=120",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(unit_lines + container_lines + service_lines)


def build_apply_script(stack: dict[str, Any], target: TargetGroup) -> str:
    service_user = stack["service_user"]
    service_root = f"{target.service_root}/{stack['service_name']}"
    home_dir = f"{service_root}/home"
    quadlet_dir = f"{home_dir}/.config/containers/systemd"
    container_payloads = []
    for container in stack["containers"]:
        quadlet = render_quadlet(stack, container, target)
        encoded = base64.b64encode(quadlet.encode("utf-8")).decode("ascii")
        container_payloads.append(
            {
                "file_name": f"{container['service_ref']}.container",
                "unit_name": f"{container['service_ref']}.service",
                "env_profile": container["env_profile"],
                "content": encoded,
                "volumes": container.get("volumes", []),
            }
        )

    data = {
        "service_name": stack["service_name"],
        "service_user": service_user,
        "service_root": service_root,
        "home_dir": home_dir,
        "quadlet_dir": quadlet_dir,
        "network_name": stack["runtime"]["network"]["name"],
        "secret_root": target.secret_root,
        "require_sudo": target.require_sudo,
        "containers": container_payloads,
        "managed_files": stack.get("managed_files", []),
        "healthcheck": {
            "url": stack["healthcheck"]["url"],
            "timeout": int(
                stack["healthcheck"].get(
                    "timeout_seconds",
                    target.default_healthcheck_timeout_seconds,
                )
            ),
        },
    }

    script = textwrap.dedent(
        """\
        set -euo pipefail
        python3 - <<'PY'
        import base64
        import json
        import subprocess
        import time
        from pathlib import Path

        payload = json.loads(__PAYLOAD_JSON__)
        service_user = payload["service_user"]
        service_root = Path(payload["service_root"])
        home_dir = Path(payload["home_dir"])
        quadlet_dir = Path(payload["quadlet_dir"])
        network_name = payload["network_name"]
        secret_root = Path(payload["secret_root"]) / service_user
        managed_files = payload.get("managed_files", [])
        managed_file_paths = {item["path"] for item in managed_files}

        def run(cmd: list[str], **kwargs) -> None:
            subprocess.run(cmd, check=True, **kwargs)

        def run_user(command: str) -> None:
            run(["runuser", "-u", service_user, "--", "bash", "-lc", command])

        try:
            run(["id", service_user], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            run([
                "useradd",
                "--system",
                "--create-home",
                "--home-dir",
                str(home_dir),
                "--shell",
                "/usr/sbin/nologin",
                service_user,
            ])

        run(["loginctl", "enable-linger", service_user])
        run(["mkdir", "-p", str(service_root), str(secret_root), str(quadlet_dir)])
        run(["chown", "-R", f"{service_user}:{service_user}", str(service_root), str(secret_root)])

        for managed_file in managed_files:
            target_path = Path(managed_file["path"])
            run(["mkdir", "-p", str(target_path.parent)])
            target_path.write_text(managed_file["content"], encoding="utf-8")
            mode = managed_file.get("mode")
            if mode:
                run(["chmod", mode, str(target_path)])
            owner = managed_file.get("owner", service_user)
            group = managed_file.get("group", service_user)
            run(["chown", f"{owner}:{group}", str(target_path)])

        for container in payload["containers"]:
            env_path = secret_root / f"{container['env_profile']}.env"
            if not env_path.exists():
                env_path.touch()
                run(["chmod", "0600", str(env_path)])
                run(["chown", f"{service_user}:{service_user}", str(env_path)])
            for volume in container["volumes"]:
                volume_path = Path(volume["source"])
                if volume["source"] in managed_file_paths:
                    run(["mkdir", "-p", str(volume_path.parent)])
                else:
                    run(["mkdir", "-p", str(volume_path)])
            target_path = quadlet_dir / container["file_name"]
            target_path.write_bytes(base64.b64decode(container["content"]))
            run(["chown", f"{service_user}:{service_user}", str(target_path)])

        run_user(f"podman network exists {{network_name}} || podman network create {{network_name}}".format(network_name=network_name))
        run_user("systemctl --user daemon-reload")

        for container in payload["containers"]:
            run_user(f"systemctl --user enable {container['unit_name']}")
            run_user(f"systemctl --user restart {container['unit_name']}")

        deadline = time.time() + payload["healthcheck"]["timeout"]
        healthcheck_url = payload["healthcheck"]["url"]
        while time.time() < deadline:
            result = subprocess.run(
                ["curl", "--silent", "--show-error", "--fail", healthcheck_url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                break
            time.sleep(3)
        else:
            raise SystemExit(f"healthcheck failed: {{healthcheck_url}}")
        PY
        """
    )
    return script.replace("__PAYLOAD_JSON__", json.dumps(json.dumps(data)))


def build_status_script(stack: dict[str, Any]) -> str:
    service_user = stack["service_user"]
    services = [f"{container['service_ref']}.service" for container in stack["containers"]]
    joined = " ".join(services)
    return textwrap.dedent(
        f"""\
        set -euo pipefail
        runuser -u {service_user} -- systemctl --user --no-pager --full status {joined}
        """
    )


def build_rollback_stack(stack: dict[str, Any], revision: int) -> dict[str, Any]:
    history = stack.get("rollback_history", [])
    if revision >= len(history):
        raise ValidationError(
            f"rollback revision {revision} is unavailable; history length={len(history)}"
        )
    snapshot = history[revision]
    digests = snapshot.get("containers", {})
    new_stack = copy.deepcopy(stack)
    for container in new_stack["containers"]:
        service_ref = container["service_ref"]
        if service_ref not in digests:
            raise ValidationError(f"rollback snapshot missing digest for {service_ref}")
        container["image_digest"] = digests[service_ref]
    return new_stack


def build_ssh_command(target: TargetGroup) -> list[str]:
    ssh_target = f"{target.ssh_user}@{target.ssh_host}"
    remote_command = "sudo --non-interactive bash -s --" if target.require_sudo else "bash -s --"
    return ["ssh", "-p", str(target.ssh_port), ssh_target, remote_command]


def run_ssh_script(target: TargetGroup, script: str, dry_run: bool) -> None:
    ssh_target = f"{target.ssh_user}@{target.ssh_host}"
    command = build_ssh_command(target)
    if dry_run:
        print("# dry-run ssh target:", ssh_target)
        print("# dry-run remote command:", command[-1])
        print(script)
        return
    subprocess.run(command, input=script, text=True, check=True)


def command_validate(args: argparse.Namespace) -> int:
    stack = load_stack(args.service, args.environment)
    validate_stack(stack, stack_path(args.service, args.environment))
    print(f"validated {args.service} in {args.environment}")
    return 0


def command_apply(args: argparse.Namespace) -> int:
    stack = load_stack(args.service, args.environment)
    target = load_targets()[stack["target_group"]]
    script = build_apply_script(stack, target)
    run_ssh_script(target, script, args.dry_run)
    return 0


def command_status(args: argparse.Namespace) -> int:
    stack = load_stack(args.service, args.environment)
    target = load_targets()[stack["target_group"]]
    script = build_status_script(stack)
    run_ssh_script(target, script, args.dry_run)
    return 0


def command_rollback(args: argparse.Namespace) -> int:
    stack = load_stack(args.service, args.environment)
    rollback_stack = build_rollback_stack(stack, args.revision)
    target = load_targets()[rollback_stack["target_group"]]
    script = build_apply_script(rollback_stack, target)
    run_ssh_script(target, script, args.dry_run)
    return 0


def command_validate_repo(_: argparse.Namespace) -> int:
    errors = validate_repo()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("repository manifests validated")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deploy release-config stacks to GTR hosts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    repo_parser = subparsers.add_parser("validate-repo", help="validate all stack manifests")
    repo_parser.set_defaults(func=command_validate_repo)

    validate_parser = subparsers.add_parser("validate", help="validate one stack manifest")
    validate_parser.add_argument("service")
    validate_parser.add_argument("environment")
    validate_parser.set_defaults(func=command_validate)

    apply_parser = subparsers.add_parser("apply", help="apply a stack manifest")
    apply_parser.add_argument("service")
    apply_parser.add_argument("environment")
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.set_defaults(func=command_apply)

    status_parser = subparsers.add_parser("status", help="inspect a deployed stack")
    status_parser.add_argument("service")
    status_parser.add_argument("environment")
    status_parser.add_argument("--dry-run", action="store_true")
    status_parser.set_defaults(func=command_status)

    rollback_parser = subparsers.add_parser("rollback", help="re-apply a recorded rollback revision")
    rollback_parser.add_argument("service")
    rollback_parser.add_argument("environment")
    rollback_parser.add_argument("--revision", type=int, default=0)
    rollback_parser.add_argument("--dry-run", action="store_true")
    rollback_parser.set_defaults(func=command_rollback)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
