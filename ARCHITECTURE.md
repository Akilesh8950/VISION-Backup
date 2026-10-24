# VISION DESKTOP — Architecture (draft v1.0; kept current as code lands)

VISION — built by SPIRIT ORG

> **Current UI implementation note:** the default renderer is now the
> offline React application in `app/frontend/`, built by Vite into
> `app/web/` and hosted by pywebview/WebView2 through the unchanged
> `vision_app.web_bridge` contract. The PySide6 view layer described below is
> retained as the `--qt` rollback/advanced-management surface. Shared backend
> services, transport, tools, safety, telemetry and state contracts remain as
> documented.

## 1. System overview

```
┌──────────────────────── VISION.exe (PySide6) ────────────────────────┐
│ UI thread                │ Chat worker QThread (asyncio loop)       │
│  MainWindow              │   ChatController (turn loop)             │
│  Transcript (listview)   │   httpx AsyncClient ──SSE──┐             │
│  Composer / Settings /   │   ToolRegistry (27 tools) │             │
│  Wizard / GenUI widgets  │     ├─ live data (ddgs / Open-Meteo /    │
│  Server console drawer   │     │    Yahoo Finance / CoinGecko)              │
│  Projects / Sources      │     ├─ projects + local knowledge (FTS) │
│        ▲ signals         │     ├─ code & files (sandbox)            │
│        └──────────────── │     ├─ memory (SQLite)                   │
│  Extensions Center       │   Extension host (worker-owned)        │
│  (Tools/MCP/Skills/      │     ├─ MCP sessions (stdio / HTTP)─────┼─▶ MCP servers
│   Connectors/Activity)   │     ├─ Skills (metadata → activation) │
│                          │     └─ Connectors (remote MCP facade) │
│  QProcess manager ───────┼────▶ llama-server.exe (child process)    │
│  Health timer (5 s)      │        127.0.0.1:8080, frozen arg list   │
└──────────────────────────┴──────────────────────────────────────────┘
              │ fallback (mode Auto/Cloud, after 2 failed health checks)
              └──────────────▶ remote OpenAI-compatible endpoint
                               (<remote base URL>/v1/chat/completions)
State (Standard): %LOCALAPPDATA%\VISION\{config.json, history.sqlite3, logs\,
                              models\, bin\, webview\, telemetry\needs.jsonl,
                              extensions\, skills\}
State (Portable): <install folder>\data\{same layout}
UI: docs/UI_SPEC.md · Extensions: docs/EXTENSIONS_SPEC.md · Features: docs/FEATURES_SPEC.md
Workspace: %USERPROFILE%\VISION\workspace (Standard) or
<install folder>\workspace (Portable)
```

## 2. Module map

