"""Stand-in for `codex app-server`: JSON-RPC, one message per line. Usage: fake_codex_app_server.py <result.json> [silent]."""

import json
import sys
import time

result_path = sys.argv[1]
silent = len(sys.argv) > 2 and sys.argv[2] == "silent"

for line in sys.stdin:
    try:
        message = json.loads(line)
    except ValueError:
        continue
    if silent:
        time.sleep(60)
        continue
    if message.get("method") == "initialize":
        reply = {"id": message["id"], "result": {"userAgent": "fake"}}
    elif message.get("method") == "account/rateLimits/read":
        with open(result_path, encoding="utf-8") as handle:
            reply = {"id": message["id"], "result": json.load(handle)}
    else:
        continue
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
