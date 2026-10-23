#!/usr/bin/env python3
"""Run the VISION benchmark in replay mode, or report live-mode prerequisites."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vision_app.bench.case_schema import dump_json, load_cases  # noqa: E402
from vision_app.bench.replay import load_observed, run_replay  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["replay", "live"], default="replay")
    parser.add_argument("--cases", type=Path, default=ROOT / "bench/cases/core.jsonl")
    parser.add_argument("--observed", type=Path, default=ROOT / "bench/fixtures/observed_core.jsonl")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    if args.mode == "live":
        print(
            "Live mode requires a real Windows machine with llama-server and the GGUF. "
            "Record observed results with the app, then replay/score them here.",
            file=sys.stderr,
        )
        return 2

    report = run_replay(load_cases(args.cases), load_observed(args.observed))
    report = {"created_at": datetime.now(timezone.utc).isoformat(), **report}
    out = args.out or ROOT / "bench/runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(dump_json(report) + "\n", encoding="utf-8")
    lines = ["# VISION bench replay", "", f"Cases: {report['summary']['cases']}", ""]
    for stratum, data in report["summary"]["by_stratum"].items():
        lines.append(f"- {stratum}: {json.dumps(data, sort_keys=True)}")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out / "report.json")
    return 1 if report["summary"]["release_blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
