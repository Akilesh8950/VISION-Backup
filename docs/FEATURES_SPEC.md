# VISION DESKTOP — Selected Feature Bundle Specification

VISION — built by SPIRIT ORG

Selected by owner under PLAN A20: **F2 Projects**, **F4 Local document
knowledge**, **F5 Code workspace mode**, and **F15 Web-page reader +
citations**. These are now planned baseline features after the core app and
extension milestones. Items marked "proposed A21–A24" still need approval
because they add APIs/dependencies not present in the frozen contract.

## 1. Shared principle

Projects provide the scope. Knowledge retrieval, code operations, skills,
MCP servers and connectors all inherit a project boundary when one is active.
Nothing about a project silently expands file-system, network or execution
permissions; it organizes context and remembers user choices.

## 2. F2 — Projects and project instructions

### User-visible behaviour

- Sidebar gets a **Projects** section with Create, Rename, Archive and Open.
- A project has:
  - name;
  - one root folder (default under `%USERPROFILE%\VISION\workspace\projects\<slug>`,
    or a user-selected existing folder after showing its absolute path);
  - project instructions (plain text/Markdown, edited by the user);
  - knowledge sources (files/folders selected for F4);
  - per-project enabled skills, MCP servers and connectors;
  - an optional project test profile for F5;
  - conversations assigned to it.
- New chats can be Unfiled or created inside a project. Moving a conversation
  changes future context only; historical messages are never rewritten.
- Active project is visible as a composer chip and in the header.

### Context rules

- Project instructions are inserted below system/safety instructions and above
  skill/resource content, in a delimited `Project instructions` block.
- Instructions cannot override safety, tool consent, sandbox paths, product
  identity, or reveal credentials.
- A project folder does **not** automatically become a broad sandbox exception:
  file tools still resolve and confirm under the original policy unless the
  user explicitly sets that folder as the active workspace for the conversation.

### Data model additions

- `projects(id, name, root_path, instructions, archived, created_at, updated_at)`
- `conversations.project_id` nullable
- `project_extensions(project_id, kind, extension_id, enabled)` for
  skills/MCP/connectors
- `project_sources(id, project_id, path, kind, enabled, last_indexed_at, last_error)`
- `project_test_profiles(project_id, name, program, args_json, cwd, updated_at)`

## 3. F4 — Local document knowledge with citations

### Baseline proposal

- Index only sources the user explicitly adds to a project (or attaches to an
  unfiled conversation). No whole-drive crawling.
- Proposed initial parsers (A21):
  - UTF-8 text/Markdown/code and common config formats: native reader;
  - PDF: `pypdf`;
  - DOCX: `python-docx`;
  - unsupported/encrypted/corrupt files are reported, never guessed.
- Chunking is deterministic (heading/paragraph-aware for documents,
  line windows for code) and records file path plus page or line range.
- Search v1 is **SQLite FTS5/BM25**, local only. No embeddings model, extra
  download, or cloud vector service in the baseline.
- File changes are detected by size/mtime/hash on project open and via a manual
  Re-index action; continuous background watching is not assumed.
- Exclusions: `.git`, virtualenvs/dependency/build folders, binaries over a
  configured cap, `.env`/secret-looking files, and anything matched by the
  project's local ignore list.

### Retrieval and citation flow (proposed A21)

1. At turn construction, if the active project has enabled knowledge, the
   controller retrieves a capped set of chunks for the user's latest prompt.
2. Chunks are inserted as explicitly delimited local source material with IDs
   such as `[S1]`, `[S2]`, each carrying display name and page/line locator.
3. The system-level source instruction tells the model to cite retrieved
   document claims using those IDs and not invent a citation.
4. The transcript renders a **Sources used** card. Clicking a citation opens
   the source in the right work panel at the relevant page/line where possible.
5. If no chunk clears the retrieval threshold, no fake source block is added.

The baseline uses controller retrieval rather than adding a 28th project-search
tool. A model-callable search tool can be proposed later if bench results show
that pre-retrieval misses too much.

### Quality gates

New bench stratum `project_rag_fidelity` scores:

- claims supported by retrieved chunks;
- citation present when a document claim is made;
- wrong/ghost citations as fabricated-provenance defects (D2);
- exact quantities absent from chunks as false precision (D3);
- retrieval returning the intended chunk in scripted fixture corpora.

## 4. F5 — Code workspace mode

### Layout

When a project root is active, the optional right work panel gains a **Code**
mode with:

- project file tree (respects exclusions, lazy expansion);
- read-only file preview with syntax highlighting in v1;
- files changed in this conversation;
- side-by-side diffs using the existing diff widget;
- Git status summary when the folder is a Git worktree;
- test profile runner and output pane.

This is a workbench around the existing chat/tool runtime, not a second IDE
editor. Direct in-panel text editing is not in the initial F5 scope unless the
owner later approves it.

