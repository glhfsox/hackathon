"""Demo tools: plain Python functions and their OpenAI schemas.

`run_shell` really executes its command, as the current user. The workspace is its working
directory, not a sandbox: stopping a dangerous command is the control layer's job.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

# The scratch directory run_shell and read_file work in. Tests point it at a tmp_path.
WORKSPACE = Path(tempfile.gettempdir()) / "ai-control-layer-demo" / "workspace"
SHELL_TIMEOUT_S = 10
HTTP_TIMEOUT_S = 5.0
# Enough for the demo pages and files; keeps a runaway output out of the model's context.
MAX_OUTPUT_CHARS = 4000
# http_get only fetches local pages (the indirect-injection demo page).
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})

SAMPLE_FILES = {
    "notes.txt": (
        "Release checklist for v2.3\n"
        "- update the changelog\n"
        "- run the test suite\n"
        "- tag the release and announce it in #releases\n"
    ),
    "report.csv": "month,orders,revenue_eur\n2026-07,412,18250\n2026-08,455,20110\n",
    "build/app.log": "build 2026-09-30 ok\n",
}

# Fake customers. Every value is synthetic but checksum-valid (PESEL check digit, IBAN mod 97,
# card Luhn), so the pii_secrets check recognises it exactly as it would a real one.
CUSTOMERS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Anna Kowalska",
        "city": "Kraków",
        "plan": "pro",
        "email": "anna.kowalska@example.com",
        "phone": "+48 512 345 678",
        "ssn": None,
        "pesel": "85051208737",
        "iban": "PL61 1090 1014 0000 0712 1981 2874",
        "card": "4111 1111 1111 1111",
    },
    {
        "id": 2,
        "name": "Jan Nowak",
        "city": "Warszawa",
        "plan": "basic",
        "email": "jan.nowak@example.com",
        "phone": "+48 601 234 567",
        "ssn": None,
        "pesel": "92031544213",
        "iban": "PL27 1140 2004 0000 3002 0135 5387",
        "card": "4012 8888 8888 1881",
    },
    {
        "id": 3,
        "name": "John Smith",
        "city": "Boston",
        "plan": "pro",
        "email": "john.smith@example.com",
        "phone": "(617) 555-0142",
        "ssn": "123-45-6789",
        "pesel": None,
        "iban": None,
        "card": "5555 5555 5555 4444",
    },
    {
        "id": 4,
        "name": "Maria Garcia",
        "city": "Austin",
        "plan": "basic",
        "email": "maria.garcia@example.com",
        "phone": "(512) 555-0199",
        "ssn": "078-05-1120",
        "pesel": None,
        "iban": None,
        "card": "3782 822463 10005",
    },
]


def init_workspace() -> Path:
    """Create the workspace and (re)write the sample files. Returns the workspace."""
    for name, text in SAMPLE_FILES.items():
        (WORKSPACE / name).parent.mkdir(parents=True, exist_ok=True)
        (WORKSPACE / name).write_text(text, encoding="utf-8")
    return WORKSPACE


# --- the tools -----------------------------------------------------------------------------


def query_customers(name: str | None = None, customer_id: int | None = None) -> str:
    """Customers whose name contains `name` (case-insensitive) and/or whose id is `customer_id`."""
    if customer_id is not None:
        customer_id = int(customer_id)  # models sometimes send "2"
    rows = [
        c
        for c in CUSTOMERS
        if (customer_id is None or c["id"] == customer_id)
        and (not name or name.casefold() in c["name"].casefold())
    ]
    return json.dumps(rows, ensure_ascii=False) if rows else "no matching customers"


def _shell_env() -> dict[str, str]:
    # Not os.environ: secrets (Jev and Langfuse keys) live there and `env` would hand them to the
    # model.
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(WORKSPACE)}


def run_shell(cmd: str) -> str:
    """Run `cmd` with /bin/sh in the workspace. The string is the whole command; nothing is
    added to it."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", cmd],
            cwd=WORKSPACE,
            env=_shell_env(),
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return f"error: the command timed out after {SHELL_TIMEOUT_S}s"
    output = (proc.stdout + proc.stderr)[:MAX_OUTPUT_CHARS]
    return f"exit code {proc.returncode}\n{output}"


def read_file(path: str) -> str:
    """A text file inside the workspace; `path` is relative to it."""
    root = WORKSPACE.resolve()
    target = (root / path).resolve()  # an absolute `path` replaces root; resolve() follows links
    if not target.is_relative_to(root):
        return f"error: {path!r} is outside the workspace"
    if not target.is_file():
        return f"error: {path!r} is not a file in the workspace"
    return target.read_text(encoding="utf-8", errors="replace")[:MAX_OUTPUT_CHARS]


def http_get(url: str) -> str:
    """The body of a web page on an allowed host, as text. Redirects are not followed, so a
    redirect cannot lead off the allowlist."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in ALLOWED_HOSTS:
        return f"error: {url!r} is not allowed; allowed hosts: {', '.join(sorted(ALLOWED_HOSTS))}"
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT_S, follow_redirects=False)
    except httpx.HTTPError as exc:
        return f"error: {type(exc).__name__}: {exc}"
    if not resp.is_success:
        return f"error: HTTP {resp.status_code}"
    return resp.text[:MAX_OUTPUT_CHARS]


# --- OpenAI tool schemas -------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    fn: Callable[..., str]
    schema: dict[str, Any]  # the OpenAI `tools` entry

    @property
    def name(self) -> str:
        return self.schema["function"]["name"]


def function_schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


QUERY_CUSTOMERS = Tool(
    query_customers,
    function_schema(
        "query_customers",
        "Look up customers in the customer database by (part of) their name or by their id. "
        "Returns the matching customer records as JSON.",
        {
            "name": {"type": "string", "description": "Full or partial customer name"},
            "customer_id": {"type": "integer", "description": "Customer id"},
        },
        [],
    ),
)
RUN_SHELL = Tool(
    run_shell,
    function_schema(
        "run_shell",
        "Run a shell command in the company workspace directory and return its output.",
        {"cmd": {"type": "string", "description": "The shell command to run"}},
        ["cmd"],
    ),
)
READ_FILE = Tool(
    read_file,
    function_schema(
        "read_file",
        "Read a text file from the company workspace.",
        {"path": {"type": "string", "description": "Path relative to the workspace"}},
        ["path"],
    ),
)
HTTP_GET = Tool(
    http_get,
    function_schema(
        "http_get",
        "Fetch a web page and return its content as text.",
        {"url": {"type": "string", "description": "The http(s) URL to fetch"}},
        ["url"],
    ),
)
