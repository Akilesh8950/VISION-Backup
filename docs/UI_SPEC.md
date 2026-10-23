# VISION DESKTOP — UI Specification

VISION — built by SPIRIT ORG

Status: proposed design for owner approval (PLAN A15). Comparator names appear
only in this planning document for pattern reference. They never appear in the
shipped UI, source comments, prompts, or marketing copy inside the app.

## 1. Design intent

VISION should feel like a focused desktop workbench, not a web page wrapped in
an executable:

- the conversation remains the center of the app;
- local-model and endpoint state are always visible rather than hidden;
- tools, skills, MCP calls, and connector activity are inspectable;
- generated tables, charts, code, and diffs become useful work surfaces rather
  than transient chat decoration;
- every consequential action shows its target, arguments, and approval state;
- the app remains fully understandable offline and does not depend on accounts.

## 2. Reference patterns

These are separate product patterns to learn from, not visual assets or text
to copy.

| Reference | Pattern worth adopting | VISION interpretation |
|---|---|---|
| ChatGPT desktop/web | Collapsible conversation sidebar; search and recents; model control in the header; round, unobtrusive composer; `+` menu for attachments and capabilities; hover message actions; regenerate and edit flows | Keep the sidebar/composer pattern, but make the endpoint chip specific to VISION: LOCAL/CLOUD, server health, context and model build are visible. The `+` menu exposes files, Skills, MCP resources/prompts, and workspace items. |
| Claude desktop/web | Calm reading column; collapsed reasoning and tool activity; Artifacts-style split workspace for code, documents, and interactive outputs; content remains editable and versionable | Generative-UI results and code diffs open in an optional right-side work panel. `<think>` and routine tool detail stay collapsed until inspected. |
| Modern IDEs | Status bar, command palette, problems/output drawer, per-file diff review, explicit workspace root | Add a command palette, server console drawer, audit/activity drawer, and workspace indicator. Diff application is always a user action, never a hidden side effect. |
| Native Windows 11 apps | Segoe UI Variable, system accent choices, title-bar integration, notifications, taskbar progress, keyboard/focus behaviour | Use native Qt widgets and dialogs; respect Windows text scaling and high-DPI; use Windows notifications only for timers/download completion. |

## 3. Main window layout

```text
┌──────────────┬──────────────────────────────────────────┬────────────────────┐
│ Sidebar      │ Header: title | LOCAL ● | 16k ctx | ⋯   │ Optional work panel │
│              ├──────────────────────────────────────────┤                    │
│ + New chat   │                                          │ Artifact / chart / │
│ Search       │             Centered transcript          │ table / diff /     │
│              │             max readable width          │ MCP resource       │
│ Pinned       │                                          │                    │
│ Today        │ User message                             │ Tabs or breadcrumb │
│ Yesterday    │ Assistant response (Markdown)            │                    │
│ Previous 7d  │  ▸ Thinking                              │ Export / Save /    │
│ Older        │  [Tool card: get_weather ✓ 1.2s]         │ Copy / Close       │
│              │  Answer…                                 │                    │
│ Projects*    │                                          │                    │
│ Extensions   ├──────────────────────────────────────────┤                    │
│ Models       │ Active chips: skill | MCP | workspace    │                    │
│ Activity     │ ┌──────────────────────────────────────┐ │                    │
│ Settings     │ │ Message VISION…             [Send] │ │                    │
│              │ └──────────────────────────────────────┘ │                    │
│ User / About │ Enter send · Shift+Enter line · ~tokens  │                    │
└──────────────┴──────────────────────────────────────────┴────────────────────┘
```

`*` Projects was selected as feature F2; its management UI lands at PLAN M12.

### Layout rules

- Sidebar defaults to roughly 260 px and collapses to an icon rail.
- Transcript text has a maximum readable width; tables/charts may use a wider
  region or move to the work panel.
- The work panel is hidden by default, opens for a chart/table/diff/resource,
  and is user-resizable. Closing it never deletes the underlying result.
- Header never hides server state behind Settings: endpoint, health, active
  model file/build, and context size remain visible.
- The server console is a bottom drawer, not a separate top-level workflow.

## 4. Sidebar and navigation

### Conversation section

- **New chat** button and keyboard shortcut.
- Search across local conversation titles and text.
- Groups: Pinned, Today, Yesterday, Previous 7 days, Older.
- Context menu: rename, pin/unpin, export, archive and delete. Deleting a
  local conversation is a normal user-initiated app action with confirmation;
  it is not exposed as a model tool.
- Each conversation remembers endpoint, active workspace, enabled skills, and
  enabled MCP servers/connectors.

### App sections

- **Chat**
- **Projects** — project settings, instructions and knowledge sources (F2/F4)
- **Extensions** — built-in tools, MCP servers, Skills, Connectors
- **Models** — installed GGUF, update status, server binary and VRAM/context
- **Activity** — audit log, feedback queue, downloads and timers
- **Settings**
- **About** — version, build/commit and the product credits line

## 5. Empty state

The first screen in a new chat contains:

