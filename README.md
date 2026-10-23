# VISION DESKTOP

VISION — built by SPIRIT ORG

VISION DESKTOP is a native Windows chat client and tool runtime for the local
`VISION` LLM. It runs `llama-server` as a managed child process, streams
OpenAI-compatible responses, exposes a validated tool runtime, renders native
generative-UI widgets, and supports portable Skills, MCP servers and custom
remote-MCP connectors.

## Frozen model and inference contract

- Model: `vision-ornith-1.5-9b-q4_k_m.gguf` (5.24 GiB) from
  `SpiritKing/VISION-Ornith-1.5-9B-GGUF`
- Runtime: `llama-server` from llama.cpp `>= b11815`
- Local launch shape:

  ```text
  llama-server -m <gguf> --host 127.0.0.1 --port 8080 -c 16384 -ngl 99 --jinja --flash-attn on
  ```

- Transport: `POST /v1/chat/completions` with `stream: true`, plus
  `GET /v1/models` for health/model discovery
- Optional remote OpenAI-compatible `/v1` endpoint with Local / Cloud / Auto
  selection and visible failover state

## Windows 11 + Python 3.11 quick start

```powershell
git clone <repository-url> VISION
cd VISION
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
python -m vision_app
```

On first launch, the wizard can:

1. download a pinned supported Windows `llama-server` build;
2. download and size-verify the 5.24 GiB GGUF;
3. set GPU layers;
4. start the local server and wait for `/v1/models` health.

You can skip the wizard and configure paths manually in **Settings → Model &
Server**.

## Data locations

