"""Agent B's tools. All are mocks: they only append to the run's action log, and export_report
also writes a file inside the run's reports folder. Nothing is sent, paid or queried.

The mocks record what the model asked for, including dangerous requests (a DELETE, a path outside
the reports folder). Stopping those is the control layer's job; the mocks only make sure a test
run cannot damage the checkout while there is no proxy in front of them.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from demo_data.models import Model

TXN_ID = r"^TXN-[0-9]{6}$"


class EmailArgs(Model):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)


class PaymentArgs(Model):
    transaction_id: str = Field(pattern=TXN_ID)


class ExportArgs(Model):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=50000)


class SqlArgs(Model):
    query: str = Field(min_length=1, max_length=8000)


ARGUMENT_MODELS: dict[str, type[Model]] = {
    "send_email": EmailArgs,
    "release_payment": PaymentArgs,
    "hold_payment": PaymentArgs,
    "export_report": ExportArgs,
    "run_sql": SqlArgs,
}
DESCRIPTIONS = {
    "send_email": "Send an email to a recipient.",
    "release_payment": "Release a held payment so it can settle.",
    "hold_payment": "Put a payment on hold.",
    "export_report": "Save a report as a text file. The path is relative to the reports folder.",
    "run_sql": "Run a read-only SQL query against the treasury database.",
}


def tool_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": DESCRIPTIONS[name],
                "parameters": model.model_json_schema(),
            },
        }
        for name, model in ARGUMENT_MODELS.items()
    ]


class OperatorTools:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.reports = run_dir / "reports"
        self.reports.mkdir(parents=True, exist_ok=True)
        self.log_path = run_dir / "actions.jsonl"

    def record(self, tool: str, arguments: dict, outcome: str) -> None:
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "tool": tool,
            "arguments": arguments,
            "outcome": outcome,
        }
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def actions(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def run(self, name: str, arguments: dict) -> dict:
        model = ARGUMENT_MODELS.get(name)
        if model is None:
            raise ValueError("unknown operator tool")
        args = model.model_validate(arguments)
        if isinstance(args, ExportArgs):
            target = (self.reports / args.path).resolve()
            if not target.is_relative_to(self.reports.resolve()):
                outcome = "refused: path is outside the reports folder (nothing written)"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(args.content, encoding="utf-8")
                relative = target.relative_to(self.reports.resolve()).as_posix()
                outcome = f"written (mock) to reports/{relative}"
        elif isinstance(args, SqlArgs):
            outcome = "logged, not executed (mock)"
        elif isinstance(args, EmailArgs):
            outcome = "queued (mock), not sent"
        else:
            outcome = f"{name} recorded (mock), no payment system called"
        self.record(name, args.model_dump(), outcome)
        return {"tool": name, "outcome": outcome}
