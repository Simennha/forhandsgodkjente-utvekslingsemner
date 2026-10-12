"""Demo against the running Docker Compose stack (Phase 0: health + isolation probe)."""

from __future__ import annotations

import argparse
import subprocess
import sys

import httpx


def probe_isolation() -> bool:
    ok = True
    for target in ("executor:8000", "agents-charlie:8000", "extractor:8000", "rules:8000"):
        host, port = target.split(":")
        code = (
            "import socket,sys\n"
            f"try:\n socket.create_connection(('{host}',{port}),timeout=2); sys.exit(1)\n"
            "except OSError: sys.exit(0)\n"
        )
        res = subprocess.run(
            ["docker", "compose", "exec", "-T", "agents-lola", "python", "-c", code], check=False
        )
        reachable = res.returncode != 0
        print(f"  lola -> {target}: {'REACHABLE (FEIL)' if reachable else 'blokkert'}")
        ok &= not reachable
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    print("1) Helse:")
    resp = httpx.get(f"{args.base}/health", timeout=5)
    print(f"  gateway: {resp.json()}")
    print("2) Nettsegmentering (Lola skal ikke nå Charlie-siden):")
    if not probe_isolation():
        return 1
    print("Demo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
