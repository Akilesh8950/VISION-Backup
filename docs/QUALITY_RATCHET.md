# VISION — Quality Ratchet (falsification kit + dogfood gate)

VISION — built by SPIRIT ORG

Purpose: make every load-bearing behaviour claim **falsifiable before it is
built on**. This document is written at implementation-planning rigour **60%**:
threshold *numbers* are deliberately not invented here; they are set from the
first measured baseline (milestone M0 replay + first Windows live run). What
is fixed now is the machinery, the metric definitions, and the rule that a
build never ships worse than its baseline without a written, approved exception.

## 1. Build order

1. **Falsification kit first** (`bench/`, `scripts/run_bench.py`,
   `src/vision_app/bench/`): case format, scoring functions, replay driver.
   It runs against recorded SSE fixtures with fake tools — no GPU, no UI.
2. **Baseline on wobbly current output**: record the raw streams from a stock
   local `llama-server` + the current GGUF; store as fixtures; publish the
   per-stratum counts below. Imperfect scores are expected and recorded, not hidden.
3. **Only then** client features (PLAN M1+). Each milestone re-runs replay;
   The final build milestone and every subsequent model/data push also re-run live.

## 2. Sensitivity check (is the kit itself measuring anything?)

Before any baseline is trusted, the kit must pass a baseline-sensitivity test
(`tests/test_bench_scoring.py`):

- perturb exactly **one** parameter in a known-good case (e.g. flip one
  expected relation word in the reference output, truncate one tool-arg fragment,
  change one tool-result number) → the *matching* metric must move and the
  unrelated metrics must not.
- If a perturbation moves nothing, the metric is dead and gets fixed before use.

This proves the benchmark output is **genuine** (computed from inputs), not a
faithful-looking template with numbers pasted in.

## 3. Generator-side ground truth (why the app does not "plan" model output)

The model generates whatever it generates; the app cannot pre-plan its token
stream. So the app delegates every *verifiable* surface to deterministic,
validated code — and measures the rest:

| Surface | Delegated to ground truth | Illustrative style (never shipped as truth) |
|---|---|---|
| Tables/charts/cards/timeline/diff | pydantic-validated `ui_*` payloads rendered by native Qt widgets; row/column lengths, enum chart types, numeric values all type-checked before render | rendering raw model JSON/text as if it were a widget |
| Tool arguments | pydantic schemas for all 27 tools; invalid → corrective tool reply (max 2 retries) | passing unvalidated args to executors |
| Weather/stocks/crypto/system numbers | values rendered in tool cards come from the tool result, not from assistant prose | trusting numbers typed out in the answer |
| Anything numeric/relational in **prose** | not delegable ⇒ measured by the entailment / fabrication / false-precision metrics (§4) and flagged for `/fix` when wrong | eyeballing a couple of chats and calling it quality |

## 4. Metrics (definitions frozen with the case format; counts are per build)

For each stratum, report `defect_count / cases` as a **count**, never a bare
success %. ("8/10 entailment-clean" = 2 defects — say that.)

| ID | Metric | Definition (scripted where possible) | Telemetry twin |
|---|---|---|---|
| D1 | Entailment violations | answer states a relation between entities/values that contradicts the case's source (tool-result JSON or prompt fixture). Scripted: expected-relation assertions per case (`must_state` / `must_not_state` templates over source fields). Unscriptable residue → single human rater, notes committed with the run. | `user_negative` (via `/fix`) |
| D2 | Fabricated attributes/provenance | answer or `ui_*` payload contains a field/value absent from source data (extra card field, invented dosage-like figure, "Source: …" line with no tool call behind it). Scripted: payload key/value set-difference vs source. | `user_negative` |
| D3 | False precision | answer emits an exact-looking quantity (count, %, rating, statistic) that is not in the case's `allowed_numbers` set (sourced from prompt/tool results). Scripted: number extraction + set membership; tolerances declared per case, never implicit. | `user_negative` |
| D4 | Tool-schema / runtime failures | arg validation errors, corrective-retry counts, exhausted retries, handler exceptions, unknown-tool blocks. Fully scripted from the registry. | `tool_schema_error`, `tool_runtime_error`, `code_error` |
| D5 | Tool-choice errors | expected tool not called, wrong tool called, or a `ui_*` widget emitted where prose was asked (and vice versa). Scripted from case `expected.tool_calls`. | `user_negative`, `topic_usage` |
| D6 | Extension trust/consent violations | an MCP/connector call executes without its required consent, a changed MCP toolset remains enabled, a skill script runs on import or without hash approval, or archive extraction escapes the skill staging dir. Fully scripted in unit/bench fixtures. **Release gate is exactly 0; it is not baseline-relative.** | `tool_runtime_error` plus audit security event |

