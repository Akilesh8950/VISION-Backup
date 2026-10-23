# VISION DESKTOP — Implementation Plan (v1.0, awaiting approval — amended for feedback, UI and extensions)

VISION — built by SPIRIT ORG

Status: **PLAN ONLY.** No application code has been written. Implementation starts
after the approval items in §10 are approved or amended.

---

## 1. Verified facts (checked live, 2026-10-22 — not assumed)

| Item | Verified value |
|---|---|
| HF repo | `SpiritKing/VISION-Ornith-1.5-9B-GGUF`, public, not gated |
| Current HEAD SHA | `46956dae5e217795a41d7bd40b72962ad7b6fde5` |
| GGUF file | `vision-ornith-1.5-9b-q4_k_m.gguf`, 5,629,108,640 bytes (5.24 GiB) |
| GGUF LFS oid | `40f4d4dda582bbdce501cd126646517839763030521b3176033f45fcbcb75b43` |
| Other files in repo | `.gitattributes`, `README.md` only — **no mmproj/projector file** (see A6) |
| GGUF metadata | architecture `qwen35`, context_length 262144, embedded ChatML/Jinja chat template with tool-call formatting |
| llama.cpp | Latest observed tag `b11819` (spec floor: `>= b11815`) |
| llama.cpp win-x64 assets at b11819 | CPU ~24 MB · Vulkan ~37 MB · CUDA 12.4 ~272 MB **+ separate cudart 12.4 ~391 MB** (two-zip install) |
| Telemetry dataset | `SpiritKing/VISION-v2-Agent-Dataset` exists, public; uploads require a token **with write access to that repo** (see A7) |
| This workspace | No git repo yet; Linux, Python 3.13. Target (spec): Windows 11, Python 3.11 (see A2) |
| MCP current shape | Standard transports are stdio and Streamable HTTP; official Python SDK package `mcp` 2.3.0 supports Python `>=3.10` (pin/proof still required in M9) |
| Skills format | Open Agent Skills canonical form is a folder with `SKILL.md` YAML frontmatter (`name`, `description`) plus optional `scripts/`, `references/`, `assets/` |
| Feedback filename | **No `feedback.jsonl` was added by that name.** The frozen contract specifies `%LOCALAPPDATA%\VISION\telemetry\needs.jsonl` → HF dataset path `telemetry/needs.jsonl`; owner locked this choice in PLAN §8/A14 |

## 2. Locked decisions (from owner, this session)

1. **Live-data providers:** free, no-key APIs + extra pinned dep `ddgs` (§7 for the per-tool mapping proposal).
2. **llama-server acquisition:** resolution order *bundled next to exe → Settings path → PATH → wizard download*.
3. **Remote fallback:** optional Bearer key in Settings (Credential Manager); `model` = first id from `GET /v1/models`, with editable override.
4. **This session:** plan + architecture first; implementation only after approval.
5. **Extensions (owner-amended):** add a full UI description (`docs/UI_SPEC.md`) and capability for MCP, portable Skills (folder/`.md`/`.zip`/`.skill`) and Connectors (`docs/EXTENSIONS_SPEC.md`). Their exact scope is A15–A19.
6. **Optional feature menu:** owner chose F-codes in A20; nothing else from that menu is baseline merely because it was suggested.
7. **Feedback:** keep frozen `needs.jsonl` names; `feedback.jsonl` is only an export filename.
8. **MCP/Connectors:** proposed baseline in A16/A18 is selected.
9. **Skills:** user activation plus `skill_load` is selected (A17).
10. **Feature bundle:** F2 Projects, F4 local knowledge, F5 code workspace mode, F15 web reader/citations (`docs/FEATURES_SPEC.md`).
11. Everything in the original spec §§0–10 stands as-is unless listed in §10 of this plan.

## 3. Guiding rule: falsification before feature work

The first buildable milestone is a **measurement kit**, not UI. Before the chat
window exists, a script drives a stock `llama-server` (or a recorded SSE
fixture) and scores it against pre-registered behaviour cases, so the load
bearing assumptions — §11 stop conditions — can fail early and cheaply.
Same discipline applies per training-data change. Full ratchet design:
`docs/QUALITY_RATCHET.md`. Applied at planning rigour **60%**: rendering is
already delegated to validated Qt widgets (not rebuilt); what remains risky is
*content-level* behaviour (tool choice, claim fidelity), and that is what gets
instrumented. Prefill-time planning of model output is explicitly out of scope
for the client; see QUALITY_RATCHET §3.

