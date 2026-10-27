# VISION — Windows 11 test and smoke instructions

Run this on a real Windows 11 machine before calling a build a release. The
headless/Linux test suite can prove Python logic and local frontend assets,
but it cannot prove WebView2 rendering, Windows native dialogs, pywin32,
Credential Manager, GPU/VRAM behaviour, or structured tool calls from the real
5.24 GiB GGUF.

Record with the results:

- date/time and tester;
- app Git commit;
- Windows edition/build and display scaling;
- Microsoft Edge WebView2 Runtime version (Windows Settings → Apps);
- CPU, RAM, GPU, driver and VRAM;
- llama.cpp build printed by the app;
- GGUF path, byte size and, if practical, SHA-256;
- source launch, packaged `dist\VISION\VISION.exe` launch, and installer
  scope/mode/folder for each installed test;
- every failed step, exact prompt, screenshot and relevant server-log tail.

A step is not “close enough” if it needs an undocumented workaround. Record it
as a defect with the exact error text.

## 1. Clean source install

Use a new folder and a Python 3.11 virtual environment:

```powershell
git clone <repository-url> VISION-smoke
cd VISION-smoke
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
```

Confirm the installed bridge dependency and entry point resolve:

```powershell
python -c "import webview, vision_app; print('pywebview import OK')"
python -c "from vision_app.web_bridge import find_web_dir; print(find_web_dir())"
```

Expected: the second command prints the source tree's `app\web` folder.

## 2. Automated checks

Run all of these from the repository root and save the output:

```powershell
python -m pytest
python -m ruff check src tests
python -m compileall -q src scripts tests
python scripts\run_bench.py --mode replay
```

Expected:

- pytest exits 0;
- Ruff reports no errors;
- compileall exits 0;
- replay bench reports no release-blocking defects.

Node is not required to run the committed React build or create the Windows
executable. If Node 20.19+ (or 22.12+) is installed and you need to
verify/rebuild the frontend from `app\frontend`, run:

```powershell
cd app\frontend
npm ci --legacy-peer-deps
npm run build
cd ..\..
git status --short app\web app\frontend
```

The build must complete without errors and rewrite `app\web` with
`index.html`, one referenced JavaScript bundle, one referenced CSS bundle,
`screenshots.html` and `licenses\`. Confirm there are no `.map`, `.jsx`,
`.ts`, `.tsx` or `package.json` files under `app\web`. Record any unexpected
generated-file diff before testing a release candidate.

## 3. Offline frontend and visual review

### 3.1 Static review captures and React gallery

First inspect the committed PNG review set in `docs\screenshots\`:

- `react-dark-empty.png` and `react-light-empty.png`;
- `react-dark-conversation.png` and `react-light-gallery.png`;
- `react-chart-sources.png`;
- `react-diff.png`;
- `react-settings.png` and `react-server-console.png`.

Then serve the production build as in section 3.2 and open
`http://127.0.0.1:8000/screenshots.html`; it must redirect to
`http://127.0.0.1:8000/index.html?gallery=1`. In the gallery, scroll the
entire demo transcript and review:

1. app frame, 260 px sidebar, custom window controls and floating composer;
2. user bubbles, assistant text, collapsed reasoning and message actions;
3. success tool cards with expandable arguments/results;
4. system-status cards, chart, sortable table, timeline and side-by-side
   diff — all five generative widget kinds;
5. source citation chips and the Sources inspector;
6. Settings, Server console and Activity sheets from the sidebar;
7. dark/light themes and the collapsed 64 px sidebar.

Do not judge the React production build by double-clicking
`app\web\index.html` from Explorer: Chromium/WebView module handling is
tested through the local HTTP server and, most importantly, through the real
pywebview launch in section 4. Tab through the gallery and confirm a visible
2 px accent focus ring, including on table sort buttons.

### 3.2 Production bundle over HTTP

In a separate PowerShell window:

```powershell
cd app\web
python -m http.server 8000 --bind 127.0.0.1
```

Open these local URLs:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/?gallery=1`
- `http://127.0.0.1:8000/screenshots.html`

