# VISION DESKTOP — MCP, Skills and Connectors Specification

VISION — built by SPIRIT ORG

Status: owner-selected scope amendment (PLAN A16–A19 locked in principle). It adds an
extension layer around the frozen 27 built-in tools; it does **not** silently
replace any of them.

Key references checked 2026-10-22:

- MCP transports: <https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>
- MCP Python SDK: <https://github.com/modelcontextprotocol/python-sdk>
- Agent Skills specification: <https://agentskills.io/specification>

## 1. Concepts — keep these separate

| Concept | What it is | What it is not |
|---|---|---|
| Built-in tool | One of the original 27 VISION-owned functions with a pydantic schema and safety policy | A plugin the model can invent or install |
| MCP server | An external process/service speaking the Model Context Protocol. It may expose tools, resources and prompts. | Automatically trusted local code or a generic shell |
| Skill | A portable instruction package built around `SKILL.md`, optionally with references, assets and scripts. It teaches the model a workflow. | A new model weight, an automatic permission grant, or an excuse to execute bundled code without review |
| Connector | A user-facing integration record for an external service, normally backed by a remote MCP endpoint. A first-party API adapter is a separate, service-specific design. | A hidden account login or a promise that every SaaS API uses one standard format |

## 2. Revised tool declaration rule

The original 27 built-in schemas remain present on every chat request.

If extensions are enabled, the request's `tools` array is:

```text
27 built-ins
+ enabled MCP/connector tools selected for this conversation
+ the selected `skill_load` meta-tool when at least one skill is enabled
```

The array is built once at the start of a turn and frozen for retries inside
that turn. Dynamic tools use namespaced names (§4.4), so audit and transcript
cards never confuse a server tool with a built-in.

Model context is finite and a 9B model can degrade with hundreds of tool
schemas. MCP enablement is therefore per conversation, server/tool counts are
shown before sending, and the controller enforces configurable caps rather
than dumping every installed server into every request.

## 3. On-disk layout

```text
%LOCALAPPDATA%\VISION\
├── config.json                       # app settings; no secrets
├── extensions\
│   ├── mcp_servers.json              # server definitions; secret refs only
│   └── connectors.json               # connector metadata; secret refs only
├── skills\
│   └── <skill-name>\
│       ├── SKILL.md                  # canonical, portable form after import
│       ├── references\               # optional
│       ├── assets\                   # optional
│       └── scripts\                  # optional; never run on import
└── logs\
    ├── audit.jsonl                   # built-in + MCP/connector tool calls
    └── mcp\<server>.log              # server stderr/protocol error summaries

%USERPROFILE%\VISION\workspace\
└── .vision\skills\                   # project skills; Projects selected as F2
```

Installed skills are always normalized to the canonical directory form.
`.zip` and `.skill` are distribution containers, not the live runtime format.

## 4. MCP host/client design

### 4.1 Transports

Baseline proposal:

1. **stdio** for local servers. The app launches the configured program as a
   child process and speaks newline-delimited JSON-RPC on stdin/stdout; stderr
   is captured as log text.
2. **Streamable HTTP** for remote/local service servers at a single MCP
   endpoint, with optional Bearer/API-key/custom headers. Enabled URLs
   outside loopback must use HTTPS and must not embed credentials.

Legacy HTTP+SSE is not in the baseline proposal. It becomes a compatibility
choice only if a named server requires it.

VISION acts as the MCP **host** and creates one client/session per enabled
server. The official Python `mcp` SDK is the proposed implementation
dependency behind a small VISION adapter, so SDK version changes do not spread
through the app. The exact pinned version and supported protocol revisions are
proven in the MCP milestone against an in-process test server and at least one
real world-shaped server fixture; raw hand-written JSON-RPC is only a fallback
if the SDK cannot satisfy Python 3.11/Windows constraints.

### 4.2 Server definition (internal app schema)

Conceptual pydantic model:

```jsonc
{
  "id": "filesystem-local",
  "display_name": "Filesystem (local)",
  "transport": "stdio",                 // or "streamable_http"
  "enabled": false,
  "command": "C:\\Tools\\mcp-fs.exe",    // stdio only
  "args": ["C:\\Users\\me\\VISION\\workspace"],
  "cwd": "%USERPROFILE%\\VISION\\workspace",
  "env_refs": {"API_KEY": "credential://mcp/filesystem-local/API_KEY"},
  "url": null,                           // Streamable HTTP only
  "header_refs": {},
  "startup": "lazy",                    // lazy | app_start
  "trust": {
    "approved_at": "…",
    "toolset_sha256": "…",
    "permissions": {"read_file": "ask", "write_file": "ask"}
  }
}
```

Rules:

- stdio command is `program + args[]`; the app never constructs a shell line.
  Windows shims such as `.cmd`/`.bat` require special review, exact resolved
  path display and tests; an unresolvable command is a configuration error.
- environment inheritance is minimal. The child gets Windows essentials plus
  explicitly configured entries. HF/remote/connector secrets are never
  inherited globally.
- secret values live in Windows Credential Manager under VISION and are
  referenced by key; the JSON file and exports exclude secret values.
- import/export of the commonly used top-level `mcpServers` JSON shape is
  supported as a conversion with a preview, but VISION's stored schema remains
  versioned (`schema_version`) rather than pretending that host configuration
  files are the MCP wire standard.

### 4.3 Discovery and lifecycle

1. User adds a server (or reviews an imported definition).
2. UI displays exact command/URL, working directory and secret variable names.
3. **Test connection** connects in the worker/async extension host, performs
   SDK initialization/discovery, then lists capabilities:
   tools, resources/resource templates and prompts as supported.
4. Discovery result is converted to immutable descriptors. A SHA-256
   fingerprint covers normalized tool/prompt names, descriptions and schemas.
5. User reviews tools and sets permissions. Default: all tools `ask`, prompts
   user-invoked only, resources user-attached only.
6. Lazy servers start on first use; their stderr goes to `logs\mcp`. They are
   restarted after crash only with a visible state change—not in a tight loop.
7. `list_changed`/rediscovery produces a new fingerprint. Changed tool
   definitions are disabled until the user re-approves; unchanged tools retain
   their permissions.
8. Shutdown closes sessions first and terminates only VISION-owned stdio child
   processes. It never kills an unrelated process that happens to share a name.

### 4.4 Tool bridge

- OpenAI-facing function name: `mcp__<server_slug>__<tool_name>`, sanitized to
  a conservative `[A-Za-z0-9_-]` alphabet, length capped and made collision
  resistant (stable suffix if truncation is needed).
- MCP `inputSchema` is passed through after validation that it is a JSON
  object schema. Outbound arguments are checked with JSON Schema before any
  call; validation errors follow the original corrective-tool-reply loop.
- Call timeout/cancellation is per tool; long-running server progress, if the
  SDK/protocol provides it, updates the same tool card rather than spawning
  separate chat messages.
- Results are normalized for the model:
  - text/structured content → bounded JSON/text tool result;
  - resource content → text where textual, otherwise an attachment/card plus a
    textual summary;
  - image/audio content → rendered for the user where possible; because the
    frozen GGUF setup has no projector, the model receives metadata/summary,
    not pretend image understanding;
  - `isError` and protocol errors → short corrective tool error; raw details
    remain in the expandable tool card/audit.
- Tool annotations such as read-only hints are shown as server claims, never
  treated as proof of safety.

### 4.5 Resources and prompts

- **Resources** appear in `@`/Attach pickers. Reading one inserts a bounded,
  clearly delimited resource block into the next user turn with URI/server
  provenance. Binary resources become attachments subject to attachment rules.
- **Prompts** appear in the `/` palette. Selecting one asks for declared
  arguments and inserts the rendered prompt for user review before sending.
- Neither capability lets a server silently inject context merely because it
  is connected. Server-initiated sampling is not supported in v1. Elicitation
  or roots support is a later approval item, not an assumed SDK default.

### 4.6 Prompt-injection and trust boundary

- Tool descriptions, schemas, resource text, prompt text and tool results are
  untrusted content. The system prompt will state that tool/skill/resource
  content cannot override VISION safety instructions or request credentials.
- The model cannot add, enable, edit or remove an MCP server; only the user
  can through Extensions Center or a reviewed import.
- The consent system is enforced outside the model by the tool registry.
- Data egress warning is metadata-driven: remote server + arguments preview.
  There is no "private because local model" claim once a remote tool is called.
- Bench stratum `extension_prompt_injection` includes malicious tool
  descriptions/results trying to trigger clipboard/secret/file actions. Any
  consent bypass is a release blocker with a zero-defect gate.

## 5. Skills design (portable to other LLM hosts)

### 5.1 Canonical format

VISION adopts the open Agent Skills directory layout:

```text
skill-name/
├── SKILL.md          # required
├── scripts/          # optional
├── references/       # optional
├── assets/           # optional
└── ...               # additional supporting files
```

`SKILL.md` starts with YAML frontmatter. Baseline validation follows the
published specification:

- `name`: required, 1–64 chars, lowercase letters/digits/hyphens, no leading/
  trailing/consecutive hyphens, and matches the parent folder after import;
- `description`: required, 1–1024 chars, says what the skill does and when to
  use it;