## 4. Proposed file tree

```
VISION/
├── pyproject.toml                  # package meta, pytest/ruff config, version
├── requirements.txt                # pinned (PySide6, httpx, pydantic 2, psutil, pywin32*,
│                                   # pyperclip, ddgs, huggingface_hub, mcp, PyYAML) *win marker
├── .env.example                    # VISION_REMOTE_BASE_URL etc. — no real secrets
├── README.md
├── PLAN.md                         # this file
├── ARCHITECTURE.md
├── docs/
│   ├── UI_SPEC.md                  # layout, components, consent UX, settings IA
│   ├── EXTENSIONS_SPEC.md          # MCP hosts, Skills import/activation, Connectors
│   ├── FEATURES_SPEC.md            # selected F2/F4/F5/F15 bundle
│   ├── QUALITY_RATCHET.md          # falsification kit + dogfood gates
│   └── WINDOWS_SMOKE_TEST.md       # manual DoD checklist for a real Windows box (M16)
├── assets/
│   └── vision.ico                  # see A9
├── third_party/
│   └── README.md                   # drop llama.cpp bundle here for build.ps1; never committed binaries
├── scripts/
│   ├── build.ps1                   # PyInstaller one-folder -> dist\VISION\VISION.exe
│   ├── probe_server.ps1            # M0: raw llama-server + curl/httpx probe (no app)
│   └── run_bench.py                # bench runner (replay | live), writes runs/<ts>/ report
├── bench/
│   ├── cases/                      # versioned *.jsonl behaviour cases, one per stratum
│   ├── fixtures/                   # recorded SSE streams for replay mode
│   └── runs/                       # git-ignored run outputs (raw + scored)
├── src/vision_app/
│   ├── __init__.py                 # __version__ (+ _build_info fallback "dev")
│   ├── __main__.py                 # python -m vision_app -> app.main()
│   ├── app.py                      # QApplication boot, crash guard, wizard gate, close hooks
│   ├── constants.py                # product name, credits line, repo ids, GGUF name, min llama build
│   ├── paths.py                    # %LOCALAPPDATA%\VISION layout, workspace resolution (pure, testable)
│   ├── config.py                   # pydantic AppConfig <-> config.json (atomic write)
│   ├── credentials.py              # Windows Credential Manager via win32cred (see A8)
│   ├── redaction.py                # secret scrubbing for logs/transcripts (pure)
│   ├── logging_setup.py            # rotating file log + audit.jsonl writer
│   ├── transport/
│   │   ├── schemas.py              # pydantic wire models: messages, tool_calls, deltas, usage
│   │   ├── sse.py                  # SSE line parser, <think> splitter, tool-delta accumulator (pure)
│   │   ├── client.py               # httpx AsyncClient: stream_chat(), list_models(), timeouts, cancel
│   │   └── endpoints.py            # EndpointMode (Local/Cloud/Auto), failover state machine (pure-ish)
│   ├── server/
│   │   ├── discovery.py            # find llama-server: bundle -> setting -> PATH -> none
│   │   ├── download_llama.py       # GitHub release asset pick + download/extract (variant: A5)
│   │   ├── manager.py              # QProcess lifecycle, exact frozen arg list, log ring buffer
│   │   └── health.py               # 5 s poll, 2-failure rule, single restart, failover offer
│   ├── history.py                  # SQLite: conversations, messages, memory_kv, meta
│   ├── projects.py                 # project records/instructions + extension scoping
│   ├── tools/
│   │   ├── schemas.py              # pydantic arg models for all 27 tools + JSON-schema export
│   │   ├── registry.py             # tool table, validation, 2-retry loop glue, audit/log, unknown-tool block
│   │   ├── sandbox.py              # path resolution, containment, system-path deny, confirmation gate (pure core)
│   │   ├── live_data.py            # web_search, get_news, get_weather, stocks, crypto, docs (§7)
│   │   ├── code_files.py           # run_python, read/write/list/search
│   │   ├── memory.py               # memory_get / memory_set
│   │   ├── genui_tools.py          # ui_* -> emits widget-build payloads (no Qt here)
│   │   └── windows_tools.py        # apps/windows/clipboard/system_status/timer (win-guarded)
│   ├── chat/
│   │   ├── controller.py           # turn loop: request -> stream -> tools -> follow-up (no Qt widgets)
│   │   ├── worker.py               # QThread hosting asyncio loop; Qt signals out
│   │   └── commands.py             # /fix, /teach parsing
│   ├── ui/
│   │   ├── main_window.py          # layout, banner (failover), endpoint chip, server drawer host
│   │   ├── transcript.py           # virtualised list (QListView + delegate model, see A10)
│   │   ├── message_widgets.py      # markdown+highlight, <think> collapse, copy/regen, tool cards
│   │   ├── composer.py             # multiline input, Enter rules, attach (A6), token counter (A11)
│   │   ├── settings_dialog.py
│   │   ├── extensions_center.py    # Built-ins / MCP / Skills / Connectors tabs
│   │   ├── projects_panel.py       # project/source management
│   │   ├── sources_panel.py        # local/web source cards + citation targets
│   │   ├── code_panel.py           # file tree/preview, changed files, test output
│   │   ├── model_manager.py
│   │   ├── server_console.py       # log tail, restart, clear
│   │   ├── wizard.py               # first run: model dl -> GPU layers -> health check
│   │   ├── confirmations.py        # UI-thread confirmation service used by tool worker
│   │   ├── toasts.py
│   │   └── theme.py                # dark default
│   ├── genui/
│   │   ├── table_widget.py         # sortable + export CSV/PNG
│   │   ├── chart_widget.py         # QtCharts bar/line/pie/scatter + export PNG
│   │   ├── cards_widget.py         # incl. live system_status refresh (A12)
│   │   ├── timeline_widget.py
│   │   └── diff_widget.py          # side-by-side, green/red gutters
│   ├── knowledge/
│   │   ├── parsers.py              # text/code + proposed PDF/DOCX adapters (A21)
│   │   ├── index.py                # deterministic chunks + SQLite FTS5/BM25
│   │   └── retrieval.py            # capped pre-turn retrieval + source IDs
│   ├── code_workspace/
│   │   ├── checkpoints.py          # pre-write snapshots + restore metadata
│   │   ├── git_status.py           # fixed read-only Git argv queries
│   │   └── test_profiles.py        # user-owned argv profiles; never model-callable (A22)
│   ├── extensions/
│   │   ├── skills.py               # SKILL.md parse/validate, discovery, progressive activation
│   │   ├── skill_import.py         # folder/.md/.zip/.skill -> canonical folder; archive safety
│   │   ├── mcp_config.py           # versioned server records + mcpServers import/export
│   │   ├── mcp_client.py           # official MCP SDK adapter; one session per server
│   │   ├── mcp_tools.py            # namespacing, JSON-schema validation, result normalization
│   │   ├── connectors.py           # connector records/auth refs; remote-MCP facade
│   │   └── trust.py                # toolset fingerprints + per-tool consent decisions
│   ├── updater/
│   │   ├── model_updater.py        # HF SHA compare (pure) + download orchestration, pending_restart
│   │   └── downloads.py            # hf_hub_download wrapper, progress, >=7 GB precheck, size verify
│   ├── telemetry/
│   │   ├── needs_log.py            # needs.jsonl append, flush @50/close, field freeze (§8)
│   │   └── uploader.py             # opt-in append-merge upload, bg thread, 3 retries (A7)
│   └── bench/
│       ├── case_schema.py          # pydantic bench-case format v1
│       ├── scoring.py              # entailment/fabrication/false-precision/schema scoring (pure)
│       └── replay.py               # fixture SSE -> controller with scripted fake tools
└── tests/
    ├── test_sse_parser.py          # deltas, [DONE], truncation, <think> split, parallel tool_calls
    ├── test_tool_schemas.py        # all 27: valid/invalid args, corrective error text, unknown tool
    ├── test_sandbox.py             # .. escape, system paths, outside-workspace confirm, exe open
    ├── test_redaction.py
    ├── test_telemetry.py           # append, flush, field freeze (category flip does not rewrite history)
    ├── test_updater_compare.py     # SHA newer/equal/older/offline
    ├── test_endpoints_failover.py  # Local/Cloud/Auto, 2-failure failover, banner state
    ├── test_commands_fix_teach.py
    ├── test_bench_scoring.py       # sensitivity: perturb one parameter, score moves
    ├── test_history_memory.py
    ├── test_skills_import.py       # all import containers + archive attacks + export round-trip
    ├── test_skill_activation.py    # metadata-only discovery, activation, resource sandbox
    ├── test_mcp_config.py          # secret refs, config conversion, command resolution
    ├── test_mcp_tools.py           # fake session: discovery, consent, fingerprints, results
    ├── test_connectors.py          # custom remote-MCP facade, disconnect, egress metadata
    ├── test_projects.py            # instructions precedence, scoping, move conversation
    ├── test_knowledge.py           # parsers/chunks/FTS retrieval, citations, secret exclusions
    ├── test_code_workspace.py      # checkpoint byte identity, git absence, no model test-run
    └── test_web_reader.py          # SSRF/redirect/content caps, extraction, citation IDs
```

