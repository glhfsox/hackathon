"""Run from the repository root: python -m demo_data generate|validate|load-postgres."""

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

from demo_data.generate import generate
from demo_data.models import GenerationConfig
from demo_data.postgres import load_postgres
from demo_data.storage import counts, export, validate_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic treasury corpus generator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    generation = subparsers.add_parser("generate", help="Create a new corpus directory")
    generation.add_argument("--config", required=True, type=Path)
    generation.add_argument("--output", required=True, type=Path)
    validation = subparsers.add_parser("validate", help="Validate an existing corpus")
    validation.add_argument("directory", type=Path)
    loading = subparsers.add_parser(
        "load-postgres", help="Load validated source data into PostgreSQL"
    )
    loading.add_argument("directory", type=Path)
    loading.add_argument("--schema", default="demo_data")
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
        elif args.command == "validate":
            corpus, _ = validate_directory(args.directory)
            print(json.dumps({"status": "valid", "counts": counts(corpus)}))
        else:
            database_url = os.environ.get("DEMO_DATABASE_URL")
            if not database_url:
                raise ValueError("set DEMO_DATABASE_URL before loading PostgreSQL")
            print(json.dumps(load_postgres(args.directory, database_url, args.schema)))
    except psycopg.Error as exc:
        # Driver diagnostics may contain credentials or source values; expose only safe codes.
        print(
            f"{args.command} failed: PostgreSQL {type(exc).__name__} "
            f"(SQLSTATE {exc.sqlstate or 'unavailable'}); check connection and schema setup",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
