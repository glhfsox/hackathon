"""Run source generation, PostgreSQL loading, RAG indexing, search, and the guarded agent."""

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
    parser = argparse.ArgumentParser(description="Synthetic treasury corpus and RAG demo")
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
    for command in ("index", "search", "agent"):
        rag_parser = subparsers.add_parser(command, help=f"Run treasury RAG {command}")
        rag_parser.add_argument(
            "--config", type=Path, default=Path(__file__).with_name("rag-config.json")
        )
        rag_parser.add_argument("--schema", default="demo_data")
        if command == "index":
            rag_parser.add_argument("--rebuild", action="store_true")
        else:
            rag_parser.add_argument("question", help="Search query or agent question")
            rag_parser.add_argument("--client-id", required=True)
            if command == "search":
                rag_parser.add_argument("--transaction-id")
                rag_parser.add_argument("--k", type=int, default=5)
            else:
                rag_parser.add_argument("--model", default=os.environ.get("DEMO_AGENT_MODEL"))
                rag_parser.add_argument("--max-steps", type=int, default=6)
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
        elif args.command == "load-postgres":
            database_url = os.environ.get("DEMO_DATABASE_URL")
            if not database_url:
                raise ValueError("set DEMO_DATABASE_URL before loading PostgreSQL")
            print(json.dumps(load_postgres(args.directory, database_url, args.schema)))
        else:
            from demo_data.rag_models import RagConfig, SearchArgs

            database_url = os.environ.get("DEMO_DATABASE_URL")
            if not database_url:
                raise ValueError("set DEMO_DATABASE_URL before using RAG")
            config = RagConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
            if args.command == "agent" and (not args.model or not os.environ.get("DEMO_API_KEY")):
                raise ValueError(
                    "set DEMO_API_KEY and DEMO_AGENT_MODEL (or --model) before running agent"
                )
            try:
                from demo_data.rag import TreasuryTools, index_documents

                if args.command == "index":
                    result = index_documents(
                        database_url, config, args.schema, rebuild=args.rebuild
                    )
                else:
                    tools = TreasuryTools(database_url, args.client_id, config, args.schema)
                    if args.command == "search":
                        result = tools.search_documents(
                            SearchArgs(
                                query=args.question, k=args.k, transaction_id=args.transaction_id
                            )
                        )
                    else:
                        import httpx

                        from demo_data.agent import run_agent

                        base_url = (
                            os.environ.get("DEMO_CONTROL_BASE_URL") or "http://127.0.0.1:8000/v1"
                        )
                        with httpx.Client(
                            base_url=base_url.rstrip("/") + "/", timeout=600, follow_redirects=False
                        ) as client:
                            run = run_agent(
                                client,
                                api_key=os.environ["DEMO_API_KEY"],
                                model=args.model,
                                question=args.question,
                                run_tool=tools.run,
                                max_steps=args.max_steps,
                            )
                        result = run.model_dump()
                        print(json.dumps(result, ensure_ascii=False))
                        return 1 if run.status in ("error", "limit") else 0
                print(json.dumps(result, ensure_ascii=False))
            except ImportError as exc:
                raise ValueError("install demo_data/requirements-rag.txt before using RAG") from exc
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