`product_genuine` modules (logic, no Qt in imports): `paths`, `config`, `redaction`,
`transport/sse`, `transport/endpoints`, `tools/schemas|sandbox|live_data…`,
`bench/*`, `telemetry/needs_log`, `updater` compare. These are the parts that
must be pytest-green in *this* Linux sandbox. Qt/Windows modules import-guard
`pywin32`/QtCharts so headless test collection never crashes.

## 5. Milestones (one commit each; tests written+run inside each milestone)

| # | Commit message | Scope | Exit gate (measurable) |
|---|---|---|---|
| M0 | `chore: scaffold, falsification bench kit, baseline fixtures` | pyproject/reqs, bench case format v1 + scoring, `probe_server` + `run_bench`, 2 replay fixtures | Scoring sensitivity test passes: one perturbed parameter (e.g. truncated tool delta / wrong relation word) moves the matching score, no other score moves. Baseline report produced in replay mode. |
| M1 | `feat: config, paths, credentials, transport + SSE parser` | constants, paths, config, credentials, redaction, transport (schemas/sse/client/endpoints) | `test_sse_parser`, `test_endpoints_failover`, `test_redaction` green; failover unit test proves: local fails health ×2 → remote selected, no exception escapes. |
| M2 | `feat: llama-server manager + health loop` | discovery, download_llama, QProcess manager (frozen arg list), health, log ring buffer | Unit: arg vector is byte-identical to the frozen contract (host 127.0.0.1, port, `-c`, `-ngl`, `--jinja`, `--flash-attn`); manager state machine test (start→healthy→dead→one restart). Real-server smoke = WINDOWS_SMOKE_TEST step. |
| M3 | `feat: chat UI + streaming` | main window, transcript, composer, worker/controller, think-collapse, stop/regen, history.sqlite3 with nullable `project_id`, right work-panel host | Replay fixture streams into transcript model in test; cancel mid-stream leaves a valid, "incomplete"-marked message; UI thread does zero I/O (assert: all httpx calls originate in worker). |
| M4 | `feat: tool runtime (27 tools) + sandbox` | schemas/registry, live_data, code_files, memory, windows tools, confirmations, audit log | `test_tool_schemas` covers 27/27 (valid + invalid each); every error path yields a `role:"tool"` reply (conversation-validity invariant); unknown/forbidden tool → `Blocked by VISION safety policy`. |
| M5 | `feat: generative-UI widgets` | table/chart/cards/timeline/diff + exports; tool cards render genui inline | Replay cases render to widget payloads of correct type in tests; `ui_chart` DoD case (1,4,9,16) produces a bar chart payload with 4 points. Visual check = Windows smoke. |
| M6 | `feat: settings, model manager, updater, wizard` | settings dialog, updater, downloads, wizard, about, server console drawer | `test_updater_compare` green (newer/equal/older/offline/partial-resume stub); disk-precheck refuses <7 GB in test; `pending_restart` never swaps mid-conversation (state test). |
| M7 | `feat: safety hardening + telemetry + uploader` | confirm matrix, needs_log, /fix /teach, uploader, flush rules | `test_telemetry` + `test_sandbox` green; needs.jsonl field set frozen and asserted; benchmarks: a prompt-category relabel in telemetry does **not** rewrite previously scored cases (re-score run required — QUALITY_RATCHET §5). |
| M8 | `feat: portable skills runtime` | canonical `SKILL.md` parser, folder/.md/.zip/.skill importer, staging/file preview, discovery + activation, Extensions → Skills UI | Import test matrix green (incl. ZIP Slip/symlink/bomb cases); import executes zero bundled code (asserted); metadata-only context before activation; export round-trip preserves canonical package. |
| M9 | `feat: MCP host and dynamic tools` | official SDK adapter, stdio + Streamable HTTP, discovery/fingerprinting, per-tool consent, resources/prompts pickers, logs | Fake/in-process MCP session tests green; changed toolset is disabled until reapproved; no MCP invocation occurs without required consent; one real protocol fixture recorded for replay. |
| M10 | `feat: connectors layer` | custom named remote-MCP connectors, API-key/Bearer credential refs, disconnect/revoke; OAuth/named adapters only if separately approved | Connector tests green; disconnect removes credentials; egress (host + args) is present on every approval card; disabled connector tools are absent from the next request. |
| M12 | `feat: projects` | project CRUD/archive, instructions, active-project context, extension scoping, conversation assignment | Project tests green; project text cannot override safety/tool policy in fixtures; moving a conversation changes no stored historical message. |
| M13 | `feat: local knowledge and citations` | project sources, parsers, chunk/FTS5 index, retrieval threshold, Sources Used cards | Fixture corpus retrieves intended chunks; citations resolve to page/line; secret/binary exclusions asserted; ghost citations count as D2 in bench. |
| M14 | `feat: code workspace mode` | file tree/preview, changed files, checkpoints/restore, read-only Git status, user test profiles | Restore byte-identity tests green; model has no test-profile or Git-mutation callable (registry assertion); Git-absent path green. |
| M15 | `feat: web reader and citations` | guarded fetcher/extractor, proposed `read_url` (A23), source IDs/cards for search/news/docs/pages | SSRF/redirect/size tests green; extraction fixture set passes; only returned sources resolve citations. |
| M16 | `test+build: full suite, README, build.ps1, smoke checklist` | fill remaining tests, README, build.ps1 (ico + version resource), WINDOWS_SMOKE_TEST.md | `pytest` fully green headlessly here; bench report vs M0 baseline attached to commit notes; every §9 DoD line maps to either an automated test or a numbered smoke step — none unmapped. |