The first page will say Preview mode because the pywebview bridge is absent
in a normal browser. That is expected, and its simulated responses are not
evidence of real model/tool behavior. Open DevTools (F12) → Network, reload,
and confirm:

- `index.html`, the initial relative `./assets/*.js` and `./assets/*.css`
  files, plus lazy relative JavaScript chunks when charts/widgets or the
  Item Library first open, are the frontend requests;
- all frontend requests are `127.0.0.1`;
- there are no CDN or other external script/style/font/image requests;
- there are no missing-asset 404s;
- the console has no React, Chart.js or module-loading error.

Stop the HTTP server with Ctrl+C.

### 3.3 Offline relaunch

After the model and llama-server are downloaded in section 4:

1. disconnect Wi-Fi/Ethernet;
2. close and relaunch VISION;
3. confirm the full interface renders;
4. ask for system status and a chart from values in the prompt;
5. reconnect the network.

Expected: the UI, local history, tools that do not need the internet, and
Chart.js widgets still work. Live-data tools may report network errors.

## 4. First run and local server

Use an isolated data root for a true first-run test:

```powershell
$env:VISION_HOME = "$PWD\.smoke-data"
python -m vision_app --debug
```

1. Confirm the frameless window opens at a usable size and can be dragged by
   the titlebar, minimized, maximized/restored and closed with the custom
   window controls.
2. Confirm the setup sheet appears.
3. Click **Download model**. Confirm progress updates, the app stays
   responsive, and the finished GGUF is 5,629,108,640 bytes.
4. Click **Download llama-server**. Confirm the detected build is at least
   b11815 and marked supported.
5. Set GPU layers. Start with 99 only if VRAM is sufficient; otherwise record
   the value that works.
6. Click **Start / health check** or **Finish**. Confirm the titlebar/server
   status reaches Healthy with no UI freeze.
7. Open **Server** and verify the command shape ends with:

   ```text
   --jinja --flash-attn on
   ```

8. Confirm logs are visible, and test Start/Stop/Restart.

If the window is blank, record the console output, verify the WebView2
Runtime installation, and try the rollback only as a diagnostic:

```powershell
python -m vision_app --qt
```

## 5. Core chat and streaming

Run each prompt in a new or clearly named chat.

1. `Explain VISION in two short paragraphs.`
   - Tokens stream progressively.
   - The streaming cursor pulses without the whole transcript jumping.
   - Scroll up during generation: a **Jump to latest** pill appears; click it.
   - Press Esc or the Stop button during a long answer. The partial answer
     remains and is marked incomplete.
2. Ask a question that produces reasoning. Confirm `<think>` is hidden behind
   a muted Reasoning chip, expands/collapses smoothly, and is collapsed by
   default after reopening the conversation.
3. Ask for a Python code block. Confirm language label, horizontal scroll,
   no page widening, and the copy button copies only the code.
4. Use the message actions: copy, regenerate, thumbs-down and Correct this.
   Also test Ctrl+Enter for regenerate.
5. Type a long composer message. Confirm it grows to eight lines, then scrolls
   internally; Enter sends and Shift+Enter inserts a line.
6. Press Ctrl+K. Run commands for New chat, Settings, Server console, theme,
   sidebar and an existing chat title.
7. Click the VISION logo to collapse the sidebar. Confirm the expanded width
   is 260 px, the rail is 64 px, the transition is smooth (200 ms), icons
   remain usable, Search expands and focuses the field, and the state is
   remembered after relaunch.
8. Confirm a completed first exchange replaces **New chat** with a useful
   3–5 word title. Double-click the title to rename it inline. From the row
   `⋯` menu, pin/unpin it, move it to a Project and back to No project, and
   delete a disposable copy. Confirm pinned chats stay first and pinned
   deletion asks **Unpin & delete**. Filter by All projects, No project and
   the selected Project; collapse/expand the Project group and relaunch to
   confirm both choices persist.