## 5. Case format v1 (`bench/cases/*.jsonl`, one JSON object per line)

```json
{
  "case_id": "live-weather-in",
  "stratum": "live_tools",
  "format_version": 1,
  "prompt": "What's the weather in Coimbatore?",
  "scripted_tool_results": {"get_weather": {"city": "Coimbatore", "temp_c": 31.0, "condition": "Partly cloudy", "humidity_pct": 62}},
  "expected": {
    "tool_calls": [{"name": "get_weather", "args_subset": {"city": "Coimbatore"}}],
    "answer_must_mention": ["31"],
    "allowed_numbers": [31, 62],
    "relations_must_hold": [],
    "genui_widget": "cards_or_none"
  },
  "notes": "number tolerance: exact (value comes from tool result)"
}
```

Rules that protect the trend line:

- The **scored field** (prompt text + expectations) is immutable once a case
  has a baseline. Need a different prompt? Add `live-weather-in-2`; re-score
  the whole set; keep the old case.
- Renaming a prompt *category* in telemetry does **not** retroactively change
  bench results. Any taxonomy change ⇒ version bump + full re-score, and the
  overwrite is called out in the commit message.
- Fixture streams (`bench/fixtures/`) are likewise append-only; a new model
  produces *new* fixture files, old ones stay for regression replay.

## 6. The ratchet (per training-data / model push, after M8)

Each push is **one commit** containing: the new benchmark run report
(per stratum: baseline count / new count / diff), the model SHA it was run
against, and any new cases added. Sequence:

1. captured/dreamed data change prepared (dataset repo side),
2. `scripts/run_bench.py --mode live --runs 3` against the candidate GGUF,
3. diff vs baseline reviewed — any stratum regression blocks release unless
   a written exception is approved,
4. **dogfood gate**: `scripts/build.ps1` output installed on the owner's
   Windows PC, used for a genuine task (a real session, not demos), every
   defect actually felt filed (as bench cases where reproducible),
5. only then the build is called a release, and its scores become the new baseline.

Minimum starting coverage before the first release claim (to grow after):
≥6 cases per stratum across `live_tools`, `code_files`, `genui`,
`windows_tools`, `memory`, `safety_refusals`, `plain_chat_fidelity`,
`skills_activation`, `mcp_tool_choice`, `extension_prompt_injection`,
`project_rag_fidelity`, `code_workspace_safety`, `web_citations`
(the last exists specifically to catch D1–D3 in prose with no tools involved).

## 7. Worked bar-raising example (the pattern, applied to a chat app)

When a defect class is found — e.g. "the answer says Chennai is hotter than
Coimbatore when the tool results say the opposite" (a D1 violation) — the fix
is **not** considered done when one chat looks right. The bar is restated as:

- source-constrained generation where delegable (comparisons rendered by
  `ui_table`/`ui_cards` from tool numbers, not prose),
- a new bench stratum of pairwise-comparison cases with scripted
  `relations_must_hold` assertions, scored on the next 3 live runs,
- and the specific failing conversation added as a regression case.

"Looks right in one chat" is rejected as a ship gate for the same reason a
single happy-path compile is rejected: untested paths are where the remaining
defects live, and an unmeasured rate is not evidence the rate is low.

## 8. Honest-reporting rules

- Report counts + denominators (`3 false-precision defects / 21 fidelity
  cases`), never a percentage alone.
- Report error bars implicitly by repeating live runs (`--runs 3`) when the
  model's sampling makes a single run unstable; if runs disagree, say so.
- "Not measured yet (no baseline on this hardware)" is an acceptable status;
  an invented threshold is not.
- Benchmarks that cannot run here (anything needing a real GPU/Windows) are
  listed as **blocked here**, with the smoke-step number that covers them.