After M16, per-ratchet rule: **each future training-data/model push = one
commit**, with a re-run benchmark (per stratum: baseline count / new count /
diff) produced by `scripts/run_bench.py`, plus a dogfood gate (install on a
personal Windows PC, use for a real task, file every defect, fix blockers)
*before* declaring that build a release.

## 6. Threading model (UI never freezes)

- **UI thread:** widgets only. Never does network, disk-heavy, or subprocess waits.
- **Chat worker:** one `QThread` running an `asyncio` loop; owns the `httpx`
  AsyncClient; emits Qt signals (`token`, `think_token`, `tool_call_started`,
  `tool_result`, `turn_done`, `error`). Cancel = cancel the asyncio task +
  close the stream; partial text is kept and marked `incomplete`.
- **Tool execution:** inside the worker's loop via a small thread pool for
  blocking tools (file I/O, ddgs, subprocess). Parallel tool calls run
  concurrently; results are re-assembled in tool_call index order before the
  follow-up request.
- **Confirmations / genui:** worker emits a request signal and blocks on a
  `concurrent.futures.Future`; UI thread shows the dialog / builds the widget
  and resolves the future. UI thread never blocks.
- **llama-server:** `QProcess` (async by design) + reader signals into a ring
  buffer for the console drawer. Health timer (5 s) dispatches through the
  worker, not the UI thread.
