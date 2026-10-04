"""Generate an editable judge policy/Compose override without provider credentials."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def configure(root: Path, url: str) -> tuple[Path, Path]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError("Use the relay HTTPS URL without query parameters or fragment")
    if parsed.username or parsed.password:
        raise ValueError("Relay URL must not contain credentials")
    base = url.rstrip("/")
    policy = yaml.safe_load((root / "backend/policy.yaml").read_text())
    model = policy["models"]["gpt-4o-mini"]
    model.update(upstream_base_url=f"{base}/openai/v1", api_key_env=None)
    jev = policy["jev"]
    jev.update(base_url=f"{base}/typesafe", api_key_env=None, timeout_s=55)
    jev["fallback"].update(base_url=f"{base}/openai/v1", api_key_env=None, timeout_s=55)
    # The relay has cold starts and provider calls; the check's total deadline must fit both.
    policy["checks"]["jev"]["timeout_s"] = 115
    policy_path = root / "backend/policy.judges.yaml"
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False))
    override = {
        "services": {
            "backend": {
                "environment": {
                    "POLICY_PATH": "/app/backend/policy.judges.yaml",
                    "OPENAI_API_KEY": "",
                    "TYPESAFE_API_KEY": "",
                },
                "volumes": ["./backend/policy.judges.yaml:/app/backend/policy.judges.yaml"],
            },
            "test-app": {"environment": {"OPENAI_API_KEY": "", "TYPESAFE_API_KEY": ""}},
        }
    }
    compose_path = root / "compose.judges.yaml"
    compose_path.write_text(yaml.safe_dump(override, sort_keys=False))
    return policy_path, compose_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    for path in configure(args.root, args.url):
        print(path)


if __name__ == "__main__":
    main()