### Checkpointed writes and undo

- Model-proposed file changes still enter through `write_file` and its existing
  resolved-path and confirmation rules.
- Before an approved overwrite of an existing project file, VISION snapshots
  the previous bytes to
  `%LOCALAPPDATA%\VISION\checkpoints\<conversation>\<timestamp>\…` and records
  original path, hash and size.
- The diff card offers **Restore checkpoint** after application. Restore is a
  user UI action, shows its own diff/confirmation, and does not create a model
  tool for arbitrary restoration or deletion.
- New files record a checkpoint tombstone so Undo can offer removing them via
  a user-confirmed UI operation. The original prohibition remains: the model
  has no file-deletion tool.

### Git (read-only baseline)

- VISION may invoke fixed argv Git queries it owns, such as
  `git -C <root> status --porcelain=v1 -b`, to display branch/dirty state.
- It does not stage, commit, push, reset or clean in v1.
- If Git is absent or the root is not a worktree, the panel says so and the
  rest of code mode continues.

### Tests without arbitrary shell access (proposed A22)

The frozen safety contract prohibits arbitrary shell execution, so F5 does not
expose "run any command" to the model.

- The user defines named **test profiles** as program + argument list + cwd in
  Project Settings (for example `python`, `["-m", "pytest", "-q"]`).
- Only the user can start a profile from the UI. The model may suggest text,
  but cannot invoke or edit a profile.
- Execution uses `CreateProcess`/Qt process APIs with argv (no shell), timeout,
  scrubbed secrets, live output, Cancel, exit code, and audit entry.
- A changed profile requires user save before it can run again.

### Quality gates

Bench/test cases cover exact diff target, checkpoint restore byte identity,
new-file tombstone behaviour, refusal to alter files outside policy, Git
absence, and attempted model invocation of a test profile (must be impossible).

## 5. F15 — Web-page reader and source citations

### Proposed API requiring approval (A23)

The frozen contract has exactly 27 built-in tools and no page-fetch tool.
To make F15 genuinely model-usable, the proposed contract amendment is one
additional built-in:

```text
read_url(url, max_chars=12000)
```

- `url`: absolute HTTP/HTTPS URL;
- `max_chars`: bounded integer in a range approved with other tool caps;
- returns `{url, final_url, title, text, truncated, fetched_at}` after
  extracting main readable content from HTML and plain text;
- PDF URLs are rejected or routed through F4 parsers only after explicit
  design approval; v1 should not pretend all content types work.

Until A23 is approved, the implementable subset is user-invoked URL attach
through the composer, which uses the same fetcher/extractor but is not a model
tool.

### Fetch safety

- HTTP/HTTPS only; no `file:`, custom schemes, embedded credentials, localhost,
  loopback, private, link-local or multicast targets (SSRF guard); recheck
  after redirects and DNS resolution where the platform permits.
- Response body, redirect, decompressed-size, content-type and time caps.
- No cookies, browser session, logged-in scraping or paywall bypass.
- `robots`/site terms are product policy choices to state in README; the reader
  is for a user/model-selected page, not crawling.

### Extraction and citations (proposed A23)

- Use a single pinned readability extractor selected after a small fixture
  bake-off; title/text extraction failures return a clear error rather than
  raw script-heavy HTML.
- `web_search`, `get_news`, `lookup_docs` and `read_url` results receive stable
  per-turn source IDs. Source cards show title, host, URL and fetched time.
- Assistant citations such as `[S3]` link to source cards; only sources that
  were actually returned in this turn can resolve.
- Unresolved or invented citations remain visibly unresolved in UI and count
  as D2 fabricated-provenance defects in benchmarks.

## 6. Milestone placement

| Milestone | Scope |
|---|---|
| M12 | Projects data model/UI, project instructions and extension scoping |
| M13 | Knowledge sources, parsers, FTS5 index, retrieval and local citations |
| M14 | Code workspace mode: tree/preview, checkpoints/undo, Git status, test profiles |
| M15 | `read_url` (if A23), extraction, web source cards and citation linking |
| M16 | Final full test/build/smoke integration |

M3 reserves nullable project fields and work-panel architecture so these
features do not require transcript/history rewrites.

## 7. New approvals added by selection

- **A21** parsers (`pypdf`, `python-docx`), FTS5-first retrieval, chunk/source
  caps and pre-turn retrieval (no embeddings/new model in v1).
- **A22** user-defined argv test profiles + read-only Git status; no model
  shell/test execution and no Git mutations.
- **A23** one additional built-in `read_url(url, max_chars)` plus extractor
  dependency chosen by fixture; otherwise user-only URL attach.
- **A24** project root/instructions behavior above, especially that selecting
  an existing folder does not by itself grant broad write access.
