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
| `telemetry/uploader` | opt-in (default OFF) timestamped-shard upload to the dataset repo `telemetry/inbox/`, background thread, 3 retries, failure keeps local file; never uploads file contents/clipboard | no | no |
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
 │             │                │ persist turn → history.sqlite3; failure-only telemetry                │
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
and an opt-in background uploader sends it to the HF dataset as
timestamped shards under `telemetry/inbox/`, clearing locally acknowledged
lines only after a successful upload. An Export action may save the same JSONL under a user's
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

`scripts/build.ps1` creates the PyInstaller one-folder app payload in
`dist\VISION`. `scripts/build_installer.ps1` refuses an app payload
containing a GGUF, `data`, `workspace`, or `VISION.portable`; builds the
stable outer launcher; stages it beside `versions\<version>\VISION.exe` and
`current.json` in `dist\installer-payload`; then compiles
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

## 9. Three-channel auto-update system

VISION has three independent update channels.  They share one UI facade,
`vision_app.updates.service.UpdateService`, but do not share failure modes or
apply timing.  The GitHub repository (`Akilesh8950/VISION`) is the
**app-update** authority only — manifests, release ZIPs, and the live remote
config that ships with them; it never hosts model files or model-update
metadata.  The model channel talks exclusively to Hugging Face.

| Channel | Source | Apply timing | Safety boundary |
|---|---|---|---|
| App | `latest.json` / `latest-dev.json` on `main`, pointing to a GitHub Release ZIP | Download in the background; apply only after the user chooses **Restart to apply** | SemVer + SHA-256 + safe ZIP extraction + `VISION.exe` check; launcher consumes `pending.json` only after process exit |
| Model | Hugging Face model metadata for `SpiritKing/VISION-Ornith-1.5-9B-GGUF` | Stage now; activate on next launch | Existing resumable `hf_hub_download` path, expected-size check, >5,000,000,000 byte floor, pending-model swap before `llama-server` starts |
| Config | `updates/config.json` / `updates/config-dev.json` on `main` | Live, without restart | Pydantic validation; complete 27-tool schema set; malformed data rejected and bundled/cached config stays active |

`APP_VERSION` is `1.0.0`.  The stable app manifest is
`https://raw.githubusercontent.com/Akilesh8950/VISION/main/latest.json`; the
dev manifest replaces `latest.json` with `latest-dev.json`.  The command line
flags `--dev-channel` and `--offline` select the dev endpoints or suppress all
update networking for one launch.  Settings → Updates also stores an
auto-check preference (default on) and a stable/dev channel preference.
Checks use a three-second timeout and run on bridge worker threads, never on
the WebView UI thread.  When offline they produce no modal error and simply
leave the last-known status in place.

### On-disk update layout

A self-updating portable install has the small launcher at the outer root and
versioned onedir app payloads below it:

```text
VISION\
  VISION.exe                 outer launcher_stub.py build (stable; compiled rarely)
  _internal\...              launcher runtime files from its onedir build
  current.json               launcher selection: {version, previous, model_sha, last_checked}
  pending.json               one-time verified-update handoff, consumed after exit
  launcher-state.json        crash counts + one-time rollback notice marker
  downloads\
    1.1.0.zip.part           resumable in-progress app ZIP
    1.1.0.zip                SHA-256-verified app ZIP
  versions\
    1.0.0\VISION.exe          PyInstaller --onedir payload
    1.1.0\VISION.exe          next PyInstaller --onedir payload
  data\                      portable app data (standard installs use LOCALAPPDATA)
    config.json              normal app config, including pending-model fields
    updates\state.json        service state mirror {version, previous, model_sha, last_checked}
    updates\remote-config.json validated remote-config cache
    models\...               installed/pending GGUF files
  workspace\
```

The running app never rewrites `current.json`.  It may write
`pending.json` only after the ZIP hash matches, extraction into
`versions\<version>.tmp` succeeds, the directory is atomically promoted with
`os.replace()`, and `versions\<version>\VISION.exe` exists.  Before a
download is accepted, the service requires at least `zip_size * 2.2` free
space; interrupted downloads resume from `.zip.part` with an HTTP `Range`
request.  One complete retry is allowed after a SHA-256 mismatch; a second
mismatch deletes the archive and stages nothing.  If a manifest requires a
launcher newer than the installed stub, the app refuses the package and tells
the user to install the latest setup manually.

`launcher_stub.py` is standard-library-only.  On launch it reads
`current.json`, starts `versions\<version>\VISION.exe`, and waits.  After the
child exits it consumes `pending.json`, atomically rewrites `current.json`,
and relaunches the staged version.  If the selected version exits non-zero
within 60 seconds, it retries once; a second quick failure rolls
`current.json` back to `previous`, writes/launches that version, and shows one
rollback notice for that bad version.  After a successful launch it prunes
extracted versions to current + previous (plus any still-pending version).

