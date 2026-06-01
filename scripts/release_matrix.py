from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

import yaml


ZERO_DIGEST = "sha256:" + ("0" * 64)


def stack_has_ready_images(repo_root: Path, environment: str, service: str) -> bool:
    path = repo_root / "environments" / environment / "stacks" / f"{service}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    containers = data.get("containers", [])
    return bool(containers) and all(container.get("image_digest") != ZERO_DIGEST for container in containers)


def build_matrix(
    changed_files: list[str],
    service: str | None = None,
    environment: str | None = None,
    repo_root: Path | None = None,
) -> dict[str, list[dict[str, str]]]:
    root = repo_root or Path.cwd()
    if service:
        if not environment:
            raise ValueError("environment is required when service is specified")
        if not stack_has_ready_images(root, environment, service):
            return {"include": []}
        return {"include": [{"service": service, "environment": environment}]}

    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_path in changed_files:
        path = PurePosixPath(raw_path.strip())
        parts = path.parts
        if len(parts) != 4 or parts[0] != "environments" or parts[2] != "stacks":
            continue
        if path.suffix != ".yaml":
            continue
        key = (path.stem, parts[1])
        if key in seen:
            continue
        if not stack_has_ready_images(root, parts[1], path.stem):
            continue
        seen.add(key)
        entries.append({"service": path.stem, "environment": parts[1]})
    return {"include": entries}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deployment matrix from changed stack files")
    parser.add_argument("--service")
    parser.add_argument("--environment")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--changed-file-list", help="Path to a newline-delimited list of changed files")
    parser.add_argument("--github-output", help="Path to the GitHub Actions output file")
    args = parser.parse_args()

    changed_files = list(args.changed_file)
    if args.changed_file_list:
        changed_files.extend(
            line.strip()
            for line in Path(args.changed_file_list).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    matrix = build_matrix(changed_files, service=args.service, environment=args.environment)
    payload = json.dumps(matrix, separators=(",", ":"))
    has_changes = "true" if matrix["include"] else "false"

    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"matrix={payload}\n")
            handle.write(f"has_changes={has_changes}\n")
    else:
        print(payload)
        print(has_changes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