- a restrained `VISION` wordmark;
- the endpoint state, e.g. "Local model ready" or "Starting local server…";
- four suggestion cards chosen to demonstrate genuine local capabilities:
  weather for a city, system status, a chart from numbers, and a workspace
  file task;
- no fake "recent activity", placeholder account area, advertisements, or
  invented usage statistics.

Clicking a suggestion inserts it into the composer; it does not silently send.

## 6. Transcript

### User messages

- Subtle right-aligned bubble or full-width block, with attachment chips.
- Hover actions: copy, edit-and-resend (creates a new branch/turn rather than
  rewriting stored history in place), and remove attachment before sending.

### Assistant messages

- Plain left-aligned reading column, no decorative bubble.
- Markdown rendering with syntax-highlighted fenced code and per-code-block
  Copy/Save actions.
- Reasoning appears as a collapsed muted-italic **Thinking** disclosure with
  token/time summary once complete.
- Footer actions: copy, regenerate, thumbs up/down, **Correct this**.
- "Correct this" opens an inline correction field and writes the same event as
  `/fix …` to the feedback pipeline.
- If streaming ends early, an unobtrusive "Incomplete response" marker and a
  Retry/Continue action are shown.

### Tool cards

Every built-in, skill-assisted, MCP, or connector call uses the same card:

- source icon/label (Built-in, skill name, MCP server, Connector);
- tool name and short human-readable status;
- states: queued, waiting for approval, running, succeeded, failed, blocked,
  cancelled;
- collapsed result summary; expandable pretty-printed arguments/result;
- duration and destination (local workspace, network host, MCP server, etc.);
- approval actions embedded in the card when consent is required;
- error copy translated into a short explanation, with raw details expandable;
- parallel calls appear as separate cards and finish independently.

No raw `ui_*` JSON is rendered as assistant text. Native widgets appear inside
the transcript and can be opened in the work panel.

## 7. Composer

- Multi-line, auto-growing editor; `Enter` sends, `Shift+Enter` inserts a line.
- Send becomes Stop while streaming.
- The `+` menu contains: Attach files, Add workspace file, Use skill,
  Attach MCP resource, Use MCP prompt, and optional choice-gated inputs.
- `/` opens a command palette: app commands, `/fix`, `/teach`, installed skill
  invocations and MCP prompts.
- `@` opens mention search for workspace files, skills, and MCP resources.
- Drag-and-drop and paste are supported for supported attachment types.
- Attachments remain removable chips before sending; unsupported binaries are
  rejected with the reason, not silently ignored.
- Chips above the composer show the active skill set, MCP servers, connectors,
  project/workspace and endpoint. A chip can be disabled for the next turn.
- Token/context meter is labelled as an estimate unless approval A11 changes it.

## 8. Approval and consent UX

Approval is a workflow, not a generic Yes/No prompt.

An approval card/dialog shows:

1. who wants to act (VISION built-in tool, a named skill, named MCP server or
   connector);
2. exact action and arguments;
3. local target path or remote destination;
4. whether data leaves the device and, if so, what;
5. risk notes (write, executable, large clipboard, skill script, external tool);
6. choices appropriate to the action: Allow once, Allow for this conversation
   or session where the original safety policy permits it, Deny.

Additional rules:

- availability of an MCP or connector tool is not consent to use it; default
  permission for external tools is **Ask every time**;
- skill `allowed-tools` metadata is informational and never grants permission;
- if an MCP server's tool name, description or schema hash changes after it
  was trusted, the next use requires review again;
- approvals never auto-focus the affirmative button for destructive, code, or
  egress actions;
- denial sends a normal tool error back to the model so the turn stays valid.

## 9. Extensions Center

One management surface with four tabs.

### Built-in tools

- Read-only catalog of the original 27 tools, grouped as in the contract.
- Shows parameter schema, side-effect class and whether confirmation applies.
- Per-tool enablement is not in the baseline because the contract says all
  27 schemas are declared on every request.

### MCP Servers

- Server cards/table: name, transport, command or URL, state, protocol/tools
  discovered, last error, tool count, trust state.
- Add/edit dialog supports:
  - local stdio command represented as program + argument list (never a shell
    command line), working directory and environment entries;
  - remote Streamable HTTP URL and headers/auth reference;
  - optional legacy SSE only if approved in scope;
  - environment values and headers marked secret are stored through Windows
    Credential Manager and rendered as masked references;
  - Test/discover, Restart, Logs, Enable/disable and Remove.
- Tool detail drawer lists every discovered tool, resource and prompt with
  per-tool permission: Ask, Allow low-risk reads (explicit owner choice), Block.
- Import/export of the common `mcpServers` JSON shape is a review-first flow;
  secrets are not included in an export by default.

### Skills

- Grid/table: skill name, description, source, version/hash, enabled state,
  validation warnings, bundled files and script count.
- Import choices: directory, `SKILL.md`, generic `.md`, `.zip`, `.skill`.
- Import preview is mandatory before installation: frontmatter, instructions,
  references/assets, and scripts are visible; nothing executes during import.
- Detail view: Rendered instructions, Source, Files, Requested tools,
  Compatibility, Validation, Export (folder/.zip/.skill), Remove.
