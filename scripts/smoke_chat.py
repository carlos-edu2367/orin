"""Drive one real conversation against a running local stack and report it.

This is the manual end-to-end probe: it talks to the HTTP API exactly as the web
client does, waits for the durable turn to reach a terminal state, and prints the
messages plus the activity the UI would render.

    python scripts/smoke_chat.py "your prompt here" [--model MODEL] [--timeout 120]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4


BASE = "http://127.0.0.1:8000"
TERMINAL = {"completed", "failed", "cancelled"}


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
        request.add_header("Idempotency-Key", uuid4().hex)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {path} -> {error.code} {error.read().decode('utf-8', 'replace')}") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("message")
    parser.add_argument("--provider", default="openrouter")
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--conversation", default=None, help="Continue an existing conversation instead of creating one.")
    arguments = parser.parse_args()

    model = arguments.model
    if model is None:
        catalog = call("GET", f"/v1/providers/{arguments.provider}/models")
        if not catalog["items"]:
            raise SystemExit(f"No models available for {arguments.provider}; configure the provider first.")
        model = catalog["items"][0]["model_id"]

    if arguments.conversation:
        conversation_id = arguments.conversation
        call("POST", f"/v1/conversations/{conversation_id}/messages", {"message": arguments.message})
    else:
        receipt = call("POST", "/v1/conversations", {
            "message": arguments.message,
            "selection": {"provider": arguments.provider, "model_id": model},
        })
        conversation_id = receipt["conversation_id"]
    print(f"conversation: {conversation_id}\nmodel: {model}\n", flush=True)

    deadline = time.monotonic() + arguments.timeout
    snapshot: dict = {}
    while time.monotonic() < deadline:
        snapshot = call("GET", f"/v1/conversations/{conversation_id}")
        if snapshot["state"] in TERMINAL:
            break
        time.sleep(1.0)

    print(f"state: {snapshot.get('state')}\n")
    for message in snapshot.get("messages", []):
        print(f"[{message['role']}/{message['status']}]\n{message['content']}\n")
    print("--- activity ---")
    for event in snapshot.get("activities", []):
        payload = event.get("payload") or {}
        extra = {key: payload[key] for key in ("tool_name", "status", "label", "agent_name", "error_code") if key in payload}
        print(f"  {event['event_type']:<26} {event['summary'][:70]:<72} {extra}")
    overview = call("GET", f"/v1/conversations/{conversation_id}/overview")
    print("\n--- overview ---")
    print(f"  agents: {[item['name'] for item in overview['agents']]}")
    print(f"  tools : {[(item['tool_name'], item['count'], item['failures']) for item in overview['tools']]}")
    print(f"  agent-to-agent messages: {len(overview['messages'])}")
    print(f"  duration: {overview['duration_seconds']}s")
    return 0 if snapshot.get("state") == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
