"""Create .env from .env.example with a random JWT_SECRET.

Run once: `docker compose run --rm init`, or `python scripts/init_env.py`. An existing .env is
never overwritten, so tokens already signed with the secret keep working.
"""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The only secret generated here: the layer verifies tokens with it and the agents sign with it.
# Other keys (TYPESAFE_API_KEY) are issued by an outside service and stay empty.
GENERATED = "JWT_SECRET"


def main() -> int:
    target = ROOT / ".env"
    if target.exists():
        print(f"{target.name} already exists; delete it first to generate a new secret")
        return 0
    lines = []
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line == f"{GENERATED}=":
            line = f"{GENERATED}={secrets.token_urlsafe(32)}"
        lines.append(line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {target.name} with a random {GENERATED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