- **Downloads/uploads:** dedicated `QThread`s with progress signals; telemetry
  upload on close is fire-and-forget with its own retry loop, never joined at exit.

## 7. Proposed live-data mapping (free/no-key; pending approval §10-A3)

| Tool | Proposed upstream | Notes / failure mode |
|---|---|---|
| `web_search` | `ddgs` `DDGS().text(query, max_results=…)` | returns title/url/snippet list |
| `get_news` | `ddgs` `DDGS().news(topic, max_results=limit)` | date/source/title/url |
| `get_weather` | Open-Meteo geocoding `geocoding-api.open-meteo.com/v1/search` → forecast `api.open-meteo.com/v1/forecast` (current + today) | `units` enum `metric\|imperial` maps to `temperature_unit`/`wind_speed_unit`; strict enum, no silent aliasing (feeds schema-error metric genuinely) |
| `get_stock_price` | Yahoo Finance chart API (`query1`/`query2`, 5d/1d) | **Deviation recorded during implementation:** the previously proposed Stooq CSV endpoint returned HTTP 404 / browser-verification HTML in live probes on 2026-10-23. Yahoo tickers use exchange suffixes such as `RELIANCE.NS`; no implicit `.us` suffix is added. |
| `get_crypto_price` | CoinGecko `api.coingecko.com/api/v3/simple/price` | needs coin *id* (`bitcoin`), not symbol (`BTC`): built-in map for majors + `/search` fallback; 401/429 → clear tool error, **no silent provider switch** |
| `lookup_docs` | `ddgs` text search `"<library> documentation <topic>"` | snippets + URLs only in v1; no page scraping |