9. Attach a small `.txt` file with the composer's **+ / Attach text files**
   button and ask a question about it. Confirm the native picker opens, the
   chip can be removed before sending, and the file appears in **Item
   Library**. Send one message with it and confirm its used-in-chats count
   increases. Upload a small image and a file larger than 256 KB: both should
   be saved to the library with a thumbnail/type icon and size, neither should
   be attached, and the image's attach action should explain the text-only
   rule. Rename and delete disposable library items. Open **Settings →
   Library** and confirm the same items and actions are present there.

## 6. Frozen tool-call and widget checks

### 6.1 Structured tool-call falsification check

Ask:

```text
What's the weather in Coimbatore?
```

Confirm:

- a `get_weather` tool card appears;
- arguments are shown as one-line JSON and expand to the full result;
- a follow-up answer streams;
- no raw ChatML/XML tool-call text appears as assistant content.

If raw tool syntax appears as content, stop the release test and save the
exact server/SSE evidence. Do not work around it in the UI.

### 6.2 Real suggestion chips and tools

From an empty chat, click each suggestion in separate chats:

- Weather in Coimbatore → `get_weather`;
- System status → `system_status` tool card plus CPU/RAM/disk/GPU cards;
- Draw a chart → `ui_chart` with values 1, 4, 9, 16;
- Open an app → `open_app` for Notepad.

Ask one combined prompt such as “Show system status and Coimbatore weather.”
Confirm parallel tool cards stack neatly and finish independently.

### 6.3 Every generative widget

Ask the model to create, or use prompts that trigger:

1. `ui_table`: sort each column; export PNG and CSV; open both files.
2. `ui_chart`: test bar and, where the model selects them, line/pie/scatter;
   export PNG and CSV.
3. `ui_cards`: export PNG and CSV.
4. `ui_timeline`: export PNG and CSV.
5. `ui_code_diff`: confirm green/red gutters and both line-number columns;
   export PNG and CSV.

Repeat one chart/table test in light theme and with the window narrowed.

## 7. Sources and local knowledge

1. Ask a current-events or documentation question that uses web tools.
   Confirm `[S#]` chips appear in the answer.
2. Click a citation and the message's Sources button. Confirm the right-hand
   inspector highlights the correct source and the external-open button uses
   the system browser.
3. Use the legacy surface or existing data to create/index a Project source if
   needed, return to the React UI, select the Project, and ask a question whose
   answer is only in that source. Confirm `[L#]`, locator text and opening the
   local source.
4. Read a public URL with `read_url`; verify its source card. Confirm a
   loopback/private-network URL is blocked by policy.

## 8. Windows tools and safety

Test with disposable files only:

1. `list_running_apps`, open Notepad, focus it by title substring, open a URL;
2. clipboard set/get with a short value;
3. clipboard set with more than 2,000 characters and confirm approval;
4. set a 0.1-minute timer and confirm the toast/banner fires;
5. remember a harmless preference, restart, and ask for it back;
6. run approved Python `print(6*7)`; deny a second run and confirm the chat
   remains usable;
7. attempt an outside-workspace write, a `..` path and opening an executable.
   Deny each confirmation once and approve only a disposable safe case.
   Confirm absolute paths are shown before writes;
8. attempt a system-path write and confirm it is blocked;
9. ask the model to delete a file, kill a process, edit the registry or run a
   shell command. There must be no such tools; blocked/hallucinated calls
   must return `Blocked by VISION safety policy` without side effects.

## 9. Settings, endpoint failover and updates

1. Open every Settings tab: General, Model, Tools, Library, Privacy and About.
2. Change theme and confirm it persists after relaunch.
3. Change a restart-relevant server value, save, and confirm the restart
   notice. Restart from Server console and verify health.
4. Enter and clear dummy remote/HF secrets. Confirm the fields never redisplay
   secret values, config JSON contains no secrets, and Windows Credential
   Manager is the store on Windows.
5. Configure a real OpenAI-compatible remote `/v1`. In Auto mode, stop local
   health, wait for two failed checks, and send a message. Confirm a visible
   CLOUD chip/banner only after remote health succeeds; repeat in Local mode
   and confirm no silent cloud use.
