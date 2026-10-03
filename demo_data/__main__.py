"""Run from the repository root: python -m demo_data generate|validate."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from demo_data.generate import generate
from demo_data.models import GenerationConfig
from demo_data.storage import counts, export, validate_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic treasury corpus generator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generation = subparsers.add_parser("generate", help="Create a new corpus directory")
    generation.add_argument("--config", required=True, type=Path)
    generation.add_argument("--output", required=True, type=Path)
    validation = subparsers.add_parser("validate", help="Validate an existing corpus")
    validation.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            if args.output.exists():
                raise ValueError(f"output already exists: {args.output}; choose a new directory")
            config = GenerationConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
            corpus = generate(config)
            export(corpus, config, args.output)
            validate_directory(args.output)
            print(
                json.dumps(
                    {"status": "generated", "output": str(args.output), "counts": counts(corpus)}
                )
            )
        else:
            corpus, _ = validate_directory(args.directory)
            print(json.dumps({"status": "valid", "counts": counts(corpus)}))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
