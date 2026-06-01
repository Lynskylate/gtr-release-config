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
            "image_repository": "ghcr.io/lynskylate/corp-finance-monitor-frontend",
            "image_digest": "sha256:1234",
            "image_tag": "abc123",
            "env_profile": "corp-finance-monitor-frontend",
            "container_port": 80,
            "host_port": 8190,
            "network_aliases": ["frontend"],
            "volumes": [{"source": "/srv/projects/cfm/data", "target": "/app/data", "mode": "rw"}],
        }

        unit = deployctl.render_service_unit(stack, container, target)

        self.assertIn("--publish 127.0.0.1:8190:80", unit)
        self.assertIn(
            "--env-file /srv/project-secrets/svc-corp-finance-monitor/corp-finance-monitor-frontend.env",
            unit,
        )
        self.assertIn("--volume /srv/projects/cfm/data:/app/data:rw", unit)
        self.assertIn("ExecStart=/usr/bin/podman run", unit)
        self.assertIn("ghcr.io/lynskylate/corp-finance-monitor-frontend:abc123", unit)

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

        with patch.dict(
            os.environ,
            {
                "DEPLOYCTL_GHCR_USERNAME": "Lynskylate",
                "DEPLOYCTL_GHCR_TOKEN": "token-value",
            },
            clear=False,
        ):
            script = deployctl.build_apply_script(
                stack,
                target,
                {"corp-finance-monitor-backend": "/tmp/backend.tar"},
            )

        self.assertIn('managed_file_paths = {item["path"] for item in managed_files}', script)
        self.assertIn('def ensure_subid(path: str, username: str) -> None:', script)
        self.assertIn('env_path = secret_root / f"{container[\'env_profile\']}.env"', script)
        self.assertIn('f"HOME={home_dir}"', script)
        self.assertIn('registry_auth = payload.get("registry_auth")', script)
        self.assertIn('"podman",', script)
        self.assertIn('"login",', script)
        self.assertIn('image_archive_path = container.get("image_archive_path")', script)
        self.assertIn('if shutil.which("skopeo"):', script)
        self.assertIn('"containers-storage:{container[\'image\']}"', script)
        self.assertIn('run_user_args(["podman", "load", "-i", image_archive_path])', script)
        self.assertIn(
            'ghcr.io/lynskylate/corp-finance-monitor-backend:release-sha',
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
        self.assertIn('target_path.write_text(managed_file["content"], encoding="utf-8")', script)
        self.assertIn('run_user(f"podman pull {container[\'image\']}")', script)

    def test_stage_stack_images_uses_local_container_tool(self) -> None:
        stack = {
            "containers": [
                {
                    "service_ref": "corp-finance-monitor-backend",
                    "image_repository": "ghcr.io/lynskylate/corp-finance-monitor-backend",
                    "image_digest": "sha256:1234",
                    "image_tag": "release-sha",
                }
            ]
        }

        commands: list[list[str]] = []

        def fake_run(cmd: list[str], **_: object) -> None:
            commands.append(cmd)
            if cmd[:3] == ["docker", "save", "-o"]:
                Path(cmd[3]).write_text("archive", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "tools.deployctl.shutil.which",
            side_effect=lambda candidate: "/usr/bin/docker" if candidate == "docker" else None,
        ), patch(
            "tools.deployctl.subprocess.run",
            side_effect=fake_run,
        ), patch.dict(
            os.environ,
            {
                "DEPLOYCTL_GHCR_USERNAME": "Lynskylate",
                "DEPLOYCTL_GHCR_TOKEN": "token-value",
            },
            clear=False,
        ):
            archives = deployctl.stage_stack_images(stack, Path(tmpdir))

        archive_path = Path(tmpdir) / "corp-finance-monitor-backend.tar"
        self.assertEqual(archives["corp-finance-monitor-backend"], archive_path)
        self.assertEqual(
            commands[0],
            ["docker", "login", "ghcr.io", "--username", "Lynskylate", "--password-stdin"],
        )
        self.assertEqual(
            commands[1],
            ["docker", "pull", "ghcr.io/lynskylate/corp-finance-monitor-backend:release-sha"],
        )
        self.assertEqual(
            commands[2],
            [
                "docker",
                "save",
                "-o",
                str(archive_path),
                "ghcr.io/lynskylate/corp-finance-monitor-backend:release-sha",
            ],
        )

    def test_stage_stack_images_prefers_release_artifacts(self) -> None:
        stack = {
            "artifact_source_repository": "Lynskylate/corp-finance-monitor",
            "artifact_source_run_id": 26771237800,
            "containers": [
                {
                    "service_ref": "corp-finance-monitor-backend",
                    "image_artifact": "release-image-corp-finance-monitor-backend",
                    "image_repository": "ghcr.io/lynskylate/corp-finance-monitor-backend",
                    "image_tag": "release-sha",
                }
            ],
        }

        def fake_run(cmd: list[str], **_: object) -> None:
            if cmd[0] == "gh":
                download_dir = Path(cmd[cmd.index("--dir") + 1])
                (download_dir / "corp-finance-monitor-backend.tar").write_text("archive", encoding="utf-8")
                return
            if cmd[0] == "/usr/bin/skopeo":
                destination = cmd[-1].removeprefix("docker-archive:")
                tar_index = destination.find(".tar")
                converted = Path(destination[: tar_index + 4])
                converted.write_text("converted-archive", encoding="utf-8")
                return
            raise AssertionError(cmd)

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "tools.deployctl.shutil.which",
            side_effect=lambda candidate: f"/usr/bin/{candidate}" if candidate in {"gh", "skopeo"} else None,
        ), patch(
            "tools.deployctl.subprocess.run",
            side_effect=fake_run,
        ):
            archives = deployctl.stage_stack_images(stack, Path(tmpdir))
            archive_path = Path(tmpdir) / "corp-finance-monitor-backend.tar"
            self.assertEqual(archives["corp-finance-monitor-backend"], archive_path)
            self.assertTrue(archive_path.exists())

    def test_command_apply_uploads_image_archives_before_ssh(self) -> None:
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
                    "image_repository": "ghcr.io/lynskylate/corp-finance-monitor-backend",
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

        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / "corp-finance-monitor-backend.tar"
            archive_path.write_text("archive", encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "service": "corp-finance-monitor",
                    "environment": "prod",
                    "image_archive_dir": tmpdir,
                    "dry_run": False,
                },
            )()
            with patch("tools.deployctl.load_stack", return_value=stack), patch(
                "tools.deployctl.load_targets",
                return_value={"gtr-core": target},
            ), patch(
                "tools.deployctl.upload_image_archives",
                return_value={"corp-finance-monitor-backend": "/tmp/backend.tar"},
            ) as upload_mock, patch(
                "tools.deployctl.run_ssh_script",
            ) as ssh_mock:
                result = deployctl.command_apply(args)

        self.assertEqual(result, 0)
        upload_mock.assert_called_once()
        ssh_mock.assert_called_once()
        script = ssh_mock.call_args.args[1]
        self.assertIn("/tmp/backend.tar", script)


if __name__ == "__main__":
    unittest.main()