6. Check for a model update. If one is downloaded, confirm it is marked for
   next launch and does not swap mid-conversation.
7. Kill only the VISION-owned `llama-server` child in Task Manager during a
   chat. Confirm health state, at most one automatic restart/banner according
   to policy, no history loss and no dead UI. Never kill a foreign process on
   port 8080.

## 10. Tools (Skills, MCP and Connectors)

1. Import the same harmless sample skill four ways in clean data roots:
   folder, lone `SKILL.md`, `.zip`, and ZIP-based `.skill`. Confirm import
   executes no script; enable it, save Settings, and activate it with
   `/skill-name`.
2. Configure one harmless local stdio MCP server and, if available, one
   Streamable HTTP MCP server. Use the legacy `--qt` surface or the documented
   JSON config for record editing, then return to the web Settings →
   Tools.
3. Click **Discover / trust**. Review every tool name/schema and fingerprint;
   deny first and confirm no dynamic tools appear. Discover again, trust,
   refresh, deny one tool call, then allow one harmless call.
4. Change a tool description/schema upstream and refresh. Confirm the old
   fingerprint is not silently accepted and re-approval is required.
5. Test a malicious local MCP description/result that asks for clipboard or
   token access. Any action without explicit user consent is a release
   blocker.

## 11. Feedback and audit

1. Trigger thumbs-down, `/fix`, `/teach`, a schema error and a Python error.
2. Open **Activity & feedback**. Inspect audit rows and the Feedback queue.
3. Open `%LOCALAPPDATA%\VISION\telemetry\needs.jsonl` (or `.smoke-data` when
   `VISION_HOME` is set). Confirm every row has exactly
   `{ts, category, prompt, model_output, signal, detail}` and no secrets,
   file contents or clipboard contents.
4. Export as `feedback.jsonl` through the native save dialog.
5. Only with a disposable/write-authorized test token and explicit opt-in,
   upload. Confirm a new timestamped shard appears under `telemetry/inbox/` in the
dataset and local rows clear after success. Repeat
   with an invalid token and confirm local rows remain.

## 12. Reduced motion, scaling and keyboard

1. Enable Windows reduced-motion/animation-off settings or the browser's
   reduced-motion emulation in debug tooling. Confirm content is readable and
   no essential state depends on animation.
2. Test 100%, 125% and 150% display scaling; resize from minimum size to full
   screen. Confirm no clipped composer controls, titlebar buttons or sheets.
3. Navigate with Tab/Shift+Tab only. Confirm focus is always visible and the
   composer, sidebar, tool cards, sheets and confirmation buttons are
   reachable. Esc closes the top sheet/palette/inspector before it stops a
   stream.

## 13. Packaged build

`build.ps1` packages the committed `app\web` React output and does not invoke
Node. If `app\frontend` changed since the last frontend build, first complete
the optional rebuild in section 2. Then run:

```powershell
.\scripts\build.ps1
Test-Path dist\VISION\VISION.exe
Test-Path dist\VISION\_internal\app\web\index.html
```

If the second path differs, locate it with:

```powershell
Get-ChildItem dist\VISION -Recurse -Filter index.html |
  Where-Object { $_.FullName -match 'app\\web' }
```

Then:

1. move/rename the source checkout or launch from a different directory;
2. launch `dist\VISION\VISION.exe`;
3. repeat sections 4–6 core checks;
4. verify About shows version/build and `VISION — built by SPIRIT ORG`;
5. launch the packaged EXE with `--qt` once to confirm rollback still opens;
6. test the browser profile under `%LOCALAPPDATA%\VISION\webview` persists
   theme/window-independent web storage without writing secrets there.

Do not replace the source-launch result with the packaged result or vice
versa; record both.

## 14. Installer and portable build

Install Inno Setup 6 and build the distributable setup plus portable ZIP:

```powershell
winget install --id JRSoftware.InnoSetup -e -s winget
.\scripts\build_installer.ps1 -PortableZip
Get-FileHash dist\installer\VISION-Setup-*-x64.exe -Algorithm SHA256
```

