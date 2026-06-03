from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tools import deployctl


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


class TestDeployCtl(unittest.TestCase):
    def test_render_service_unit_includes_ports_and_volumes(self) -> None:
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
            "image_repository": "ccr.ccs.tencentyun.com/fin-monitor/corp-finance-monitor-frontend",
            "image_digest": "sha256:1234",
            "image_tag": "abc123",
            "env_profile": "corp-finance-monitor-frontend",
            "container_port": 80,
            "host_port": 8190,
            "ip_address": "10.89.0.10",
            "extra_hosts": ["backend:10.89.0.10"],
            "network_aliases": ["frontend"],
            "entrypoint": "/bin/sh",
            "command": ["-lc", "echo test"],
            "volumes": [{"source": "/srv/projects/cfm/data", "target": "/app/data", "mode": "rw"}],
        }

        unit = deployctl.render_service_unit(stack, container, target)

        self.assertIn("--publish 127.0.0.1:8190:80", unit)
        self.assertIn("--ip 10.89.0.10", unit)
        self.assertIn("--add-host backend:10.89.0.10", unit)
        self.assertIn(
            "--env-file /srv/project-secrets/svc-corp-finance-monitor/corp-finance-monitor-frontend.env",
            unit,
        )
        self.assertIn("--entrypoint /bin/sh", unit)
        self.assertIn("--volume /srv/projects/cfm/data:/app/data:rw", unit)
        self.assertIn("ExecStart=/usr/bin/podman run", unit)
        self.assertIn(" -lc 'echo test'", unit)
        self.assertIn("ccr.ccs.tencentyun.com/fin-monitor/corp-finance-monitor-frontend:abc123", unit)

    def test_render_service_unit_entrypoint_list(self) -> None:
        """Entrypoint as a YAML list (e.g. ['python']) must render as a plain
        string, not the Python repr \"['python']\" which causes exit code 127."""
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
            "service_ref": "corp-finance-monitor-scheduler",
            "container_name": "corp-finance-monitor-scheduler",
            "image_repository": "ccr.ccs.tencentyun.com/fin-monitor/corp-finance-monitor-scheduler",
            "image_digest": "sha256:abcd",
            "env_profile": "corp-finance-monitor-backend",
            "container_port": 8191,
            "entrypoint": ["python"],
            "command": ["-m", "corp_finance_monitor", "run", "-c", "/app/config.yaml"],
        }

        unit = deployctl.render_service_unit(stack, container, target)

        # Must NOT contain Python repr like "['python']"
        self.assertNotIn("['python']", unit)
        self.assertNotIn('"[', unit)
        # Must contain the correct plain entrypoint
        self.assertIn("--entrypoint python", unit)
        # Command args must also render correctly
        self.assertIn("-m corp_finance_monitor run -c /app/config.yaml", unit)

    def test_render_service_unit_entrypoint_multi_element_list(self) -> None:
        """Multi-element list entrypoint joins with space."""
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
            "service_name": "test-svc",
            "service_user": "svc-test",
            "runtime": {"type": "rootless-podman", "network": {"name": "test"}},
        }
        container = {
            "service_ref": "test-worker",
            "container_name": "test-worker",
            "image_repository": "example/test-worker",
            "image_digest": "sha256:1234",
            "env_profile": "test-worker",
            "container_port": 8080,
            "entrypoint": ["python", "-u"],
        }

        unit = deployctl.render_service_unit(stack, container, target)

        self.assertNotIn("['python', '-u']", unit)
        self.assertIn("--entrypoint 'python -u'", unit)

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
                            "image_repository": "ccr.ccs.tencentyun.com/fin-monitor/corp-finance-monitor-frontend",
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
                    "image_repository": "ccr.ccs.tencentyun.com/fin-monitor/corp-finance-monitor-backend",
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

        with patch.dict(
            os.environ,
            {
                "DEPLOYCTL_REGISTRY": "ccr.ccs.tencentyun.com",
                "DEPLOYCTL_REGISTRY_USERNAME": "tcuser",
                "DEPLOYCTL_REGISTRY_TOKEN": "token-value",
            },
            clear=False,
        ):
            script = deployctl.build_apply_script(stack, target)

        self.assertIn('managed_file_paths = {item["path"] for item in managed_files}', script)
        self.assertIn('secret_base_root = Path(payload["secret_root"])', script)
        self.assertIn('def build_user_command(args: list[str]) -> list[str]:', script)
        self.assertIn('def ensure_subid(path: str, username: str) -> None:', script)
        self.assertIn('def normalize_cni_network_config(network_name: str) -> None:', script)
        self.assertIn('env_path = secret_root / f"{container[\'env_profile\']}.env"', script)
        self.assertIn('f"HOME={home_dir}"', script)
        self.assertIn('registry_auth = payload.get("registry_auth")', script)
        self.assertIn('"podman",', script)
        self.assertIn('"login",', script)
        self.assertIn(
            'ccr.ccs.tencentyun.com/fin-monitor/corp-finance-monitor-backend:release-sha',
            deployctl.build_apply_script(
                {
                    **stack,
                    "containers": [
                        {
                            **stack["containers"][0],
                            "image_tag": "release-sha",
                        }
                    ],
                },
                target,
            ),
        )
        self.assertIn('run(["systemctl", "start", f"user@{uid}.service"])', script)
        self.assertIn('run(["chmod", "0711", str(secret_base_root)])', script)
        self.assertIn('run(["chmod", "0700", str(secret_root)])', script)
        self.assertIn('normalize_cni_network_config(network_name)', script)
        self.assertIn('service_units = [container["unit_name"] for container in payload["containers"]]', script)
        self.assertIn('build_user_command(["systemctl", "--user", "is-active", *service_units])', script)
        self.assertIn('target_path.write_text(managed_file["content"], encoding="utf-8")', script)
        self.assertIn('pull_with_retry(container["image"])', script)

    def test_command_apply_builds_script_and_runs_ssh(self) -> None:
        stack = {
            "service_name": "corp-finance-monitor",
            "service_user": "svc-corp-finance-monitor",
            "target_group": "gtr-core",
            "runtime": {"type": "rootless-podman", "network": {"name": "corp-finance-monitor"}},
            "healthcheck": {"url": "http://127.0.0.1:8190/healthz"},
            "containers": [
                {
                    "service_ref": "corp-finance-monitor-backend",
                    "container_name": "corp-finance-monitor-backend",
                    "image_repository": "ccr.ccs.tencentyun.com/fin-monitor/corp-finance-monitor-backend",
                    "image_digest": "sha256:1234",
                    "env_profile": "corp-finance-monitor-backend",
                    "container_port": 8190,
                }
            ],
            "managed_files": [],
        }
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

        args = type(
            "Args",
            (),
            {
                "service": "corp-finance-monitor",
                "environment": "prod",
                "dry_run": False,
            },
        )()
        with patch("tools.deployctl.load_stack", return_value=stack), patch(
            "tools.deployctl.load_targets",
            return_value={"gtr-core": target},
        ), patch(
            "tools.deployctl.run_ssh_script",
        ) as ssh_mock:
            result = deployctl.command_apply(args)

        self.assertEqual(result, 0)
        ssh_mock.assert_called_once()
        script = ssh_mock.call_args.args[1]
        self.assertIn('pull_with_retry(container["image"])', script)


if __name__ == "__main__":
    unittest.main()
