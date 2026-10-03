"""Create .env from .env.example with a random value for every local API key.

Run once: `docker compose run --rm init`, or `python scripts/init_env.py`. An existing .env is
never overwritten, so keys already handed to agents keep working.
"""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Issued by an outside service, so it cannot be generated here.
EXTERNAL_KEYS = {"TYPESAFE_API_KEY"}


def main() -> int:
    target = ROOT / ".env"
    if target.exists():
        print(f"{target.name} already exists; delete it first to generate new keys")
        return 0
    lines = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        name = line.partition("=")[0]
        if (
            line == f"{name}="
            and name.endswith("_API_KEY")
            and name not in EXTERNAL_KEYS
        ):
            line = f"{name}={secrets.token_urlsafe(24)}"
        lines.append(line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {target.name} with random API keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
