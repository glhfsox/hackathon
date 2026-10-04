"""Provision the relay and upload provider secrets without Terraform secret values."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def run(command: list[str], cwd: Path, *, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def credentials(env_file: Path) -> dict[str, str]:
    names = ("OPENAI_API_KEY", "TYPESAFE_API_KEY")
    values = {name: os.environ.get(name, "") for name in names}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, separator, value = line.removeprefix("export ").partition("=")
            name = name.strip()
            if separator and name in names and not values[name]:
                parts = shlex.split(value, comments=True)
                if len(parts) == 1:
                    values[name] = parts[0]
    if not all(values.values()):
        missing = ", ".join(name for name in names if not values[name])
        raise ValueError(f"Missing provider credentials: {missing}")
    return values


def upload(project: str, secret_id: str, value: str, cwd: Path) -> str:
    # stdin keeps credentials out of argv, shell history, Terraform variables and state.
    result = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "add",
            secret_id,
            f"--project={project}",
            "--data-file=-",
            "--format=value(name)",
        ],
        input=value,
        check=False,
        text=True,
        capture_output=True,
        cwd=cwd,
    )
    if result.returncode:
        raise RuntimeError(f"Secret upload failed for {secret_id}; check Google Cloud permissions")
    version = result.stdout.strip().rsplit("/", 1)[-1]
    if not version.isdigit():
        raise RuntimeError("Secret upload returned an invalid version identifier")
    return version


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="hackyeah-2026-510606")
    parser.add_argument("--region", default="europe-west1")
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--total-calls", type=int, default=1500)
    parser.add_argument("--expires-at", default="2026-10-05T21:59:00Z")
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    expiry = datetime.fromisoformat(args.expires_at)
    if expiry.tzinfo is None or expiry <= datetime.now(UTC):
        parser.error("--expires-at must be a future timestamp with timezone")
    if args.total_calls <= 0:
        parser.error("--total-calls must be positive")
    keys = credentials(args.env_file)
    cwd = root / "infra/judge-relay"
    run(
        ["gcloud", "projects", "describe", args.project, "--format=value(projectId)"],
        cwd,
    )
    run(["terraform", "init", "-input=false"], cwd)
    variables = {
        "project_id": args.project,
        "region": args.region,
        "total_calls": args.total_calls,
        "expires_at": args.expires_at,
        "enabled": False,
        "deploy_function": False,
    }
    path = cwd / "deployment.auto.tfvars.json"
    # Do not remove an existing function during repeated deployments.
    state = (
        run(["terraform", "state", "list"], cwd, capture=True)
        if (cwd / "terraform.tfstate").exists()
        else ""
    )
    if "google_cloudfunctions2_function.relay" not in state:
        path.write_text(json.dumps(variables, indent=2) + "\n")
        run(["terraform", "apply", "-input=false", "-auto-approve"], cwd)
    versions = {
        provider: upload(args.project, f"judge-api-relay-{provider}", keys[name], cwd)
        for provider, name in (
            ("openai", "OPENAI_API_KEY"),
            ("typesafe", "TYPESAFE_API_KEY"),
        )
    }
    variables.update(
        deploy_function=True,
        enabled=args.enable,
        openai_secret_version=versions["openai"],
        typesafe_secret_version=versions["typesafe"],
    )
    path.write_text(json.dumps(variables, indent=2) + "\n")
    run(["terraform", "apply", "-input=false", "-auto-approve"], cwd)
    url = run(["terraform", "output", "-raw", "relay_url"], cwd, capture=True)
    run([sys.executable, str(root / "scripts/configure_judge_relay.py"), url], root)
    print(f"Relay: {url}")


if __name__ == "__main__":
    main()
