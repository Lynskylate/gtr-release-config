from __future__ import annotations

import unittest

from scripts import release_matrix


class TestReleaseMatrix(unittest.TestCase):
    def test_build_matrix_from_changed_stacks(self) -> None:
        matrix = release_matrix.build_matrix(
            [
                "README.md",
                "environments/prod/stacks/corp-finance-monitor.yaml",
                "environments/prod/stacks/corp-finance-monitor.yaml",
                "environments/staging/stacks/example.yaml",
            ]
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
        matrix = release_matrix.build_matrix([], service="corp-finance-monitor", environment="prod")
        self.assertEqual(
            matrix,
            {"include": [{"service": "corp-finance-monitor", "environment": "prod"}]},
        )


if __name__ == "__main__":
    unittest.main()