Verify `dist\installer\` contains the setup EXE, its `.sha256`, the portable
ZIP and its `.sha256`.

### 14.1 Current-user portable install

Use a test folder on a non-system drive if available, otherwise a writable
profile folder:

1. Run the installer and choose **Install for current user only**.
2. Confirm **Portable folder (recommended)** is enabled and selected.
3. Browse to a new folder such as `D:\VISION-smoke\VISION`.
4. Enable shortcuts, install, and launch from the Finish page.
5. Confirm the folder contains the outer launcher `VISION.exe`,
   `VISION.portable`, launcher `_internal\`, `current.json`, `downloads\`,
   `versions\<version>\VISION.exe`, `data\` and `workspace\`.
6. Complete first-run downloads and confirm the GGUF lands in
   `<install>\data\models\`, llama.cpp under `<install>\data\bin\`, and the
   WebView profile under `<install>\data\webview\`.
7. In **Settings → Privacy**, confirm Installation mode says
   **Portable folder** and the App data/Workspace paths are under the chosen
   folder.
8. Close VISION, copy the entire folder to a second test path, launch from the
   copy, and confirm history/settings are present. Record that Windows
   Credential Manager secrets are intentionally not inside the copied folder.
9. Uninstall from Windows Settings and confirm program files/shortcuts are
   removed while `data\` and `workspace\` remain. Manually delete the test
   folder only after recording that preservation behaviour.

### 14.2 All-users standard install

On a VM or disposable test account:

1. Run the installer and choose **Install for all users**.
2. Confirm the administrator prompt appears, Portable is disabled, and
   Standard Windows data is selected.
3. Install to Program Files and launch as the original non-elevated user.
4. Confirm there is no `VISION.portable` beside `VISION.exe`.
5. Confirm Settings shows **Standard Windows data**, App data under
   `%LOCALAPPDATA%\VISION`, workspace under `%USERPROFILE%\VISION\workspace`,
   and WebView profile under `%LOCALAPPDATA%\VISION\webview`.
6. Uninstall and record that standard user data remains until manually
   deleted.

### 14.3 Portable ZIP

Extract `VISION-*-portable-x64.zip` to a clean test folder, confirm
`VISION.portable`, `data\` and `workspace\` exist beside `VISION.exe`, then
launch and repeat the Settings path checks from 14.

### 14.4 Three-channel update handoff

For a release candidate, use a disposable launcher-managed test install:

1. Open **Settings → Updates** and confirm the App, Model, and Config rows show
   installed/available versions, last-checked time, and status pills without
   exposing update URLs or secrets.
2. Run **Check now** per channel with the network disconnected; VISION should
   keep last-known status with no modal crash.
3. Stage a test app update from the local proof manifest, confirm the running
   app writes only `downloads\`, `versions\<new>\`, and `pending.json`, then
   click **Restart to apply** and confirm the outer launcher—not the running
   app—rewrites `current.json` and relaunches the new version.
4. Force the staged test version to exit non-zero twice within 60 seconds and
   confirm the launcher rolls back to the previous version and shows one
   notice.
5. Download a model update while a conversation exists and confirm it remains
   pending until the next launch; apply a malformed remote config and confirm
   the bundled/cached prompt and tool schemas stay active.

## 15. Minimum pass gate

Do not call the Windows build release-ready unless all are true:

- section 2 exits cleanly;
- first run downloads/verifies model and supported llama.cpp;
- `--flash-attn on` server reaches Healthy;
- structured `get_weather` tool call passes with no raw tool syntax leak;
- offline relaunch and local React bundle/no-CDN checks pass;
- all five widgets render and export PNG/CSV;
- safety confirmations/blocks pass;
- packaged EXE launches outside the repo with bundled `app\web`;
- current-user portable and all-users standard installer flows in section 14
  select the correct folders, preserve data correctly on uninstall, and show
  the correct mode/paths in Settings;
- the section 14.4 launcher staging, restart handoff, crash-loop rollback,
  model-next-launch, and malformed-config checks pass;
- no crash, secret leak, unconsented extension action or history loss occurs.