All live tools share: httpx timeout (connect 5 s), result-size caps, and
exception → short `role:"tool"` error (never raises into the turn loop).

## 8. Frozen formats and feedback pipeline (do not drift without a version bump + re-score)

- **Audit** `logs/audit.jsonl`, one line/tool call: `{ts, name, args (redacted), status, duration_ms}`.
- **Feedback/telemetry queue — status:** *planned, not yet implemented; no file
  literally named `feedback.jsonl` was specified or added.* Frozen source of
  truth is local `%LOCALAPPDATA%\VISION\telemetry\needs.jsonl`, one JSON
  object per line: exactly `{ts, category, prompt, model_output, signal, detail}`,
  `signal ∈ {tool_schema_error, tool_runtime_error, code_error, user_negative, topic_usage}`.
  Buffered in memory, flushed every 50 events and on app close. Sources are
  tool validation/runtime failures, `run_python` failure, thumbs-down,
  regenerate, `/fix` and `/teach`; all text is redacted first and file/clipboard
  contents are excluded.
- **HF upload (opt-in, default OFF):** on close and via “Upload now”, a
  background thread uses the stored HF credential to append-merge the local
  queue into `SpiritKing/VISION-v2-Agent-Dataset` at `telemetry/needs.jsonl`
  (download existing lines → append only new local lines → upload; never
  overwrite remote history), retries 3×, then clears local lines only after a
  confirmed successful upload. Filename/remote path are locked by A14.
  A category *value* may be revisited per event, but the *recorded field* and the
  scored prompt text are never silently rewritten; any taxonomy change requires
  re-scoring the whole bench set (QUALITY_RATCHET §5).
- **Projects/knowledge:** project metadata and FTS data live locally under the
  same app-data database family; indexed source files remain in place and are
  referenced by absolute path + hash. No project document is uploaded by F4.
