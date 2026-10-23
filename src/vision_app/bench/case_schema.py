from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ExpectedToolCall(BaseModel):
    name: str
    args_subset: dict[str, Any] = Field(default_factory=dict)


class Expected(BaseModel):
    tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    answer_must_mention: list[str] = Field(default_factory=list)
    answer_must_not_mention: list[str] = Field(default_factory=list)
    allowed_numbers: list[float] | None = None
    relations_must_hold: list[str] = Field(default_factory=list)
    genui_widget: str | None = None


class BenchCase(BaseModel):
    case_id: str
    stratum: str
    format_version: int = 1
    prompt: str
    scripted_tool_results: dict[str, Any] = Field(default_factory=dict)
    expected: Expected = Field(default_factory=Expected)
    notes: str = ""


class ObservedResult(BaseModel):
    answer: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    genui_widget: str | None = None
    schema_errors: int = 0
    runtime_errors: int = 0
    consent_violations: int = 0


def load_cases(path: str | Path) -> list[BenchCase]:
    cases: list[BenchCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(BenchCase.model_validate_json(line))
    return cases


def dump_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