Application state lives in `%LOCALAPPDATA%\VISION\`:

- `config.json`
- `history.sqlite3`
- `logs\vision.log`, `logs\audit.jsonl`
- `models\`
- `bin\` for downloaded llama.cpp builds
- `skills\`
- `extensions\mcp_servers.json`, `extensions\connectors.json`
- `telemetry\needs.jsonl`
- `checkpoints\` for code-workspace pre-write snapshots

Tool file operations default to `%USERPROFILE%\VISION\workspace`. Relative
tool paths resolve there. Writes show the resolved absolute path and ask for
confirmation outside the workspace.

Secrets are never stored in JSON config files. HF tokens, remote endpoint keys
and MCP/connector credentials use Windows Credential Manager on Windows.

## What is included

### Chat

- streaming responses with Stop and Regenerate
- collapsible `<think>` reasoning in muted styling
- Markdown, syntax-highlighted code, copy actions and inline correction
- LOCAL/CLOUD endpoint chip, health/failover banner and server console drawer
- text attachments, `/fix`, `/teach`, thumbs-down feedback and export
- SQLite history and persistent memory keys

### Tools

The original 27 built-ins cover live data, files/code, memory, generative UI
and Windows desktop actions. The owner-approved 28th built-in is `read_url`
for guarded public web-page reading and citations. Tool arguments are
validated with pydantic/JSON Schema before execution; failures return a
corrective `role:"tool"` message so the model can retry.

Generative UI tools render natively:

- sortable table with CSV/PNG export
- QtCharts bar/line/pie/scatter chart with PNG export
- cards, vertical timeline, side-by-side diff

### Projects, local knowledge and code mode

Projects provide instructions, a root folder, conversation assignment,
extension scoping and knowledge sources. Selected documents/code are indexed
locally with SQLite FTS5 and cited as `[L1]`, `[L2]`, etc. Supported baseline
knowledge files include text/Markdown/code, PDF and DOCX.

Code mode provides a project tree and preview, checkpointed overwrites,
restore, read-only Git status and user-defined argv test profiles. Test
profiles are started only by the user from the UI — they are not model tools,
and there is no shell or Git-mutation tool.

## Skills

VISION uses the portable Agent Skills folder format:

```text
skill-name/
├── SKILL.md
├── scripts/       optional
├── references/    optional
└── assets/        optional
```

`SKILL.md` begins with YAML frontmatter containing at least `name` and
`description`. Imports accepted in **Extensions → Skills**:

- a canonical folder;
- a lone `SKILL.md`;
- a plain `.md` (VISION asks for name/description and converts it);
- `.zip` containing `SKILL.md` at the root or in one top-level folder;
- `.skill`, treated as a ZIP container with the same canonical contents.

Import never executes bundled code. Only metadata is discoverable initially.
Full instructions load when the user selects a skill or the model calls
`skill_load`. Python scripts can be used only through the approved
`run_python` flow; other executable script types are blocked in v1.

## MCP and Connectors

VISION is an MCP host for stdio and Streamable HTTP servers. Legacy SSE is
available as a transport record if a named server requires it. Add servers in
**Extensions → MCP Servers**, run **Test/discover**, review the tool list and
fingerprint, then explicitly trust that toolset. External tools default to
**Ask every time**. A changed tool description/schema changes the fingerprint
and requires re-approval before the tool is registered again.

Safety boundaries:

- stdio servers are stored as program + argv, never a shell command line;
- environment/header secrets are credential references in JSON;
- the model and skills cannot add/enable servers or change permissions;
- resources are user-attached and prompts are user-invoked;
- server-initiated sampling is unsupported in v1;
- remote calls display the server and arguments in tool/approval cards.

A baseline **Connector** is a named remote MCP endpoint with none/API-key/
Bearer auth in **Extensions → Connectors**. OAuth and first-party named
service adapters are deliberately separate integrations; they are not bundled
or implied by the custom connector feature.

## Feedback that feeds future training

Local feedback is a JSONL queue at:

```text
%LOCALAPPDATA%\VISION\telemetry\needs.jsonl
```

This exact frozen name is intentional. **Activity & Feedback** can export a
copy as `feedback.jsonl`, but the app/HF canonical name remains
`telemetry/needs.jsonl`.

Each row contains exactly:

```json
{"ts":"...","category":"...","prompt":"...","model_output":"...","signal":"...","detail":"..."}
```

Signals include `tool_schema_error`, `tool_runtime_error`, `code_error`,
`user_negative` and `topic_usage`. Secrets are redacted, file/clipboard
contents are excluded, and upload is opt-in/default OFF. When enabled, queued
rows append-merge into `SpiritKing/VISION-v2-Agent-Dataset` at
`telemetry/needs.jsonl` in a background thread; local rows clear only after a
confirmed successful upload. A token with write access to that dataset is
required.

## Build a Windows executable

For an optional fully bundled server, first extract a supported llama.cpp
Windows release into `third_party\llama.cpp\` (see `third_party\README.md`).
Then:

```powershell
.\scripts\build.ps1
```

Output:

```text
dist\VISION\VISION.exe
```

The script uses a one-folder PyInstaller build, `assets\vision.ico` and
`assets\version_info.txt`.

## Tests and falsification bench

```powershell
python -m pytest
python scripts\run_bench.py --mode replay
python scripts\probe_server.py --base-url http://127.0.0.1:8080/v1
```

The benchmark reports counts by stratum, not a single pass percentage:
entailment, fabricated attributes/provenance, false precision, schema/runtime
and tool-choice defects. Extension consent violations are a zero-tolerance
release blocker. See `docs\QUALITY_RATCHET.md`.

Windows-only behaviour must also pass `docs\WINDOWS_SMOKE_TEST.md` on real
hardware before a release claim; headless tests cannot prove VRAM behaviour,
Qt rendering on the target GPU stack, pywin32 window focus, or a real GGUF's
tool-call stream.

## Troubleshooting

### Server exits immediately / CUDA or VRAM errors

- Open **Server console** (`Ctrl+Shift+S`) and read the last lines.
- Lower **Settings → GPU layers** from 99 (for example 30, 20 or 0) and restart.
- Confirm the model path points to the complete 5.24 GiB GGUF.
- For CUDA builds, make sure the llama.cpp build and matching CUDA runtime
  files came from the same release. Vulkan is the smaller vendor-neutral
  fallback; CPU works but will be slower.
- VISION uses `--flash-attn on`; the pinned b11819 binary rejects the older
  value-less form with “expected value for argument.” If a future llama.cpp
  build rejects the valued form, do not silently change it in a support
  session; capture the stderr and use a supported pinned release until the
  command is amended.

### Port 8080 is already in use

VISION never kills another process. Change **Settings → Local port**, or stop
the other service yourself and restart VISION's server.

### Antivirus blocks downloads or `llama-server.exe`

Unsigned local ML executables and large downloads can be quarantined. Download
only from the official release/Hugging Face locations configured by the app,
restore only files you expected, and verify the model size/path in Model
Manager. A code-signed production installer is outside this source build.

### Cloud failover does not work

- The remote URL should normally end in `/v1`.
- `GET /v1/models` must succeed.
- If the service needs a key, set **Remote Bearer key** in Settings.
- Local mode does not silently fall back; choose Auto for automatic failover
  after two local health failures.

### MCP tools do not appear

- Enable the server and run **Test/discover**.
- Trust the exact discovered fingerprint.
- Check that the conversation's extension-tool cap has not truncated them.
- If tools/schemas changed upstream, re-run discovery and approve again.

### A skill does not activate

- Enable it in **Extensions → Skills**.
- Validate that the folder name equals frontmatter `name`.
- Use `/skill-name` or `@skill-name` for explicit activation.
- Skills containing non-Python executables may only partially work by design.

## Development notes

Target runtime is Python 3.11 on Windows; pure-logic tests also run headlessly
on newer Python versions. `VISION_HOME` can override the app-data root for
isolated development/testing. See `ARCHITECTURE.md`, `PLAN.md`,
`docs\UI_SPEC.md`, `docs\EXTENSIONS_SPEC.md` and `docs\FEATURES_SPEC.md`.
