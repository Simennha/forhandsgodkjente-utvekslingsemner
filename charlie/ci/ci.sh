#!/usr/bin/env bash
# CI entry point: identical to what a developer runs locally.
set -euo pipefail
cd "$(dirname "$0")/.."
make install
make ci
