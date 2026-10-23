#!/usr/bin/env python3
"""Probe an already-running OpenAI-compatible endpoint without starting the UI."""
from __future__ import annotations

import argparse
import json
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1")
    args = parser.parse_args()
    request = urllib.request.Request(args.base_url.rstrip("/") + "/models", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        print(json.dumps(json.load(response), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