The Model row never swaps the GGUF mid-conversation.  A download goes to the
existing pending-model location and sets the pending-restart fields; the next
launch activates it before the server starts.  The activation path keeps its
swap-time backup recovery: if the pending file is missing or the move fails,
the previous GGUF is restored/kept and the failure is reported instead of
leaving the install model-less.

The Config channel applies only validated data.  Its payload contains a
version, the system prompt, all 27 baseline tool schemas, and a small
allowlisted set of UI strings.  Tool descriptions/schemas from remote config
replace only the model-facing copies; local Pydantic argument models,
handlers, consent checks, and audit remain authoritative in the installed
binary.  A malformed payload is rejected wholesale and the bundled or last
valid cached config remains live.

### Release ritual

1. Bump `src/vision_app/__init__.py`, `assets/version_info.txt`, the installer
   fallback version, and `APP_VERSION` together if the baked app version
   changes; regenerate `updates/config.json` when bundled prompts/schemas/UI
   strings change.
2. Run the full test suite and the local update proof:
   `python -m pytest tests/test_updates.py -q` (its local HTTP server
   advertises a fake `9.9.9` app release and proves resume, hash handling,
   safe extraction, pending handoff, and launcher rollback).
3. On Windows, run `scripts\release.ps1 -BuildFrontend -Notes "..."`.  It
   builds the app through the existing PyInstaller `--onedir` path (never
   `--onefile` for the app payload), builds the launcher, zips the version
   payload, hashes it with `Get-FileHash`, and rewrites `latest.json` or
   `latest-dev.json`.
4. Inspect the ZIPs, then re-run with `-Publish` so `gh release upload`
   publishes the assets.  Commit/push the rewritten manifest only after the
   upload succeeds; otherwise clients would see a manifest whose asset does
   not exist yet.
5. Installs that predate the launcher layout need one run of the new setup
   (or extraction of the generated launcher ZIP) to migrate to the outer
   launcher + `versions\` tree.  After that, app updates are staged by the
   running app and applied by the outer launcher.

`setup_helper\VISION-Create-Folders.exe` is a separate convenience for the
manual build tutorial: the user picks one parent folder and it creates
`VISION-Source` (source/build only) and `VISION` (final install only).  It
does not download or install VISION.

## 10. Feedback telemetry and unified sync

The feedback loop and the three update channels share one scheduler,
`vision_app.sync_service.SyncService`, but retain separate data authorities
and failure handling.  It is started by the WebView bridge (and the legacy Qt
entry point) and runs in a daemon thread:

1. **At launch**, when update auto-check is on, it asks
   `UpdateService.check_all(apply_config=True)` to check the app manifest,
   model metadata, and remote config (each network check is bounded to three
   seconds inside the update service), then forwards the result to the UI.
   It also retries telemetry left queued by an earlier session.
2. **Every six hours** it flushes the telemetry queue and uploads it only if
   the user has opted in.
3. **At close** it flushes telemetry and makes one bounded close-time upload
   attempt if opted in.  Network failures are debug-logged only; the local
   rows remain queued and no modal/offline error is shown.

### Local telemetry files

In Standard mode the files live with the rest of app data (normally
`%LOCALAPPDATA%\VISION\telemetry\`); portable mode uses the equivalent
`<install>\data\telemetry\` directory:

```text
telemetry\
  needs.jsonl          bounded local event queue (canonical upload name)
  report.json          aggregate rewritten on every flush
  upload-status.json   last local upload attempt for Settings -> Privacy
