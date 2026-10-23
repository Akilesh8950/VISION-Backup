from __future__ import annotations

import json
from pathlib import Path

from .case_schema import BenchCase, ObservedResult
from .scoring import score_case, summarize


def load_observed(path: str | Path) -> dict[str, ObservedResult]:
    observed: dict[str, ObservedResult] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = row.pop("case_id")
        observed[case_id] = ObservedResult.model_validate(row)
    return observed


def run_replay(cases: list[BenchCase], observed: dict[str, ObservedResult]) -> dict:
    results = [score_case(case, observed.get(case.case_id, ObservedResult())) for case in cases]
    return {"summary": summarize(results), "results": results}
