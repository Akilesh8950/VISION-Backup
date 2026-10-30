# VISION 1.0.0

The first public release of **VISION Desktop** — a private, local-first AI
assistant for Windows. The VISION model runs on your own PC; your chats,
files, voice, and screen frames stay on your machine unless you explicitly
choose an online tool or backup connection.

VISION — built by SPIRIT ORG

## Highlights

- **Local AI chat** with the VISION model (downloaded once on first run),
  streaming answers, optional collapsible reasoning, projects, pinned
  chats, and full-text history search.
- **Real desktop tools, safely confirmed:** files, clipboard, apps, and
  answers rendered as native tables/charts/cards/timelines/diffs,
  web search and page reading with sources, system status, and more —
  risky actions always ask first.
- **Chat and Agent modes:** Agent mode plans multi-step goals and works
  through them with tools while you watch; stop anytime.
- **Skills, MCP servers, and Connectors** with per-tool permissions,
  trust fingerprints, and secrets kept only in Windows Credential Manager.
- **Soothing read-aloud voice** (Listen on any answer) and **Vision Live**:
  strict hold-to-talk voice chat transcribed locally by whisper.cpp, with
  live captions, an optional floating mini window, and one-frame-at-a-time
  screen sharing that always shows a preview for your approval first.
- **Item Library** for reusable files, PDF/DOCX understanding with
  citations, code checkpoints with restore, and per-widget PNG/CSV export.
- **Three update channels** (app, model, config) — the app never updates
  silently; it asks, downloads in the background, and applies on restart
  with rollback protection.
- **Fits any window:** from a 320×360 snapped window through portrait,
  square, short-wide, ultrawide and 4K shapes. Controls and panels resize
  smoothly in place during normal window dragging, with layout changes
  reserved for truly tiny widths. A calm motion system fully respects
  Windows reduced-motion settings and a Low-end CPU mode for small PCs.

## Download and install

- **VISION-Setup-1.0.0-x64.exe** — installer. Choose *Portable folder*
  (everything under one folder you pick) or *Standard Windows data*.
- **VISION-1.0.0-portable-x64.zip** — portable build; unzip and run
  `VISION\VISION.exe`.
- Verify downloads against the `.sha256` files published with this release.
- On first launch the setup wizard downloads the VISION model
  (about 5.24 GiB) and the llama.cpp runtime. After that, local chat and
  Vision Live voice work offline.

## Requirements

- Windows 10 (build 17763+) or Windows 11, 64-bit
- Microsoft Edge WebView2 Runtime (included with Windows 11; the installer
  will tell you if it is missing)
- About 12 GiB free disk space recommended for the app + model + headroom
- A GPU is optional; CPU-only works with the Low-end CPU profile

## Privacy and safety

- No accounts, no analytics, no ads. Feedback upload is opt-in and off by
  default, and you can preview exactly what would be sent.
- Microphone audio and screen frames are never saved or uploaded; the mic
  only listens while you hold push-to-talk.
- File writes outside the workspace, executable launches, and large
  clipboard writes ask for confirmation. Deletion and arbitrary shell
  commands are not available to the model.

## Known limitations

- **Seeing, roadmap:** today's VISION model is text-only, so Vision Live
  screen frames are preview-and-approve only for now. Screen
  understanding turns on automatically when a future VISION model ships
  with its matching vision projector — no screenshot is ever sent to a
  cloud model as a substitute.
- Windows may show a SmartScreen prompt for a new publisher; verify the
  SHA-256 checksum, then choose **More info → Run anyway** if you trust
  the download source (this GitHub release).
- Vision Live needs microphone permission for VISION in Windows Settings
  the first time you hold push-to-talk.
