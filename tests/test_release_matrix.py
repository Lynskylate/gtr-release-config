from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import release_matrix


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


class TestReleaseMatrix(unittest.TestCase):
    def test_build_matrix_from_changed_stacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_yaml(
                root / "environments/prod/stacks/corp-finance-monitor.yaml",
                {
                    "containers": [{"image_digest": "sha256:1111"}],
                },
            )
            write_yaml(
                root / "environments/staging/stacks/example.yaml",
                {
                    "containers": [{"image_digest": "sha256:2222"}],
                },
            )
            matrix = release_matrix.build_matrix(
                [
                    "README.md",
                    "environments/prod/stacks/corp-finance-monitor.yaml",
                    "environments/prod/stacks/corp-finance-monitor.yaml",
                    "environments/staging/stacks/example.yaml",
                ],
                repo_root=root,
            )
            self.assertEqual(
                matrix,
                {
                    "include": [
                        {"service": "corp-finance-monitor", "environment": "prod"},
                        {"service": "example", "environment": "staging"},
                    ]
                },
            )

    def test_build_matrix_from_manual_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_yaml(
                root / "environments/prod/stacks/corp-finance-monitor.yaml",
                {
                    "containers": [{"image_digest": "sha256:1111"}],
                },
            )
            matrix = release_matrix.build_matrix(
                [],
                service="corp-finance-monitor",
                environment="prod",
                repo_root=root,
            )
            self.assertEqual(
                matrix,
                {"include": [{"service": "corp-finance-monitor", "environment": "prod"}]},
            )

    def test_skips_placeholder_digests(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_yaml(
                root / "environments/prod/stacks/corp-finance-monitor.yaml",
                {
                    "containers": [{"image_digest": release_matrix.ZERO_DIGEST}],
                },
            )
            matrix = release_matrix.build_matrix(
                ["environments/prod/stacks/corp-finance-monitor.yaml"],
                repo_root=root,
            )
            self.assertEqual(matrix, {"include": []})


if __name__ == "__main__":
    unittest.main()