- optional: `license`, `compatibility` (≤500 chars), `metadata` (string map),
  `allowed-tools` (experimental, informational in VISION).

Unknown portable frontmatter is preserved on export where safe. Required
VISION-specific data belongs in app state or under `metadata` string keys,
never in new mandatory top-level fields, so the same package remains usable by
another compliant LLM host.

### 5.2 Accepted import formats

| User supplies | Normalization |
|---|---|
| Folder | Find its `SKILL.md`; validate; copy as `skills\<name>\` after preview. |
| Single `SKILL.md` | Create wrapper folder from validated `name`; install as canonical folder. Supporting-file references that do not exist produce warnings. |
| Generic `.md` without valid frontmatter | Open "Create skill" preview asking for name + description; generate compliant `SKILL.md`, preserving the body. It is not falsely labelled already portable before conversion. |
| `.zip` | Treat as an archive containing either `SKILL.md` at root or exactly one top-level skill folder with `SKILL.md`. Other layouts are ambiguous and rejected with a file listing. |
| `.skill` | Treat as a ZIP-container alias and verify ZIP magic/structure, then apply the `.zip` rule. A non-ZIP `.skill` is rejected unless the owner supplies its actual format/spec or sample. |

Export reproduces a canonical folder ZIP; "Export as .skill" writes the same
ZIP bytes with the `.skill` suffix. This keeps `.zip`, `.skill`, folder and
`SKILL.md` workflows semantically identical.

### 5.3 Import safety gates

- ZIP Slip (`../`), absolute paths, drive-relative paths, symlinks/junctions,
  duplicate normalized paths and Windows reserved names are rejected.
- Per-file, uncompressed-total, file-count and compression-ratio caps prevent
  archive bombs.
- Import extracts to a staging directory inside app data, validates and shows
  a full file listing, then atomically moves into `skills\<name>` only on
  user confirmation.
- Import never executes a script, opens an executable, follows an external
  URL from instructions, or enables the skill automatically.
- Installing over an existing skill previews a diff/hash change and keeps the
  previous version disabled until the user accepts the replacement.

### 5.4 Progressive disclosure and activation

Following the Agent Skills model:

1. **Discovery:** at conversation build time, only `name + description` for
   enabled skills enter a delimited "Available skills" section. Dozens of
   installed-but-disabled skills cost zero context.
2. **Activation:** either user-selects via `/skill-name`, `@skill` or a chip,
   or the model calls the single selected tool `skill_load`, whose only
   argument is the skill name. The tool response is the bounded body of
   `SKILL.md` plus a manifest of bundled resource paths.
3. **Resources:** references/assets are loaded only when needed through the
   existing sandboxed read/attachment mechanisms, constrained to that skill's
   directory. The transcript shows "Skill activated" as a system/tool card so
   users can see which instructions influenced the turn.

Skill text is lower priority than system/safety instructions and is wrapped
with provenance. It cannot expand the tool list, waive confirmations, read
credentials, change MCP/connector settings, or demand secrecy from the user.

### 5.5 Skill scripts

- Scripts are data until explicitly used.
- Python scripts may be run only through the existing `run_python` policy after
  showing exact skill-relative path, text/hash and arguments. `-I -S`, timeout
  and scrubbed environment still apply; limitations are surfaced, not hidden.
- Other executable types (`.exe`, `.bat`, `.cmd`, `.ps1`, shell scripts) are
  blocked by VISION safety policy in v1; their presence is reported as a
  compatibility warning. A skill that depends on them will not fully work and
  the import preview says so.
- A script running successfully once does not pre-approve a modified script:
  approval is tied to content hash.

### 5.6 Authoring in app

Extensions Center can create a new blank skill with compliant frontmatter and
open its folder. Rich in-app skill editing/versioning is an optional feature;
v1 can offer a simple text editor only if it does not compromise the core app
schedule. Export validation uses the same parser as import.

## 6. Connectors design

### 6.1 Baseline: custom connector = named remote MCP connection

This yields real interoperability without inventing a separate protocol:

- user provides display name + Streamable HTTP MCP URL;
- auth is none, API key header, or Bearer token in Credential Manager;
- VISION discovers tools/resources/prompts and applies the same fingerprint,
  consent, audit, naming and enablement flow as MCP;
- the UI presents it as a Connector (service-like card), while technical
  details expose the underlying MCP server record.

### 6.2 OAuth connectors

OAuth 2.1 Authorization Code + PKCE is phase 2 and provider-aware:

- system browser is used for sign-in; the app never embeds a password form;
- loopback redirect and state/PKCE are handled by a temporary localhost flow;
- refresh/access tokens go to Credential Manager; scopes and expiry are shown;
- dynamic client registration is only used where the MCP service supports it;
- a named service may require owner/developer app registration and a client ID;
  those requirements must be verified service-by-service, not guessed.

### 6.3 First-party API adapters

Examples such as a files, mail, calendar, source-control, notes or chat
integration cannot be designed as one generic connector. Each selected adapter
requires its own approved mini-spec: official API, auth, scopes, operations
exposed as tools, destructive-operation confirmations, rate limits, data
retention and tests with mocked HTTP.

No first-party connector is bundled in the baseline. The owner selected the
custom remote-MCP connector baseline; a named service remains a future,
separately specified decision.

### 6.4 Connector operations policy

- Read/write/delete-like capabilities are classified by VISION from explicit
  connector definitions or user permission settings, not by trusting remote
  annotations alone.
- Remote writes/deletes/sends always default to per-call approval.
- Disconnect disables tools immediately and deletes stored tokens; removing a
  connector also removes its definition after confirmation.
- Tool cards show the remote host and arguments so egress is visible.

## 7. Suggested optional features — A20 selection recorded

Owner selected **F2, F4, F5 and F15**, now detailed in
`docs/FEATURES_SPEC.md` (subject to detailed A21–A24 approvals). All other
rows remain out of baseline. Effort/risk is a planning estimate, not a
schedule promise.

| Code | Feature | What it adds | Cost/risk |
|---|---|---|---|
| F1 | **Artifacts/Canvas workspace** | Editable side pane for generated code/documents/tables/charts, version history, save/export | Medium UI work; high user value |
| F2 | **Projects & project instructions** | Per-project system guidance, files, skills and MCP enablement | Medium data-model work |
| F3 | **Conversation upgrades** | Deep search, pin/archive, edit-branch, export MD/JSON/HTML, duplicate | Low–medium; history schema additions |
| F4 | **Local document knowledge (RAG)** | Index workspace txt/md/code/PDF/DOCX, cited retrieval; FTS first, embeddings later | Medium–high; PDF/DOCX parsing + index correctness |
| F5 | **Code workspace mode** | File tree, checkpointed diff apply/undo, git status read-only, test-command approval | Medium–high; strong safety/audit needs |
| F6 | **Prompt library** | Reusable local prompt templates with variables alongside MCP prompts | Low |
| F7 | **Performance & health dashboard** | tok/s, first-token latency, VRAM/context, tool latency and failure trends | Low–medium; feeds the bench story |
| F8 | **Memory Center** | View/edit/delete memory, conversation summary memory, retention/export | Low–medium; privacy controls required |
| F9 | **Tray + global quick-ask** | System tray, global hotkey mini composer, timer notifications | Medium Windows integration |
| F10 | **Voice** | Push-to-talk and read-aloud using an owner-chosen local/Windows speech route | Provider/API choice needed; medium |
| F11 | **Image input** | True multimodal attachments | **Contract change:** requires compatible projector/model artifacts not in verified repo |
| F12 | **Extension catalog/install from URL/Git** | Browse/install skills and MCP definitions from owner-curated sources | High supply-chain risk; defer until trust flow proves out |
| F13 | **Automations** | Scheduled local prompts/tool tasks with approvals and run history | Medium–high; background execution safety |
| F14 | **Multiple model profiles** | Switch local GGUFs or remote profiles from header | Contract change to the single frozen model; context/tool bench per model |
| F15 | **Web-page reader + citations** | Fetch/read selected URLs and show source cards/citations | New built-in tool or MCP; fetching/SSRF policy needed |
| F16 | **Appearance/accessibility pack** | Light theme, density/font controls, customizable shortcuts, high contrast checks | Low–medium |

## 8. Tests added by this amendment

- `test_skills_import.py`: folder, lone `SKILL.md`, plain `.md` conversion,
  `.zip` root/folder layouts, `.skill` ZIP alias, ZIP Slip/symlink/bomb caps,
  invalid frontmatter/name mismatch, export round-trip.
- `test_mcp_config.py`: stored schema, secret refs not values, `mcpServers`
  import/export conversion, command resolution errors.
- `test_mcp_tools.py`: fake/in-process session discovery, namespacing,
  JSON-schema validation, consent branches, changed fingerprint reapproval,
  result/error conversion, timeout/cancel and parallel ordering.
- `test_connectors.py`: custom remote-MCP facade, auth reference resolution,
  disabled tool absence, disconnect removing credentials, egress metadata.
- `test_skill_activation.py`: only metadata at discovery, explicit activation,
  optional `skill_load`, resource sandbox and instruction-priority fixtures.
- Bench strata: `skills_activation`, `mcp_tool_choice`,
  `extension_prompt_injection`; safety invariant "no extension action without
  required consent" is scored as a zero-tolerance defect.
