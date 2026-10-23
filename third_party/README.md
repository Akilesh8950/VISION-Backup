# Optional bundled llama.cpp

For an offline PyInstaller build, extract a supported Windows llama.cpp
release (`>= b11815`) into `third_party/llama.cpp/` so this file exists:

```text
third_party/llama.cpp/llama-server.exe
```

Include the matching DLLs from the same release. For CUDA builds, include the
matching CUDA runtime files as directed by the upstream release. Do not commit
these binaries unless your repository policy explicitly permits it; they are
ignored by `.gitignore`.

If this folder is absent, VISION resolves `llama-server` in this order:
bundled next to the app, Settings path, `%LOCALAPPDATA%\VISION\bin`, then
`PATH`, and the first-run wizard can download a pinned release.