| Module | Responsibility | Qt? | Windows-only? |
|---|---|---|---|
| `constants` | product/repo/file constants, credits line, min llama.cpp build | no | no |
| `paths` | app-data/workspace resolution: `VISION_HOME` → frozen `VISION.portable` marker (`<install>\data` + `<install>\workspace`) → standard OS locations | no | no (pure `pathlib` + env) |
| `config` | pydantic `AppConfig`, atomic JSON persistence | no | no |
| `credentials` | HF token / remote key via `win32cred` | no | guarded (memory fallback) |
| `redaction` | `hf_[A-Za-z0-9]{20,}`, `HF_TOKEN`, key patterns → `***` | no | no |
| `logging_setup` | rotating app log; append-only audit JSONL | no | no |
| `transport/schemas` | pydantic wire models for chat/messages/tools/deltas | no | no |
| `transport/sse` | bytes/lines → typed events; `<think>` state machine; tool-delta accumulation by index; `[DONE]`; truncation flag | no | no |
| `transport/client` | `POST /v1/chat/completions` (`stream:true`; all 27 built-ins plus any conversation-enabled extension tools), `GET /v1/models`; timeouts connect 5 s / first-token 120 s / total 600 s; cancellation | no | no |
| `transport/endpoints` | Local/Cloud/Auto selection, failover state machine, chip+ banner state | no | no |
| `server/discovery` | find `llama-server`: bundle dir → Settings path → `PATH` → app-data `bin\` | no | no |
| `server/download_llama` | pinned GitHub release asset selection (CUDA+Vulkan/CPU), download/extract to app-data `bin\` | signals only | no |
| `server/manager` | `QProcess` lifecycle: start with frozen args, capture stdout/stderr ring buffer (≤ ~2k lines), stop/restart, orphan-adopt check | yes | no |
| `server/health` | poll `/v1/models`/` every 5 s → 2 failures → one restart → offer/auto cloud failover with banner | signals | no |
| `history` | SQLite conversations/messages/memory/meta; redaction applied before insert | no | no |
| `projects` + `knowledge/*` | project records/instructions, selected sources, deterministic chunks, FTS5 retrieval + source locators | no | no |
| `code_workspace/*` | pre-write checkpoints, fixed read-only Git status calls, user-owned argv test profiles (not model tools) | no | no |
| proposed `tools/web_reader` | A23-guarded `read_url` fetch/extract + per-turn source IDs | no | no |
| `tools/schemas` | one pydantic model per tool; exports the OpenAI JSON-schema array | no | no |
| `tools/registry` | dispatch, validation → corrective `role:"tool"` errors (≤2 retries/turn), audit line, duration timing, unknown-tool block (`Blocked by VISION safety policy`) | no | no |
| `tools/sandbox` | workspace containment, `..`/system-path denial, confirmation policy decisions (pure predicate + UI callback) | no | no |
| `tools/live_data` | the 6 network tools (see PLAN §7) | no | no |
| `tools/code_files` | `run_python` (`[exe, -I, -S]`, 15 s, scrubbed env), read/write/list/search | no | no |
| `tools/memory` | `memory_get/set` over `history.memory_kv` | no | no |
| `tools/genui_tools` | validate `ui_*` args → plain payloads; UI builds the actual widgets | no | no |
| `tools/windows_tools` | app launch map, `os.startfile` fallback, `focus_window` (pywin32), clipboard (`pyperclip` + length gate), `system_status` (psutil + optional `nvidia-smi`), timers | no | guarded |
| `chat/controller` | one user turn: build messages → stream → run tool calls (parallel, ordered results) → follow-up request → persist; enforces "always a `role:'tool'` reply" | no | no |
| `chat/worker` | hosts asyncio loop in `QThread`; bridges controller callbacks → Qt signals | yes | no |
| `chat/commands` | `/fix …`, `/teach …` → telemetry write + inline confirmation | no | no |
| `ui/*` | windows/dialogs/transcript/composer; all I/O delegated to worker/manager | yes | no |
| `genui/*` | native widgets: sortable table, QtCharts chart, cards grid, timeline, diff; PNG/CSV export via `QFileDialog` | yes | no |
| `extensions/skills` + `skill_import` | validate canonical Agent Skills packages; normalize folder/.md/.zip/.skill; metadata-only discovery then bounded activation | no | no |
| `extensions/mcp_config` + `mcp_client` | versioned server records, secret references, official MCP SDK sessions for stdio/Streamable HTTP | no | no |
| `extensions/mcp_tools` + `trust` | namespaced dynamic tools, schema validation, toolset fingerprints, user-consent policy | no | no |
| `extensions/connectors` | named remote-MCP connector facade, auth refs, enable/revoke/egress metadata | no | no |
| `updater/model_updater` | startup async SHA check vs `SpiritKing/VISION-Ornith-1.5-9B-GGUF`; toast; `pending_restart` swap-on-next-launch | signals | no |
| `updater/downloads` | `hf_hub_download` wrapper: ≥7 GB free-space precheck, progress, size verify, resume | no | no |
| `telemetry/needs_log` | frozen record `{ts,category,prompt,model_output,signal,detail}`; buffer flush at 50 events and on close | no | no |
| `telemetry/uploader` | opt-in (default OFF) append-merge upload to the dataset repo, background thread, 3 retries, failure keeps local file; never uploads file contents/clipboard | no | no |
| `bench/*` | case schema v1, pure scoring (entailment/fabrication/false-precision/schema+runtime), replay driver with fake tools | no | no |

