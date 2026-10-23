from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .case_schema import BenchCase, ObservedResult

NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?(?![A-Za-z0-9_])")


def _numbers(text: str) -> list[float]:
    values: list[float] = []
    for raw in NUMBER_RE.findall(text):
        try:
            values.append(float(raw))
        except ValueError:
            pass
    return values


def _args_match(expected: dict, observed: dict) -> bool:
    return all(observed.get(k) == v for k, v in expected.items())


def score_case(case: BenchCase, observed: ObservedResult) -> dict:
    defects: Counter[str] = Counter()
    details: list[str] = []

    observed_names = [str(c.get("name", "")) for c in observed.tool_calls]
    for expected in case.expected.tool_calls:
        match = next(
            (
                c
                for c in observed.tool_calls
                if c.get("name") == expected.name
                and _args_match(expected.args_subset, c.get("arguments") or {})
            ),
            None,
        )
        if match is None:
            defects["D5_tool_choice"] += 1
            details.append(f"missing/mismatched tool call {expected.name}; observed={observed_names}")

    low = observed.answer.lower()
    for phrase in case.expected.relations_must_hold:
        if phrase.lower() not in low:
            defects["D1_entailment"] += 1
            details.append(f"required relation missing: {phrase}")
    for phrase in case.expected.answer_must_not_mention:
        if phrase.lower() in low:
            defects["D1_entailment"] += 1
            details.append(f"forbidden phrase present: {phrase}")
    for phrase in case.expected.answer_must_mention:
        if phrase.lower() not in low:
            defects["D2_fabrication"] += 1
            details.append(f"required mention missing: {phrase}")

    if case.expected.allowed_numbers is not None:
        allowed = {round(x, 6) for x in case.expected.allowed_numbers}
        bad = [n for n in _numbers(observed.answer) if round(n, 6) not in allowed]
        if bad:
            defects["D3_false_precision"] += len(bad)
            details.append(f"unallowed numbers: {bad}")

    if case.expected.genui_widget not in (None, "any", "cards_or_none"):
        if observed.genui_widget != case.expected.genui_widget:
            defects["D5_tool_choice"] += 1
            details.append(
                f"genui widget {observed.genui_widget!r} != expected {case.expected.genui_widget!r}"
            )
    elif case.expected.genui_widget == "cards_or_none" and observed.genui_widget not in (None, "cards"):
        defects["D5_tool_choice"] += 1
        details.append("genui widget was neither cards nor omitted")

    if observed.schema_errors or observed.runtime_errors:
        defects["D4_schema_runtime"] += observed.schema_errors + observed.runtime_errors
    if observed.consent_violations:
        defects["D6_extension_trust"] += observed.consent_violations

    return {
        "case_id": case.case_id,
        "stratum": case.stratum,
        "defects": dict(defects),
        "defect_total": int(sum(defects.values())),
        "details": details,
    }


def summarize(results: Iterable[dict]) -> dict:
    by_stratum: dict[str, Counter[str]] = {}
    totals: Counter[str] = Counter()
    count = 0
    for result in results:
        count += 1
        stratum = result["stratum"]
        by_stratum.setdefault(stratum, Counter())
        by_stratum[stratum]["cases"] += 1
        for key, value in result["defects"].items():
            by_stratum[stratum][key] += int(value)
            totals[key] += int(value)
    return {
        "cases": count,
        "totals": dict(totals),
        "by_stratum": {k: dict(v) for k, v in sorted(by_stratum.items())},
        "release_blocked": totals.get("D6_extension_trust", 0) > 0,
    }