- Enablement scopes: globally available, current conversation only, disabled.
- Invalid Agent Skills packages are rejected with field-level errors, while a
  plain `.md` can be converted through a small "Create skill" form.

### Connectors

- Shows installed/custom connectors separately from raw MCP servers.
- Each connector displays service alias, connection state, auth type, granted
  scopes if known, available tools and last-used time.
- "Add custom connector" in the baseline proposal means a user-named remote
  MCP endpoint; named first-party service adapters require a separate owner
  choice and service-specific API/OAuth design.
- Disconnect removes tokens from Credential Manager. Remove also deletes the
  local connector definition after confirmation.

Detailed semantics are in `docs/EXTENSIONS_SPEC.md`.

## 10. Models and server surfaces

### Model Manager

- Installed model filename, SHA/commit, size and path.
- Local server binary path/build, GPU layers, context and port.
- Check for updates, progress, disk-space warning and `pending_restart` state.
- Clear warning that a new model becomes active only on next launch.

### Server console drawer

- Start/Stop/Restart server, endpoint and process state.
- Live stdout/stderr tail with redaction, pause autoscroll, Copy and Clear.
- Health events, restart attempt and cloud-failover events appear in plain
  language above raw logs.

### First-run wizard

Steps: welcome → find/download `llama-server` → download model → GPU layers
with detected VRAM guidance → start/health check → endpoint choice → optional
remote URL → privacy/feedback choice (default off) → Extensions intro (can skip).

## 11. Activity and feedback

- **Activity** combines audit events, timers, downloads, MCP server events and
  feedback upload status in a chronological local view.
- **Feedback queue** shows pending feedback rows (timestamp, signal, category,
  redacted prompt, detail), with View, Delete one, Clear all, Export JSONL and,
  if opt-in is enabled, Upload now.
- The settings label remains "Send anonymous feedback to SPIRIT ORG" and is
  default OFF.
- Upload progress/failure is shown without exposing the HF token.

## 12. Settings information architecture

- General: startup behaviour, default endpoint mode, workspace location
- Model & Server: model path, server binary, port, context, GPU layers
- Endpoints: remote base URL, model override, optional Bearer credential
- Extensions: shortcuts to MCP, Skills and Connectors management
- Privacy & Feedback: opt-in upload, queue controls, credential status
- Data: history location/export, memory viewer if optional feature selected
- Appearance: theme, density and font size (dark default)
- Shortcuts: keyboard reference; user editing only if selected as optional work
- About: version, commit and credits

Secrets fields show only "Stored/Not stored" plus Set/Replace and Remove.

## 13. Visual language

- Dark default based on neutral charcoal surfaces rather than pure black.
- One restrained accent used for send, focus and active states; status colours
  are semantic and paired with text/icons (never colour alone).
- Segoe UI Variable for interface text; Cascadia Code or Consolas fallback for
  code/JSON. System fonts are used—no network font dependency.
- 4 px spacing scale, compact desktop density, clear focus rings.
- Motion is limited to progress/spinner states; streaming itself is the motion.
- Respect Windows text scaling, contrast settings where Qt permits, keyboard
  navigation, tooltips and accessible names for icon-only buttons.

## 14. Error and offline states

| State | UI behaviour |
|---|---|
| Local server starting | Header amber dot + progress in console; composer stays editable, Send disabled until selected endpoint is usable |
| Local health failed twice | Persistent banner: restart attempted/failed, endpoint choice, View logs; Auto mode moves chip to CLOUD only after remote health succeeds |
| Remote unreachable | Banner with endpoint and retry; transcript/history remain available |
| MCP server down | Its chip/card turns error; unrelated chat/tools continue; tool call returns a normal tool error |
| Skill invalid | Excluded from composer catalog; detail view shows exact validation errors |
| Connector auth expired | Connector card asks Reconnect; model receives a clear tool error if invoked |
| Feedback upload failed | Activity warning; local queue retained; no repeated modal dialogs |
| Update downloaded | Toast + Models badge; does not interrupt current response |

## 15. Baseline keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+N` | New chat |
| `Ctrl+K` | Search/command palette |
| `Ctrl+Shift+S` | Toggle server console |
| `Ctrl+,` | Settings |
| `Ctrl+.` while streaming | Stop response |
| `Enter` / `Shift+Enter` | Send / newline |
| `Esc` | Close drawer/dialog or stop transient UI operation (never discard composer text) |

## 16. Selected feature-bundle surfaces (F2/F4/F5/F15)

- **Project view:** overview, instructions editor, root folder, conversations,
  knowledge sources with index state, extension enablement and test profiles.
- **Sources:** local and web citations share a Sources card. Local sources show
  filename + page/line; web sources show title + host + fetched time. Clicking
  opens the right work panel without replacing the transcript.
- **Code mode:** project file tree, read-only preview, changed-file list,
  checkpoint/restore actions, read-only Git status and named test profiles.
- **Index/reader jobs:** document indexing and URL extraction appear in
  Activity with progress/cancel; failures name the source and reason.