```

`TelemetryLogger`/`NeedsLog` is buffered and non-blocking for chat/tool
callers: event construction performs validation and redaction, then appends
to memory; a background writer flushes periodically, and explicit flush is
used for preview/export/upload/shutdown.  The queue is capped at 2,000
events and 5 MB.  When trimming is necessary, rows drop by training value
(`DROP_ORDER`: legacy `topic_usage` counters first, then runtime/schema/code
errors, and `user_negative` corrections only when nothing else remains).

Each `needs.jsonl` row has exactly this schema:

```json
{"ts":"...","session_id":"...","app_version":"1.0.0","model_sha":"...","signal":"...","category":"...","prompt":"...","model_output":"...","detail":{}}
```

Permitted signals are:

- `tool_schema_error` — Pydantic/JSON rejected model tool arguments.
  `detail` includes the tool, sanitized `raw_arguments`, and
  `validation_error`.
- `tool_runtime_error` — a tool raised; `detail` includes the tool and a
  redacted traceback.
- `code_error` — `run_python` exited non-zero; `detail` includes the exit
  code and bounded stderr.
- `user_negative` — thumbs-down, regeneration, or `/fix`/`/teach`.  The two
  composer commands store the user's correction as `detail.target`, making
  the preceding prompt/model-output pair directly usable as labelled data.
- `topic_usage` — legacy category counter, **no longer emitted**:
  successful chats are not recorded at all.  Rows already on disk from
  older builds remain valid (prompt/output forced empty) and are the
  first dropped when the queue trims.

Before a row is written, VISION redacts API keys and Hugging Face tokens
(`hf_...`), rewrites absolute user paths to `<PATH:basename>`, replaces
known file-body/clipboard argument values with length markers, and truncates
`model_output` to 2,000 characters.  The prompt is hashed with SHA-256;
that fingerprint (with the event timestamp/session/detail) deduplicates
rows locally.  Uploads need no remote deduplication: each snapshot becomes
one immutable shard file, acknowledged locally only after it lands.

`report.json` is regenerated on every flush with exactly `week`,
`total_events`, `by_category`, `by_signal`, `fix_rate`, and
`top_failing_tools`.  It describes the currently retained local queue;
uploaded JSONL rows are the historical record.

### Upload and privacy controls

Telemetry upload is **opt-in and default OFF**.  The destination is the HF
dataset `SpiritKing/VISION-v2-Agent-Dataset`:

- `telemetry/inbox/<UTC timestamp>-<id>.jsonl` — one immutable shard per
  successful upload; shards are never read back, merged, or overwritten;
  the path is fixed before retries so an ambiguous failure re-sends the
  identical shard instead of duplicating rows;
- `telemetry/report.json` — overwritten with the current aggregate.

The only credential source is the platform credential store — Windows
Credential Manager in production — under `HF_TOKEN`; no token is bundled in
the executable, and a missing token silently leaves rows queued.  Upload runs
off the UI thread with three attempts, exponential backoff, and a 60-second
caller timeout.  Local rows are acknowledged/cleared only after both dataset
writes succeed; rows added during an in-flight upload remain pending.

Settings → Privacy exposes the opt-in toggle, pending count, last-upload
status, **Preview what will be sent** (the exact local `needs.jsonl` payload
that will be uploaded as the next inbox shard plus the exact `report.json`), and **Clear local
telemetry**.  Activity & Feedback remains the operational view for recent
rows, export, and a manual explicit send.

### Remote-update data flow reminder

The same scheduler drives the already-documented update authorities:
GitHub raw manifests/config under `Akilesh8950/VISION` for App/Config, and
`SpiritKing/VISION-Ornith-1.5-9B-GGUF` on Hugging Face for the model SHA and
GGUF.  App packages stage under `versions\<version>\` for the launcher,
models activate only on next launch, and validated config (prompt + 27 tool
schemas + allowlisted UI strings) applies live with bundled/cached fallback.

## 11. Hardened update gates and model verification (post-1.0 hardening)

The App channel now passes four explicit gates before a build may run:

1. **Match.** `latest.json` carries `version`, `channel`
   (`stable`|`dev`), `platform` (`windows-x64` — the only VISION build),
   `url`, `size_bytes` (legacy `size` accepted), `sha256`, `min_launcher`,
   `notes`, and `released_at`. A manifest for another channel/platform is
   refused with one clear status line. SemVer is compared numerically
   (`1.10.0 > 1.9.0`); equal/older is skipped, and `min_launcher` above the
   running stub tells the user to run Setup manually.
2. **Integrity.** Resumable download to `downloads\<ver>.zip.part` with a
   2.2× free-space precheck; the finished file must match `size_bytes`
   exactly and then the SHA-256. On mismatch the archives are deleted and
   exactly one clean retry is allowed.
3. **Contents.** The ZIP is opened without extracting: it must contain
   `VISION.exe` and `version.json` at the root, every entry path must be a
   safe relative path, and the `version.json` version must equal the
   manifest version. Only then does the existing atomic
   `versions\<ver>.tmp` → `versions\<ver>` extraction happen.
   `scripts\build.ps1` and `scripts\release.ps1` stamp `version.json` into
   every payload. The GitHub authority for manifests/config is
   `Akilesh8950/VISION` (`raw.githubusercontent.com/Akilesh8950/VISION/main/...`).
4. **Selftest.** `VISION.exe --selftest` imports every module, validates the
   27 baseline tool schemas, locates `llama-server`, pings a running server
   if present, writes `result.json`, and exits 0/1. The launcher runs it
   once for a freshly applied `versions\<ver>` before its first real
   launch; on failure it deletes that folder, keeps the previous version,
   records `last_selftest_failure` in `launcher-state.json`, and shows one
   notice. The existing two-quick-crash rollback remains as the second
   safety net, and successful launches prune to current + previous.

Model downloads are proven before they can become pending: size floor plus
a real `GGUF` magic-header check, then — when a `llama-server` binary is
available — a behavioural smoke test on a throwaway side port (the user's
server is never touched): an identity prompt must answer with SPIRIT ORG
and a weather prompt must return a parsable `tool_call`, inside 120 s.
Failure deletes the staged GGUF, keeps the current model, and reports the
reason once in Settings → Updates. With no server binary yet (fresh
install), the smoke test is deferred rather than failed.

Settings → Updates rows now also show the available size and the
`Verifying` pill during hash/contents verification and model smoke tests.

Additional options added with this hardening:

- **Model switcher** — Settings → Model lists every GGUF in the models
  folder (plus the current custom path) for one-click switching; the
  official download/update flow still tracks the frozen VISION repo.
- **Document attach** — Library PDF/DOCX items (≤10 MB) attach with their
  text extracted into the message (256 KB cap), alongside plain text files.
- **Checkpoint restore UI** — Settings → Tools lists code checkpoints with
  a two-click restore.
- **Context trimming** — very long conversations drop their oldest turns to
  a char budget derived from the configured context size, never orphaning
  tool replies, with a one-line system note that trimming happened.
- **Read aloud** — assistant messages have a Listen button using the
  WebView speech synthesizer when available; clicking again stops it.

Still deliberately not built: image understanding (the shipped model is
text-only; no mmproj), microphone voice input (WebView2 speech recognition
is cloud-backed/unreliable), OAuth/first-party named connectors (require
official per-service integrations), non-Python skill executables and MCP
generic OAuth connector sign-in (needs per-service official docs), Git write operations in Code
Workspace (v1 read-only), accounts/cloud sync/analytics/crash reporting
(excluded by the privacy-first product rules), code signing (requires a
purchased certificate), and compiling/proving `VISION-Setup-1.0.0-x64.exe`
on real Windows hardware (see `docs\WINDOWS_SMOKE_TEST.md`).


## 12. Agentic mode and completed MCP / Skills / Connectors (post-1.0)

**Agentic mode.** The composer has a Chat | Agent toggle (also `/agent`).
An Agent turn adds a plan-act-verify system addendum, raises the tool-loop
budget from 8 rounds to `agent_max_steps` (default 30, clamped 1-100), and
enables two scoped auto-approvals for that turn only: `run_python` previews
run without asking, and registry-level "ask" prompts for builtin tools are
auto-approved (never `open_path`/`open_app`). MCP/connector tools still ask
every time unless the user preset them to Allow, and tool-level safety
confirmations (outside-workspace writes, executable opens, large clipboard
sets) still interrupt. Flags are restored in a `finally` block.

**MCP client completion.** Sessions are created with `client_info`
"VISION", a per-server `timeout_seconds` (5-600), a logging callback
(last 50 lines per server, shown under Connection details), an observe-only
message handler that marks the server stale on `notifications/*/list_changed`
and triggers a re-discovery (trust fingerprint decides whether tools stay
live), progress callbacks on tool calls, and `ping`. Three capabilities are
advertised only when usable:

- **Roots**: the VISION workspace folder.
- **Elicitation** (per-server `allow_elicitation`, default on): form params
  are rendered as a native dialog; URL-mode elicitation is declined.
- **Sampling** (per-server `allow_sampling`, default **off**): every request
  needs a per-request user confirmation and runs against the managed local
  model only (`http://127.0.0.1:<port>/v1`), never a remote endpoint.

**React management UI (Settings -> Tools).** Full add/edit/delete for MCP
servers (stdio, Streamable HTTP, SSE) and connectors (remote; auth none /
API-key header / Bearer). Secrets entered in the UI go straight to Windows
Credential Manager as `EXT_<ID>_KEY`; the JSON files keep only credential
references, and the editor never receives secret values back. Changing where
a server connects resets its trusted toolset fingerprint. Discovered
resources can be **Attached** to the next message (text cached under
`extensions/resource_cache`, capped at the normal attachment limits) and
discovered prompts can be **Used** (arguments collected, rendered text
placed in the composer for review before sending). Per-tool permissions
(Ask/Allow/Blocked) are editable. Skills can be created (writes a
`SKILL.md` package), exported as ZIP, and deleted, alongside the existing
import; bundled skill files are readable by the model through
`read_file` with `skill://<name>/<relative-path>`, confined to the skill
folder. Generic OAuth sign-in remains unimplemented and the UI says so.


## 13. Three extension systems: MCP, Skills, Connectors (and the Tool Router)

These are three distinct systems, never synonyms:

- **MCP (Model Context Protocol)** is a transport + protocol layer. VISION
  is an MCP *client*; it connects to MCP *servers* over stdio (local
  subprocess) or Streamable HTTP/SSE (remote), performs `initialize` +
  capability negotiation (official Python SDK), and then calls tools,
  reads resources, and fetches prompts. MCP adds *capabilities the model
  does not have*.
- **A Skill** is a folder containing `SKILL.md` (YAML frontmatter: `name`,
  `description`), then markdown instructions, optionally `scripts/` and
  `assets/`. It is progressively disclosed and adds *procedural
  knowledge, not new callable tools. A skill is not MCP.
- **A Connector** is a product-level bundle: an MCP server plus stored
  credentials plus preset permissions plus a UI entry, installed in one
  click. A connector *is backed by* an MCP server with authentication
  already solved; it is never called "a skill", and skills/connectors are
  never described as MCP itself.

### Tool Router (context budget)

The 9B model runs at 16k context. Built-in schemas (27 originals +
`read_url`, `skill_load`, `read_skill_file` ≈ 3.1k tokens) are always
present. Extension tools compete for the remaining budget
(`tools/router.py`):

```
user message + last two turns
        │
        ▼
┌─────────────────┐   lexical score (always)
│  Tool Router    │ + embedding score (only if local /v1/embeddings answers)
│                 │   rank extension tools best-first
└────────┬────────┘
         ▼  add until 40 tools OR ~4,000 tokens OR score < threshold
  [27 builtins + read_url/skill tools] + [ranked mcp__server__tool ...]
         │
         ▼
   report logged + shown as "N/40 tools · ~Xk tokens" (Extensions debug panel)
```

### MCP client

```
Settings → Extensions → MCP Servers          config: extensions/mcp_servers.json
        │                                     + drop-in extensions/servers/*.json
        ▼
ExtensionHost (dedicated asyncio thread) ──► McpClientManager
        │  owner-task per session (open/close in the same task)
        ├─ stdio: subprocess, cwd = workspace, env = PATH/SystemRoot/
        │         WINDIR/TEMP/TMP + declared only (never HF_TOKEN)
        └─ Streamable HTTP / SSE: headers from Credential Manager refs
        ▼
initialize → list tools/resources/prompts → fingerprint → user trust
        ▼
tools exposed as mcp__<server>__<tool> (collision-proof), shown in chat
as tool cards with the server name as a badge
```

Failure isolation: a server that times out (per-server
`timeout_seconds`) is parked as *unavailable for the session*, produces
one notice, and its calls return tool errors — chat continues. Explicit
**Check** reconnects and re-discovers. Trust is by toolset fingerprint;
changing where a server connects resets it.

### Skills

```
startup:  index of name+description only (≤ ~500 tokens total)
turn:     score vs message ── above threshold ──► inject SKILL.md body
          under "Relevant skills" + "skill: <name> used" chip
          /name or @name in the message forces activation
model:    skill_load(name) when metadata matches; read_skill_file
          (skill, path) or read_file skill://... for bundled files,
          always confined to the skill's folder
scripts:  never auto-run; only via run_python with its normal approval
```

### Connectors

```
config channel RemoteConfig.connectors[] (bundled fallback: empty)
        │  Settings → Extensions → Connectors → Directory
        ▼  Install (one click)
auth: none ───────────────┐
      api_key → prompt ───┤→ token → Windows Credential Manager
      oauth → generic Authorization-Code + PKCE + loopback redirect
              (only when the entry carries its own endpoints/client id)
        ▼
writes an ordinary connector record (a remote-MCP entry with a
credential *reference*, preset permissions, auto_approve) → the one
MCP client above connects it. Disconnect deletes credential + record.
```

No second protocol implementation exists: connectors reuse the MCP
client. No named third-party OAuth services are invented or bundled —
a directory entry without OAuth endpoints cannot be OAuth-installed
and the UI says so.

### Safety rules (all three systems)

- Extension tools ask **once per session** (server, tool, pretty args)
  unless the tool is preset **Allow** or the server has `auto_approve`.
  **Blocked** never runs.
- Tools whose *names* indicate deletion, process termination, registry
  writes, or unrestricted shell are **hard-blocked** at registration —
  a stored Allow cannot override this.
- `allow_paths` (per server): absolute path arguments outside the
  listed roots are rejected before the call.
- Every call is audited to `audit.jsonl` with server (source), tool,
  `args_sha256`, redacted args, duration, and status.
- **Disable all extensions** (Extensions page switch,
  `extensions_enabled=false`) reverts to exactly the 27 built-in tools:
  no dynamic tools, no skill metadata or injection.

## 14. Performance profiles and deployment hardening

### Performance profiles (`vision_app.server.hardware`)

`Settings → Model → Performance` (`performance_profile` in config) tunes
how `llama-server` is launched for the machine it runs on.  Resolution is
centralized in `resolve_perf()` and used both by
`build_server_command()` and by the chat context-trimming budget, so the
app never trims to a larger context than the server actually has.

| Profile | Behaviour |
|---|---|
| `auto` (default) | Detect hardware (`detect_hardware()`, cached): clearly constrained PCs (<10 GB RAM or ≤4 CPU threads) get `low_end`, everything else `balanced`. Auto never selects `performance`. |
| `balanced` | The frozen default invocation, byte-for-byte: `-c <context_size> -ngl <gpu_layers> --jinja --flash-attn on`, no tuning flags. |
| `performance` | Opt-in: `-t` = physical cores, `--threads-batch` = logical cores, `-b 1024 -ub 512`. Context/GPU layers stay as configured. |
| `low_end` | CPU-only PCs (e.g. Intel i3, 8 GB RAM): `-ngl 0`, context capped at 8192 (4096 under 7 GB RAM), KV cache `--cache-type-k/v q8_0` (≈half the cache RAM), physical-core threads, `-b 256 -ub 128`, `-np 1`. The web UI also drops backdrop blur/shortens transitions (`.perf-low`) so integrated graphics composite cheaply. |

Honest limits: the 9B Q4_K_M model needs ~5.6 GB for weights alone.
`low_end` makes an 8 GB, CPU-only PC *usable* (no swapping, responsive
UI, a few tokens/sec); it cannot make a 9B model fast, and <8 GB RAM is
not viable.  `get_settings`/bootstrap expose detected cores/RAM plus
`recommended_profile`/`effective_profile` so the UI can advise the user.

### Secrets ("encrypted keys") audit

- All API keys/tokens (Hugging Face, remote fallback, connector and MCP
  secrets) live only in **Windows Credential Manager** (DPAPI-encrypted,
  per-user) via `WindowsCredentialStore`.  Config files store credential
  *keys*, never values; the non-Windows fallback store is memory-only
  and never writes secrets to disk.
- The frontend only ever receives `*_stored` booleans for secrets;
  key entry is write-only.
- Logs, the tool audit (`audit.jsonl`), telemetry, and server logs pass
  through `vision_app.redaction` (token patterns, secret-named fields,
  absolute user paths).  Audit rows also carry `args_sha256`.
- Repository secret scans (GitHub/HF/OpenAI/AWS token patterns) are
  clean; `.env`, `*.gguf`, and build info are git-ignored.

### Deployment readiness

- One version everywhere: `APP_VERSION`/`__version__`/`pyproject`/
  frontend `package.json`/installer all read `1.0.0`; release payloads
  are stamped with `version.json` and verified against the manifest.
- Release bundle: minified, **no source maps** (`sourcemap: false`),
  WebView devtools disabled in frozen builds even with `--debug`.
- Crash-safety: atomic config save (temp file + fsync + `os.replace`,
  validated at the persistence boundary), corrupt config quarantined to
  `.invalid.json` with defaults restored, history in SQLite WAL mode
  with `busy_timeout`.
- Gates at time of writing: full pytest suite green (171 passed,
  2 environment skips), Ruff clean, mypy clean (99 files), npm audit
  0 vulnerabilities, `--selftest` exit 0, headless-Chrome frontend
  mount smoke green.
- Still requires a real Windows machine before release: compiled
  `VISION-Setup.exe` + smoke test (`docs/WINDOWS_SMOKE_TEST.md`),
  Credential Manager/WebView2 behaviour, GPU inference check, and
  publishing the first GitHub **app** release (`latest.json` + ZIP).
  Model updates never touch GitHub (Hugging Face only, §9).

## 15. The weekly model cycle

The model behind VISION is retrained daily by an external scheduled job,
but the GGUF the app consumes is republished only about **once a week**
(Sunday/Monday, UTC) to `SpiritKing/VISION-Ornith-1.5-9B-GGUF`.  The app
is built around that cadence, not around the training schedule:

- **Polling**: the model repo is checked at most once per launch plus on
  the SyncService six-hour tick (`scheduled_tick()`), by comparing the
  repo commit sha with `installed_model_sha` in config.  Manual checks
  from Settings are user-initiated and exempt.  Six quiet days between
  builds is the normal case, surfaces as "Up to date", and is never an
  error.
- **Daily training is invisible**: the LoRA adapter repo
  (`SpiritKing/VISION-Ornith-1.5-9B`, daily commits) is never polled,
  referenced, or treated as an update signal — a regression test pins
  `MODEL_REPO_ID` to the `-GGUF` repo.  Only the GGUF channel matters.
- **Update pipeline** (unchanged): user-consented download → size
  > 5,000,000,000 + GGUF magic + identity/one-tool smoke test → staged
  as pending → swapped on next launch.  `llama-server` is never
  restarted mid-conversation for a model change.
- **Telemetry feeds the next build**: `needs.jsonl` records only
  failures and corrections (`user_negative` with `/fix` at highest
  retention priority, `tool_schema_error` with raw arguments +
  validation error, `code_error`/`tool_runtime_error` with traceback).
  Successful chats are not recorded.  Opt-in uploads are timestamped
  shards in `telemetry/inbox/` (§10).
- **Honest UI**: Settings → Model shows the installed model SHA, last
  checked, and when the model last changed (`model_updated_at`),
  labelled "Updated weekly".  Nothing in the UI promises daily
  improvements.
- **Offline**: weeks offline change nothing locally — chat runs on the
  installed GGUF and telemetry queues.  The first launch/tick back
  online performs one sha check and one shard upload attempt.
- **Never**: trigger training, upload without opt-in consent, bundle a
  write token (Credential Manager only), or assume a new model exists
  because time passed — only a changed sha means a new model.

## 16. Connectivity awareness and download failsafes

`vision_app.connectivity` is the single source of online/offline truth
for the whole app.  The shared `ConnectivityMonitor` probes only hosts
the app already uses for updates (Hugging Face / GitHub raw HEAD
requests; any HTTP response counts as reachable), caches results for
~20 s, learns instantly from successes/failures of real network
operations, and can block until connectivity returns.

**Downloads survive connection loss.**  Every large transfer is
resume-based and wrapped in `ReconnectWaiter` retry logic:

| Download | Resume mechanism | On connection loss |
|---|---|---|
| Model GGUF (5.6 GB) | `hf_hub_download` partial files in the models dir | pause → wait for internet (progress says so) → resume automatically |
| App update ZIP | `.part` file + HTTP Range (`_fetch_with_reconnect`) | same |
| llama.cpp runtime | `.part` file + HTTP Range (`_download_file_once`) | same |

Two budgets keep this sane: while *online*, repeated transfer failures
get max 5 attempts with exponential backoff (server trouble is not
fixed by waiting); while *offline*, total waiting is capped at 2 hours,
after which the download stops with a clear message and the partial
file is kept so the next attempt continues where it stopped.
Non-network errors (404, auth, oversize, hash mismatch) never enter
the wait loop.  Note httpx yields decoded data in read-size chunks,
so one drop loses at most the final partial chunk (<1 MiB app ZIP,
<256 KiB runtime).

**The model is told.**  Each turn's system prompt gains one network
line from the monitor's cached state (background-refresh only; chat
never blocks on a probe): ONLINE → web tools available; OFFLINE →
web tools unavailable, do not call them, answer locally and say so.
Web-backed tools (`web_search`, `get_news`, `get_weather`,
`get_stock_price`, `get_crypto_price`, `lookup_docs`, `read_url`)
additionally fast-fail with "No internet connection is available
right now" when known offline, and record real connection failures
back into the monitor.  Settings shows the same state as an
**Internet** row (Connected / Offline — local model only).

## 17. Voice (read-aloud), responsive shell, and motion performance

### 17.1 Voice

Read-aloud is implemented in `app/frontend/src/voice.js` on top of the Web
Speech API (`speechSynthesis`), which WebView2/Chromium sources from the
speech voices installed in Windows.  No voice model is bundled and no audio
leaves the PC (local voices work fully offline; voices Windows marks as
online require a connection and are labelled as such).  Because installed
voices differ per machine, the "soothing voice" is a *policy*, not a file:

- **Ranking.**  `rankVoices()` scores every installed voice (natural/neural
  quality, language match, local-service reliability, soft gender hint from
  well-known Windows voice names, novelty-voice penalty).  `pickVoice()`
  honours an explicit Settings choice first (matched by `voiceURI`, then by
  name), otherwise the top-ranked voice wins.
- **Styles.**  Presets set rate/pitch/volume: `soothing` (warm female,
  0.92×/0.95 — the owner-chosen default), `calm_deep`, `natural`, `bright`,
  `slow_clear`, and `custom` (sliders).  Moving a slider switches to
  `custom` without changing values.
- **Chunking.**  Answers are cleaned (`plainSpeechText`: code fences →
  "Code block omitted", tables → cell text, links/images → text) and queued
  as ≤220-char sentence chunks (`chunkForSpeech`), because Chromium
  silently truncates long single utterances.  The engine tracks the speaking
  message, toggles Listen→Stop, stops on chat switch/Esc/dialog test, and
  returns explicit statuses (`unavailable`, `no-voices`, `empty`) that the
  UI surfaces as actionable toasts instead of silence.
- **Settings → Voice** offers the picker (Auto shows the concrete soothing
  pick by name), style, speed/pitch/volume sliders, an instant "Hear a
  sample" audition using the unsaved form values, voice-list refresh, and
  an optional auto-read of finished answers (default off).  Preferences
  persist as `tts_*` fields in `AppConfig` (clamp-never-raise validators,
  mirrored by `normalizeTts()` in JS; localStorage seeds first paint and
  serves preview/gallery mode).  No backend/tool contract changes.

### 17.2 Responsive shell (every window size)

The native main-window floor is **320×360** (`web_bridge.run_web_app`).
Its launch size is fitted to the primary display's logical size from
pywebview: the preferred 1440×920 is retained on larger monitors, while
smaller/scaled displays use 94% of the display width and 92% of its height
inside conservative margins (1280×800 fallback when no display can be read).
If a display is itself smaller than 320×360, the native minimum is lowered
to that display instead of requesting an off-screen minimum.

The icon rail is switched from React (`matchMedia('(max-width: 900px)')` →
`effectiveCollapsed = collapsed || narrow`) because `Sidebar.jsx` renders
different rows for the rail; the earlier CSS-only media query squeezed the
full sidebar into 64 px and clipped it.  The stored collapse preference is
preserved (in a narrow window, toggling means "open when widened").  CSS
breakpoints adapt layout only: ≥1700/≥2300 wider sidebar and reading column;
≤1100 padding/type; ≤900 rail + Settings top tabs + single-column forms;
≤720 full-bleed sheets/panels; ≤540 compact chrome/type and fixed wrapping
tables; ≤420 a 56 px rail and wrapping composer controls; ≤360 a 50 px rail;
≤300 a 44 px rail with Send kept on the first composer row; and
`max-height` 620/460/380 compact modes for short windows.

A later viewport audit found a hidden ~386 px layout floor below the old
520 px test.  It was fixed at the root with an explicit
`minmax(0, 1fr)` app column, `min-width: 0`/overflow constraints through
the shell and transcript, and a shrinkable/wrapping composer row; tables,
diffs, dialogs, and side panels now wrap or scroll internally instead of
widening the app.  Verified with Chrome/CDP at 320×340, 320×360, 320×480,
360×640, 400×700, 520×460, 800×480, 899×600, 901×600, 1024×600, 1366×768,
1440×900, 1920×1080, 2560×1080, 3840×2160, and 600×1000 with zero document
horizontal overflow.  Settings, Vision Live, Sources, Item Library, the
command palette, and the empty-chat hero were also checked at 320×360, and
the floating Live mini at 240×260.  The selected viewport matrix and
320×360 Voice-picker reachability are locked in by
`tests/test_frontend_smoke.py`; display-size calculations are unit-tested in
`tests/test_web_ui.py`.

### 17.3 Motion performance

Animation stays transform/opacity-based; the audit removed the remaining
layout-thrashing and waste: download progress now animates `scaleX` instead
of `width` (compositor-only), a permanent `will-change:
grid-template-columns` (a non-compositable property) was deleted, message
entrance animations stop past 24 rows (`.reading.many`), and ~77 lines of
CSS for two previous sidebar generations were pruned.  Controls gained
consistent 120–140 ms hover/press feedback.  Three calm modes compose:
OS `prefers-reduced-motion`, **Low-end CPU** perf mode (blur removed,
entrance animations skipped, slower spinner), and a manual **Reduce
animations** toggle (Settings → General, `reduce_motion` in config).

## 18. Vision Live: local push-to-talk voice and gated screen frames

Vision Live adds two user-requested modes without changing the frozen
text-model contract or adding a model-callable capture tool:

1. **Voice Live** — the user holds push-to-talk, local whisper.cpp
   transcribes the clip, the transcript enters the normal chat controller,
   and the final answer is read with the existing soothing TTS policy while
   translucent captions and a circular state meter update.
2. **Voice + Screen Live** — the same flow plus one explicitly captured,
   previewed, and approved primary-display frame.  It is read-only context
   for one turn only; there is no continuous screen stream and no mouse,
   keyboard, or computer-control capability.

### 18.1 Local speech-to-text

`vision_app.live.speech_to_text` uses the official whisper.cpp `v1.8.4`
Windows x64 `whisper-cli` and GGML Whisper Tiny/Base/Small models from the
official `ggerganov/whisper.cpp` Hugging Face repository.  Nothing is
bundled or downloaded until the user clicks **Download local voice model**.
The pinned archive and every model are SHA-256 verified; downloads reuse
the resumable, offline-aware downloader and traversal-safe ZIP extraction.
Files live under `%LOCALAPPDATA%\VISION\bin` and
`%LOCALAPPDATA%\VISION\speech-to-text\models`.

The WebView records only between press and release, decodes/resamples in
memory to 16 kHz mono PCM16 WAV, and passes the clip to the bridge.  The
backend validates the exact WAV shape/duration/non-silence, writes one
temporary WAV because `whisper-cli` is file-based, runs it with bounded
output/timeouts, and deletes that WAV in `finally` on every path.  Audio is
never logged, persisted, telemetered, or sent to any cloud speech API.
Browser/OS speech recognition is deliberately not used because its offline
behaviour cannot be guaranteed.

### 18.2 Screen-frame privacy boundary

`vision_app.live.screen_capture` grabs one Pillow/ImageGrab still frame of
the primary display, downscales/JPEG-encodes it only in memory, and returns
it to the UI for preview.  The frame cannot join a turn unless the bridge
re-validates JPEG/size, sees `live_screen_approved=true`, and sees a real
vision capability.  The controller additionally forces the loopback local
endpoint for image turns (not even a cloud health probe), marks history
with text only, never writes image bytes to the database/disk, treats all
on-screen content as untrusted data rather than instructions, and drops the
frame after that turn.  Screen capture is not exposed as a model tool.

Today's shipped `vision-ornith-1.5-9b-q4_k_m.gguf` repository contains no
matching `mmproj`, so **screen understanding is honestly disabled** even
though capture/preview works.  A future vision-capable VISION GGUF can set
`mmproj_path` or place exactly one matching `*mmproj*.gguf` beside the model;
`build_server_command` then appends `--mmproj`, and only then may the
one-turn OpenAI `image_url` data-URI path activate.  With no projector, the
frozen llama-server command remains exactly unchanged.  This reduces risk;
it does not make any local application literally “100% safe.” Windows
microphone permission, WebView2 `getUserMedia`, `whisper-cli.exe`,
ImageGrab, multi-window behaviour, and a future real projector still require
the Windows smoke test.

### 18.3 Surfaces

The main Live view and optional frameless always-on-top pywebview mini
share the React bundle (`?liveMini=1`).  The bridge keeps only sanitized
caption/status state and a monotonic mini-command sequence; no audio or
image bytes enter that state.  The main window remains the sole owner of
microphone capture, transcription submission, screen preview/approval, chat
sending, and TTS.
