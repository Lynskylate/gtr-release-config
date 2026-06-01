from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from tools import deployctl


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


class TestDeployCtl(unittest.TestCase):
    def test_render_quadlet_includes_ports_and_volumes(self) -> None:
        target = deployctl.TargetGroup(
            name="gtr-core",
            ssh_host="gtr.tail414c32.ts.net",
            ssh_user="root",
            ssh_port=22,
            require_sudo=False,
            service_root="/srv/projects",
            secret_root="/srv/project-secrets",
            default_healthcheck_timeout_seconds=60,
        )
        stack = {
            "service_name": "corp-finance-monitor",
            "service_user": "svc-corp-finance-monitor",
            "runtime": {"type": "rootless-podman", "network": {"name": "corp-finance-monitor"}},
        }
        container = {
            "service_ref": "corp-finance-monitor-frontend",
            "container_name": "corp-finance-monitor-frontend",
            "image_repository": "ghcr.io/lynskylate/corp-finance-monitor-frontend",
            "image_digest": "sha256:1234",
            "env_profile": "corp-finance-monitor-frontend",
            "container_port": 80,
            "host_port": 8190,
            "network_aliases": ["frontend"],
            "volumes": [{"source": "/srv/projects/cfm/data", "target": "/app/data", "mode": "rw"}],
        }

        quadlet = deployctl.render_quadlet(stack, container, target)

        self.assertIn("PublishPort=127.0.0.1:8190:80", quadlet)
        self.assertIn(
            "EnvironmentFile=/srv/project-secrets/svc-corp-finance-monitor/corp-finance-monitor-frontend.env",
            quadlet,
        )
        self.assertIn("Volume=/srv/projects/cfm/data:/app/data:rw", quadlet)

    def test_validate_repo_accepts_sample_stack(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_yaml(
                root / "inventories/targets.yaml",
                {
                    "groups": {
                        "gtr-core": {
                            "ssh_host": "gtr.tail414c32.ts.net",
                            "ssh_user": "root",
                            "ssh_port": 22,
                            "require_sudo": False,
                            "service_root": "/srv/projects",
                            "secret_root": "/srv/project-secrets",
                            "default_healthcheck_timeout_seconds": 60,
                        }
                    }
                },
            )
            write_yaml(
                root / "environments/prod/stacks/corp-finance-monitor.yaml",
                {
                    "apiVersion": "deploy.lynskylate/v1alpha1",
                    "kind": "DeploymentStack",
                    "service_name": "corp-finance-monitor",
                    "target_group": "gtr-core",
                    "runtime": {"type": "rootless-podman", "network": {"name": "cfm"}},
                    "service_user": "svc-corp-finance-monitor",
                    "exposure": "tailscale",
                    "healthcheck": {"url": "http://127.0.0.1:8190/healthz"},
                    "containers": [
                        {
                            "service_ref": "corp-finance-monitor-frontend",
                            "container_name": "corp-finance-monitor-frontend",
                            "image_repository": "ghcr.io/lynskylate/corp-finance-monitor-frontend",
                            "image_digest": "sha256:1234",
                            "env_profile": "corp-finance-monitor-frontend",
                            "container_port": 80,
                            "host_port": 8190,
                        }
                    ],
                    "rollback_history": [],
                },
            )

            self.assertEqual(deployctl.validate_repo(root), [])

    def test_build_ssh_command_uses_sudo_when_required(self) -> None:
        target = deployctl.TargetGroup(
            name="edge-aliyun",
            ssh_host="47.120.46.128",
            ssh_user="yiling",
            ssh_port=22,
            require_sudo=True,
            service_root="/srv/projects",
            secret_root="/srv/project-secrets",
            default_healthcheck_timeout_seconds=60,
        )
        command = deployctl.build_ssh_command(target)
        self.assertEqual(command[-1], "sudo --non-interactive bash -s --")

    def test_build_apply_script_manages_files_and_env_placeholders(self) -> None:
        target = deployctl.TargetGroup(
            name="gtr-core",
            ssh_host="gtr.tail414c32.ts.net",
            ssh_user="root",
            ssh_port=22,
            require_sudo=False,
            service_root="/srv/projects",
            secret_root="/srv/project-secrets",
            default_healthcheck_timeout_seconds=60,
        )
        stack = {
            "service_name": "corp-finance-monitor",
            "service_user": "svc-corp-finance-monitor",
            "runtime": {"type": "rootless-podman", "network": {"name": "corp-finance-monitor"}},
            "healthcheck": {"url": "http://127.0.0.1:8190/healthz"},
            "containers": [
                {
                    "service_ref": "corp-finance-monitor-backend",
                    "container_name": "corp-finance-monitor-backend",
                    "image_repository": "ghcr.io/lynskylate/corp-finance-monitor-backend",
                    "image_digest": "sha256:1234",
                    "env_profile": "corp-finance-monitor-backend",
                    "container_port": 8190,
                    "volumes": [
                        {
                            "source": "/srv/projects/corp-finance-monitor/config/config.yaml",
                            "target": "/app/config.yaml",
                            "mode": "ro",
                        }
                    ],
                }
            ],
            "managed_files": [
                {
                    "path": "/srv/projects/corp-finance-monitor/config/config.yaml",
                    "content": "api:\n  port: 8190\n",
                    "mode": "0644",
                }
            ],
        }

        script = deployctl.build_apply_script(stack, target)

        self.assertIn('managed_file_paths = {item["path"] for item in managed_files}', script)
        self.assertIn('env_path = secret_root / f"{container[\'env_profile\']}.env"', script)
        self.assertIn('target_path.write_text(managed_file["content"], encoding="utf-8")', script)


if __name__ == "__main__":
    unittest.main()