## 3. Sequence: one streaming turn with a tool call

"What's the weather in Coimbatore?" (local mode)

```
User        Composer     Controller (worker thread)     llama-server        ToolRegistry        UI (transcript)
 │  Enter      │                │                            │                   │                    │
 │────────────▶│ submit(text)   │                            │                   │                    │
 │             │───────────────▶│ build messages[] + project/skill context (see §7)          │                    │
 │             │                │  + 27 built-ins + enabled extension tool schemas           │                    │
 │             │                │ POST /v1/chat/completions  │                   │                    │
 │             │                │   stream:true ────────────▶│                   │                    │
 │             │                │◀── data: {delta.reasoning…}│ (think tokens)    │                    │
 │             │                │──── think_token signal ────────────────────────────────▶ muted italic, collapsed block
 │             │                │◀── data: {delta.tool_calls[0]                    │                    │
 │             │                │        .function.name="get_weather",             │                    │
 │             │                │        .arguments="{ \"cit" …} (fragments)        │                    │
 │             │                │ accumulate by index until finish_reason=tool_calls                   │
 │             │                │──── tool_call_started signal ───────────────────────────────────▶ tool card: name + pretty JSON + spinner
 │             │                │ validate args (pydantic)   │                   │                    │
 │             │                │────────────────────────────┼──────────────────▶│ get_weather        │
 │             │                │                            │   Open-Meteo ◀────│ geocode+forecast   │
 │             │                │◀───────────────────────────┼───────────────────│ result dict        │
 │             │                │ append assistant(tool_calls) + role:"tool" msg   │                    │
 │             │                │ audit.jsonl line (redacted args, ms)              │                    │
 │             │                │──── tool_result signal ────────────────────────────────────────▶ card: result summary
 │             │                │ POST /v1/chat/completions (updated messages)────▶│                    │
 │             │                │◀── data: {delta.content="In Coimbatore…"} … [DONE]                    │
 │             │                │──── token signals ────────────────────────────────────────────▶ live append, markdown render
 │             │                │ persist turn → history.sqlite3; topic_usage telemetry                │
 │  sees final │                │──── turn_done ─────────────────────────────────────────────────▶ copy / regenerate enabled
```

Failure branches on the same turn:

- Args fail pydantic → corrective `role:"tool"` error sent back (telemetry
  `tool_schema_error`); model retries; after 2 failed retries the controller
  stops and asks the user.
- Tool raises → caught in registry, short `role:"tool"` error sent back
  (telemetry `tool_runtime_error`; `run_python` non-zero exit → `code_error`).
  *No branch ever skips the tool reply* — the conversation stays valid.
- Stream dies mid-response → partial message persisted with `incomplete=true`,
  banner offers retry; health loop decides restart/failover.
- An MCP/connector call follows the same controller path, but dispatch goes
  through the extension host only after fingerprint + consent checks. A denial,
  protocol error or schema failure becomes an ordinary `role:"tool"` reply.
- A `ui_*` call follows the same path, except the "result" is a widget payload:
  the worker emits `genui_requested(payload)`; the UI thread builds the native
  widget inside the tool card and the model receives a short textual
  acknowledgement as its `role:"tool"` content (never raw JSON shown to user).

## 4. Key invariants

1. The UI thread never performs I/O; every network/subprocess/disk-heavy call
   originates outside it (checked by review + M3 test).
2. Every tool call produces exactly one `role:"tool"` message, one audit line,
   and (on failure classes) one telemetry record.
3. Secrets pass through `redaction` before *any* sink: transcript, history,
   audit, app log, telemetry, server-console display.
4. File tools resolve against the workspace first; any write shows the resolved
   absolute path; confirmations per PLAN/safety matrix. No delete, no process
   kill, no registry, in the API surface at all.
5. `needs.jsonl` field set and bench-case scoring inputs are versioned; a
   taxonomy change never rewrites history — it triggers a full re-score.
6. A downloaded model is only swapped in at next launch (`pending_restart`),
   never mid-conversation.