- **Config** `config.json` (pydantic, atomic write): endpoint mode, local port,
  remote base URL, remote model override, context size, GPU layers, model path,
  installed repo SHA, `pending_restart`, theme, telemetry opt-in, run_python
  auto-approve (session-only, not persisted). MCP/connector records live in
  versioned files under `extensions\` with credential references only. Secrets
  never in any JSON config file.
- **SQLite** `history.sqlite3`: `conversations(id, project_id, title, created_at, updated_at)`,
  `messages(id, conversation_id, role, content, reasoning, tool_calls_json,
  tool_call_id, incomplete, created_at)`, `memory_kv(key, value, updated_at)`, `meta(k, v)`.
  Feature tables for projects/sources/chunks(FTS5)/test profiles are specified in FEATURES_SPEC.

## 9. Definition-of-done → verification map

| Spec §9 line | Verified by |
|---|---|
| clone → install → `python -m vision_app` → wizard downloads GGUF → offline chat | WINDOWS_SMOKE_TEST #1–4 (cannot run in this Linux sandbox) |
| "What's the weather in Coimbatore?" → `get_weather` → card → streamed answer | bench case `live-weather-in`, replay-green here; smoke #5 on Windows |
| "Show CPU and RAM usage" → `system_status` → live cards | bench case `win-system-status`; A12 for "live-updating" semantics; smoke #6 |
| "Draw a bar chart of 1,4,9,16" → real Qt chart | bench case `genui-chart-squares` (payload-level assertions here); smoke #7 |
| `<think>` collapse, tool cards, no UI freeze | transcript model tests + smoke #8 (subjective freeze check) |
| Kill llama-server mid-chat → auto-restart + banner, no data loss | manager/health unit tests; smoke #9 |
| pytest green, audit + telemetry produced, no secrets in logs | automated here at every milestone from M1 on |
| Feedback queue can be inspected, exported and (opt-in) append-merged to HF without losing lines on failure | `test_telemetry` + mocked HF upload tests; smoke step with a write-capable test token |
| Import a skill as folder, `SKILL.md`, `.zip` and `.skill`; activate it visibly | `test_skills_import` / `test_skill_activation`; Windows smoke with a sample skill |
| Add a local and remote MCP server; call a namespaced tool only after consent | `test_mcp_tools` fake session; Windows smoke with selected real servers |
| Add a custom Connector backed by remote MCP and revoke it cleanly | `test_connectors`; Windows smoke if A18 enables a remote service |
| Create a Project, index local documents, ask a question and open a cited source | `test_projects` + `test_knowledge`; bench `project_rag_fidelity`; Windows smoke |
| Propose a code change, inspect diff, apply and restore checkpoint | `test_code_workspace`; Windows smoke |
| Read a selected web page (if A23) and resolve source citations | `test_web_reader`; bench web-citation cases |

## 10. Approval items — proposed defaults (reply "approve as-is" or amend by number)

- **A1 Git**: `git init` in this workspace; commits per milestone table above (needs a local git identity — supply name/email or approve a neutral local one). No remote, no binary blobs (GGUF/llama.cpp). Third-party bundle placeholder is git-ignored.
- **A2 Python range**: code targets **3.11**; `requires-python = ">=3.11,<3.14"` so headless tests can also run on this sandbox's 3.13. Production README says 3.11.
- **A3 Live-data details**: mapping in §7, including Yahoo Finance chart tickers (Stooq falsified live, see §7), CoinGecko id map, snippets-only docs lookup.
- **A4 Tool-schema caps/enums** (not in spec, needed for pydantic): `max_results≤10`, `limit≤20`, file read/write ≤1 MB text, `search_code` returns ≤200 matches, `set_timer.minutes` is `float, 0<·≤1440`, chart `values:list[float]` (len == labels; pie values ≥0; **scatter**: x = float(label) if numeric else index), `ui_cards` item = `{title, value?, body?, footer?}`, `ui_timeline` item = `{time, title, description?}`, `ui_table` rows must match `columns` length.
- **A5 llama.cpp download variant**: pin a tested tag (start: b11819, not floating "latest"). Detect: NVIDIA (`nvidia-smi` present) → CUDA 12.4 x64 build zip + matching `cudart` zip; else Vulkan x64; user can override in wizard. ARM64 → matching ARM64 asset. Bundled/`third_party` copy always wins over download.
- **A6 Attach file**: v1 = text/code files only, UTF-8, ≤ 256 KB, inserted as a fenced block + filename into the user message; binaries/images rejected with an inline notice. Rationale: the verified GGUF repo contains no mmproj, so genuine image input is impossible under the frozen contract.
- **A7 Telemetry upload failure**: upload needs a write-capable token for the dataset repo. On 401/403/network failure after 3 retries: **keep** local `needs.jsonl` (do not clear), log a redacted warning, retry next close. Successful append-merge upload (download existing → concat new lines → upload) clears local.
- **A8 Credential Manager**: use `pywin32` `win32cred` directly (no extra `keyring` dep), service name `VISION`, keys `HF_TOKEN`, `REMOTE_API_KEY`. Non-Windows fallback for tests: in-memory store only.
- **A9 Icon**: generate a simple "V" `.ico` in-repo during M16 unless you supply artwork. Version resource: product "VISION", credits line in About only.
- **A10 Transcript virtualisation**: `QListView` + custom delegate/model (message widgets built lazily per visible row; genui widgets hosted in the row). Chosen over a giant `QTextDocument` so native sortable tables/charts can live inline.
- **A11 Token counter**: v1 shows an **estimate** (`~ chars/4`, labelled "est.") to stay inside the frozen transport (no `/tokenize` in the contract). Approve, or approve adding local-only `POST /tokenize`.
- **A12 "Live-updating" system status**: the `system_status` tool card (and a following `ui_cards` if the model emits one, flagged by the controller) refreshes locally every 2 s via `psutil` — without re-querying the model — until the card is collapsed/closed or the turn ends. Tool *result text* sent to the model stays the single snapshot.
- **A13 Bench gates at 60%**: ship gates per QUALITY_RATCHET §4 — thresholds are *set from the M0 baseline on real hardware*, not invented now; only the ratchet direction (never worse than baseline without a signed-off exception) is fixed at this gate.
- **A14 Feedback filename/path — LOCKED**: **keep the frozen names** (`telemetry/needs.jsonl` locally and on HF) while the UI calls it “Feedback queue” and Export can save a copy as `feedback.jsonl`. Alternatives requiring explicit amendment: rename both to `feedback.jsonl`, or write both files. Do not silently change the training pipeline's input name.
- **A15 UI direction**: approve `docs/UI_SPEC.md`: collapsible sidebar + centered transcript + optional right-side work/artifact panel + bottom server/activity drawers; Extensions and Activity as first-class sections.
- **A16 MCP scope — LOCKED**: stdio + Streamable HTTP; tools bridged dynamically, resources user-attached, prompts user-invoked; no server-initiated sampling in v1; legacy SSE only for a named server that requires it. The 27 built-ins remain fixed, while the total request tool array becomes dynamic.
- **A17 Skills scope/activation — LOCKED**: canonical Agent Skills folders; import folder/`SKILL.md`/plain `.md` (conversion form)/`.zip`/`.skill` (ZIP alias); user activation plus the selected `skill_load` model meta-tool; Python skill scripts only through the existing approval path, all other executables blocked in v1.
- **A18 Connectors scope — LOCKED**: custom Connector = named remote-MCP endpoint with none/API-key/Bearer auth. OAuth 2.1 and first-party named-service adapters are separate approvals requiring the service name and its official API/auth details.
- **A19 Added dependencies — LOCKED in principle**: official `mcp` Python SDK and `PyYAML` (frontmatter); SDK is hidden behind a VISION adapter and pinned only after a Python 3.11/Windows protocol proof in M9.
- **A20 Optional features — LOCKED bundle**: F2 Projects, F4 local knowledge/RAG, F5 Code workspace, F15 Web reader/citations. Unchosen F-items stay out of baseline.
- **A21 F4 implementation**: approve `pypdf` + `python-docx`, SQLite FTS5/BM25 first (no embeddings model), controller pre-turn retrieval and FEATURES_SPEC caps/exclusions.
- **A22 F5 execution boundary**: approve user-defined named test profiles (program + argv, UI-started only) and fixed read-only Git status queries; no model-invoked tests, shell or Git mutations.
- **A23 F15 API**: approve one extra built-in `read_url(url, max_chars=12000)` and a pinned HTML readability extractor selected by fixtures; without approval, URL reading is user-attach only and the product still has 27 built-ins.
- **A24 F2 boundary**: approve FEATURES_SPEC project-root/instructions rules, especially that selecting an existing project folder does not itself grant broad tool write access.

## 11. Stop conditions discovered during planning (will pause & ask, not guess)

1. **Tool-call wire format**: the GGUF's template formats calls as XML inside ChatML. The plan assumes llama.cpp `>=b11815` with `--jinja` converts these into OpenAI `tool_calls` deltas. The M0 probe's first job is to disprove this on a real server; if calls arrive as raw content text, I stop and ask before writing any fallback parser.
2. **`--flash-attn` bare flag**: frozen command is used verbatim. If b11819's `llama-server` rejects a value-less `--flash-attn` (exit ≠ 0 with arg error), I stop and ask before changing it to a valued form.
3. **OOM with `-ngl 99`**: no silent auto-retry at lower layers. Wizard suggests layers from detected VRAM; a load failure surfaces the stderr tail and asks the user to lower the slider unless they have pre-approved a fallback in Settings.
4. **Port 8080 in use by someone else**: never kill foreign processes. Banner + Settings port change; if it's our own orphan, it is adopted after a `/v1/models` identity check.
5. **MCP stdio versus “no arbitrary shell”**: a local MCP server is user-installed executable code. It is allowed only after exact command review and enablement by the user; the model/skill/resource/tool results can never add or start a new server definition.
6. **Non-ZIP `.skill` files**: if a supplied `.skill` is not a ZIP container with canonical `SKILL.md` contents, stop for its actual spec/sample instead of inventing a second parser.
7. **Named OAuth/API connectors**: each service can require app registration, scopes and redirect rules. Stop for the selected service and official docs before designing its adapter.
