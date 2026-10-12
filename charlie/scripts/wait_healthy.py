"""Wait until every compose service with a healthcheck reports healthy."""

from __future__ import annotations

import json
import subprocess
import sys
import time


def main(timeout_s: float = 240.0) -> int:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
        pending = [
            r["Service"]
            for r in rows
            if r.get("Health") not in ("healthy", "") and r["Service"] not in ("migrate",)
        ]
        if rows and not pending:
            print("all services healthy:", ", ".join(sorted(r["Service"] for r in rows)))
            return 0
        time.sleep(3)
    print("timeout waiting for:", pending)
    return 1


if __name__ == "__main__":
    sys.exit(main())
