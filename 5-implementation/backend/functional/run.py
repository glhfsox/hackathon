"""Live functional cases against a running control layer (real Jev and the real model).

    docker compose up -d --build backend                 # or uvicorn, see the root README
    cd backend && set -a && . ../.env && set +a          # JWT_SECRET, as the layer sees it
    uv run python functional/run.py                      # every case
    uv run python functional/run.py -k rbac -k run_sql   # cases whose name or group matches

Not part of pytest: the cases cost model and Jev calls and the model is not deterministic. The
exit code is 1 when a case fails, so a failure is a finding to look at, not a flaky test to retry.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import jwt
import yaml

CASES = Path(__file__).with_name("cases.yaml")
TOKEN_TTL_S = 3600


def tool_definition(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    params = spec["params"]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": spec["description"],
            "parameters": {
                "type": "object",
                "properties": {key: {"type": kind} for key, kind in params.items()},
                "required": list(params),
            },
        },
    }


def wire_call(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call["id"],
        "type": "function",
        "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
    }


def build_messages(case: dict[str, Any]) -> list[dict[str, Any]]:
    if "messages" in case:
        messages = []
        for message in case["messages"]:
            message = dict(message)
            if message.get("tool_calls"):
                message["tool_calls"] = [wire_call(call) for call in message["tool_calls"]]
            messages.append(message)
    else:
        messages = [{"role": "user", "content": case["prompt"]}]
    if history := case.get("history"):
        for i in range(1, history["times"] + 1):
            arguments = {
                key: value.replace("{i}", str(i)) if isinstance(value, str) else value
                for key, value in history["arguments"].items()
            }
            call = {"id": f"h{i}", "name": history["tool"], "arguments": arguments}
            messages.append({"role": "assistant", "content": None, "tool_calls": [wire_call(call)]})
            messages.append({"role": "tool", "tool_call_id": f"h{i}", "content": history["result"]})
    return messages


def bearer(case: dict[str, Any], secret: str) -> str | None:
    user = case.get("user", {"sub": "functional", "roles": ["developer"]})
    claims: dict[str, Any] = {**user, "exp": int(time.time()) + TOKEN_TTL_S}
    match case.get("token"):
        case None:
            return jwt.encode(claims, secret, algorithm="HS256")
        case "missing":
            return None
        case "garbage":
            return "not-a-jwt"
        case "expired":
            return jwt.encode({**claims, "exp": int(time.time()) - 60}, secret, algorithm="HS256")
        case "wrong_secret":
            return jwt.encode(claims, secret + "-other", algorithm="HS256")
        case "no_roles":
            claims.pop("roles")
            return jwt.encode(claims, secret, algorithm="HS256")
        case other:
            raise ValueError(f"unknown token kind {other!r}")


def outcome(body: dict[str, Any]) -> dict[str, Any]:
    """The final verdict, what fired and what the reply asks to run, from a proxy answer."""
    message = body["choices"][0]["message"]
    decisions = body["control"]["decisions"]
    blocking = next((d for d in decisions if d["action"] == "block"), None)
    fired = [
        f"{d['checkpoint']}:{r['check']}={r['action']}" for d in decisions for r in d["results"]
    ]
    judged = [r.get("decided_by") for d in decisions for r in d["results"] if r["check"] == "jev"]
    return {
        "action": "block" if blocking else "allow",
        "blocked_by": blocking["blocked_by"] if blocking else None,
        "checkpoint": blocking["checkpoint"] if blocking else None,
        "fired": fired,
        "judged": judged,
        "calls": [call["function"]["name"] for call in message.get("tool_calls") or []],
        "text": message.get("content") or "",
    }


def problems(expect: dict[str, Any], status: int, got: dict[str, Any] | None) -> list[str]:
    wrong: list[str] = []
    if status != expect.get("status", 200):
        return [f"status {status}, expected {expect.get('status', 200)}"]
    if got is None:
        return wrong
    if "action" in expect and got["action"] != expect["action"]:
        by = f" (by {got['blocked_by']})" if got["blocked_by"] else ""
        wrong.append(f"action {got['action']}{by}, expected {expect['action']}")
    if "blocked_by" in expect and got["action"] == "block":
        if got["blocked_by"] not in expect["blocked_by"]:
            wrong.append(f"blocked by {got['blocked_by']}, expected one of {expect['blocked_by']}")
    if "checkpoint" in expect and got["action"] == "block":
        if got["checkpoint"] not in expect["checkpoint"]:
            wrong.append(f"blocked at {got['checkpoint']}, expected at {expect['checkpoint']}")
    for entry in expect.get("fired", []):
        checkpoint, _, rest = entry.partition(":")
        if not any(
            f.endswith(f":{rest}") if checkpoint == "*" else f == entry for f in got["fired"]
        ):
            wrong.append(f"{entry} did not fire")
    for name in expect.get("calls", []):
        if name not in got["calls"]:
            wrong.append(f"no {name} call in the reply")
    for name in expect.get("no_calls", []):
        if name in got["calls"]:
            wrong.append(f"the reply calls {name}")
    if (text := expect.get("reply_contains")) and text not in got["text"]:
        wrong.append(f"reply does not contain {text!r}")
    return wrong


def run_case(
    client: httpx.Client, case: dict[str, Any], tools: dict[str, Any], model: str, secret: str
) -> tuple[bool, str, float]:
    body: dict[str, Any] = {
        "model": case.get("model", model),
        "messages": build_messages(case),
        "temperature": 0,
    }
    if names := case.get("tools"):
        body["tools"] = [tool_definition(name, tools[name]) for name in names]
    if forced := case.get("force"):
        body["tool_choice"] = {"type": "function", "function": {"name": forced}}
    if case.get("stream"):
        body["stream"] = True
    token = bearer(case, secret)
    headers = {"authorization": f"Bearer {token}"} if token else {}
    started = time.perf_counter()
    response = client.post("/v1/chat/completions", json=body, headers=headers)
    elapsed = time.perf_counter() - started
    got = outcome(response.json()) if response.status_code == 200 else None
    wrong = problems(case["expect"], response.status_code, got)
    if got is None:
        detail = f"HTTP {response.status_code}"
    else:
        where = f" at {got['checkpoint']} by {got['blocked_by']}" if got["blocked_by"] else ""
        not_allow = [f for f in got["fired"] if not f.endswith("=allow")]
        detail = f"{got['action']}{where}; calls={got['calls']}; fired={not_allow or '-'}"
        if got["judged"]:
            detail += f"; jev by {sorted(set(filter(None, got['judged'])))}"
        if got["action"] == "block":
            detail += f"\n        reply: {' '.join(got['text'].split())[:150]}"
    if wrong:
        detail += "\n        FAIL: " + "; ".join(wrong)
    return not wrong, detail, elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--base-url", default=os.environ.get("FUNCTIONAL_BASE_URL", "http://localhost:8000")
    )
    parser.add_argument("--model", default=os.environ.get("FUNCTIONAL_MODEL", "gpt-4o-mini"))
    parser.add_argument(
        "-k", action="append", default=[], help="run cases whose name or group contains this"
    )
    args = parser.parse_args(argv)
    if not (secret := os.environ.get("JWT_SECRET")):
        print("set JWT_SECRET to the secret the layer verifies tokens with", file=sys.stderr)
        return 2

    spec = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    cases = [
        case
        for case in spec["cases"]
        if not args.k or any(k in case["name"] or k in case["group"] for k in args.k)
    ]
    passed, started = 0, time.perf_counter()
    with httpx.Client(base_url=args.base_url, timeout=180) as client:
        health = client.get("/api/health").json()
        print(f"layer {args.base_url}: {health}; model {args.model}; {len(cases)} cases\n")
        for number, case in enumerate(cases, 1):
            try:
                ok, detail, elapsed = run_case(client, case, spec["tools"], args.model, secret)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                ok, detail, elapsed = False, f"FAIL: {type(exc).__name__}: {exc}", 0.0
            passed += ok
            mark = "PASS" if ok else "FAIL"
            print(f"{number:>2} {mark} [{elapsed:4.1f}s] {case['group']}: {case['name']}")
            print(f"        {detail}")
    print(f"\n{passed}/{len(cases)} passed in {time.perf_counter() - started:.0f}s")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