7. Tool availability is not consent. The model, skills and external tool results
   cannot install/enable an extension, change permissions or approve their own calls.
8. Skills use progressive disclosure: metadata may be discoverable, but full
   instructions/resources load only on activation and remain below system/safety priority.
9. Project instructions and retrieved sources are delimited context, not policy;
   test profiles and Git queries are app-owned operations, never model callables.

## 5. Resilience mapping (spec §7 → mechanism)

| Requirement | Mechanism |
|---|---|
| Health poll 5 s; 2 failures → restart once → failover | `server/health` state machine; Auto mode fails over, Local mode offers, Cloud mode skips local entirely; banner + endpoint chip always show current target (LOCAL / CLOUD) |
| SSE `data:` / `[DONE]` / tool deltas by index | `transport/sse` pure parser with fixture-based tests, incl. truncated and parallel-call streams |
| Timeouts 5 s / 120 s / 600 s; clean cancel | `transport/client` httpx timeout split + task cancellation; partial text retained, marked incomplete |
| Crash-safe tools | registry `try/except` around every handler; invariant §4-2 |

## 6. Quality instrumentation (summary; full design in docs/QUALITY_RATCHET.md)

`bench/cases/*.jsonl` (versioned) + `bench/fixtures/*.sse.jsonl` let the same
cases run in two modes: **replay** (no GPU — recorded streams + fake tools,
verifies client parse/dispatch/scoring) and **live** (real `llama-server` +
GGUF on Windows, verifies the model). Scoring separates tool-schema validity, entailment fidelity,
fabricated-attribute rate, false-precision rate, tool-choice errors, and
extension trust/consent violations (zero tolerance) — and every model/data push is gated on a re-run diff
against the previous baseline, one commit per push. Planning rigour baseline
for gates is **60%**: set numerical thresholds from the first real baseline;
only the non-regression direction is pre-committed.

## 7. Feedback and extension flows

### Feedback named in the frozen contract

There is no app file literally named `feedback.jsonl`; PLAN A14 locks the
frozen contract names. The planned queue is
`%LOCALAPPDATA%\VISION\telemetry\needs.jsonl`; the UI calls it Feedback,
and an opt-in background uploader append-merges it to the HF dataset at
`telemetry/needs.jsonl`, clearing locally acknowledged lines only after a
successful upload. An Export action may save the same JSONL under a user's
chosen `feedback.jsonl` filename without changing the training pipeline input.

### Extension turn construction

1. Conversation state selects enabled extensions.
2. Skill catalog contributes name/description metadata only.
3. Trusted, currently fingerprinted MCP/connector descriptors contribute
   namespaced tool schemas, subject to conversation caps.
4. Controller freezes the request tool array for the turn.
5. Registry enforces validation/consent/audit identically for built-in and
   extension tools; extensions additionally carry server/skill provenance and
   remote-egress metadata in their tool cards.

Full import, transport, trust and connector rules live in
`docs/EXTENSIONS_SPEC.md`; visible layout and consent surfaces live in
`docs/UI_SPEC.md`; the owner-selected F2/F4/F5/F15 bundle is detailed in
`docs/FEATURES_SPEC.md` and enters at PLAN M12–M15.

## 8. Windows packaging and installation modes

`scripts/build.ps1` creates the PyInstaller one-folder payload in
`dist\VISION`. `scripts/build_installer.ps1` refuses a payload containing a
GGUF, `data`, `workspace`, or `VISION.portable`, then compiles
`installer\VISION.iss` with Inno Setup solid LZMA2/Ultra64 and writes a
SHA-256 sidecar.

Setup asks current-user vs all-users, then Portable vs Standard data, then the
destination folder. Portable is available for current-user installs and writes
`VISION.portable`, `data\`, and `workspace\` at the destination. All-users
installs are forced to Standard mode so private per-user data is not shared
from Program Files. The WebView2 profile remains under the active app-data
root (`<app_data>\webview`), while secrets remain in Windows Credential
Manager in both modes. The same wrapper can emit a portable ZIP with the
marker and empty `data\`/`workspace\` directories. User/owner steps live in
`docs/INSTALLATION.md`.
