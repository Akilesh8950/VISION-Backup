#!/usr/bin/env python3
"""Repeatable UI performance benchmark for the built React shell.

Serves app/web locally, drives headless Chrome over CDP, and reports:
  * production bundle sizes (raw + gzip),
  * load metrics (layout/style counts, script/task time, JS heap, DOM nodes),
  * sidebar collapse/expand frame timings,
  * long-transcript scroll frame timings (synthetic ~180 message rows),
  * Settings open latency.

Usage:
    CHROME_BIN=/path/to/chrome python3 scripts/bench_ui.py [--json out.json]

Requires the `websocket-client` package and a Chrome/Chromium binary.
Frame numbers are from a headless software-rendered browser: use them to
compare before/after on the same machine, not as absolute product claims.
"""

from __future__ import annotations

import argparse
import functools
import gzip
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "app" / "web"


def find_chrome() -> str | None:
    candidates = [
        os.environ.get("CHROME_BIN", ""),
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        str(Path.home() / ".local/share/choreographer/deps/chrome-linux64/chrome"),
        "/tmp/browsers/chrome/linux-156.0.8078.47/chrome-linux64/chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def bundle_sizes() -> dict[str, dict[str, int]]:
    sizes: dict[str, dict[str, int]] = {}
    assets = WEB / "assets"
    for path in sorted(assets.glob("*")):
        data = path.read_bytes()
        sizes[path.name] = {"bytes": len(data), "gzip": len(gzip.compress(data))}
    return sizes


FRAME_SAMPLER = """
window.__sampleFrames = (ms) => new Promise((resolve) => {
  const deltas = [];
  let last = performance.now();
  const start = last;
  function frame(t) {
    deltas.push(t - last);
    last = t;
    if (t - start < ms) requestAnimationFrame(frame);
    else {
      const sorted = [...deltas].sort((a, b) => a - b);
      const pick = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))] || 0;
      const avg = deltas.reduce((a, b) => a + b, 0) / Math.max(1, deltas.length);
      resolve({
        frames: deltas.length,
        avg_ms: Math.round(avg * 10) / 10,
        p50_ms: Math.round(pick(0.5) * 10) / 10,
        p95_ms: Math.round(pick(0.95) * 10) / 10,
        max_ms: Math.round((sorted[sorted.length - 1] || 0) * 10) / 10,
        dropped_over_34ms: deltas.filter((d) => d > 34).length,
        fps: Math.round(1000 / Math.max(1, avg)),
      });
    }
  }
  requestAnimationFrame(frame);
});
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", dest="out", default="", help="also write results JSON here")
    parser.add_argument(
        "--cpu-throttle", type=float, default=1.0,
        help="CPU throttling rate (e.g. 4 = 4x slower, approximates a low-end PC)",
    )
    args = parser.parse_args()

    chrome = find_chrome()
    if chrome is None:
        print("No Chrome/Chromium found. Set CHROME_BIN.", file=sys.stderr)
        return 2
    try:
        import websocket
    except ImportError:
        print("websocket-client is required: pip install websocket-client", file=sys.stderr)
        return 2
    if not (WEB / "index.html").exists():
        print("app/web/index.html missing — run the frontend build first.", file=sys.stderr)
        return 2

    results: dict[str, object] = {"bundle": bundle_sizes()}

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = int(httpd.server_address[1])
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    profile = Path("/tmp/vision-bench-profile")
    shutil.rmtree(profile, ignore_errors=True)
    env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS="/dev/null")
    proc = subprocess.Popen(
        [
            chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--remote-debugging-port=0",
            "--remote-allow-origins=*", f"--user-data-dir={profile}",
            "--window-size=1440,900", "about:blank",
        ],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ws = None
    try:
        port_file = profile / "DevToolsActivePort"
        ws_url = None
        deadline = time.time() + 30
        while time.time() < deadline and not ws_url:
            try:
                debug_port = int(port_file.read_text().splitlines()[0])
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{debug_port}/json/list", timeout=5
                ) as response:
                    targets = json.load(response)
                page = next((t for t in targets if t.get("type") == "page"), None)
                if page:
                    ws_url = page["webSocketDebuggerUrl"]
            except Exception:
                time.sleep(0.25)
        if not ws_url:
            print("Chrome DevTools endpoint never came up.", file=sys.stderr)
            return 1

        ws = websocket.create_connection(ws_url, timeout=60)
        seq = 0

        def send(method: str, params: dict | None = None) -> dict:
            nonlocal seq
            seq += 1
            ident = seq
            ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == ident:
                    if "error" in msg:
                        raise RuntimeError(msg["error"])
                    return msg.get("result", {})

        def ev(expression: str, await_promise: bool = False):
            res = send(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            )
            obj = res.get("result", {})
            if obj.get("subtype") == "error":
                raise RuntimeError(obj.get("description"))
            return obj.get("value")

        def wait_for(expression: str, timeout: float = 25) -> bool:
            end = time.time() + timeout
            while time.time() < end:
                try:
                    if ev(expression):
                        return True
                except Exception:
                    pass
                time.sleep(0.25)
            return False

        send("Page.enable")
        send("Runtime.enable")
        send("Performance.enable")
        send(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False},
        )
        if args.cpu_throttle and args.cpu_throttle != 1.0:
            send("Emulation.setCPUThrottlingRate", {"rate": args.cpu_throttle})
            results["cpu_throttle"] = args.cpu_throttle
        url = f"http://127.0.0.1:{port}/index.html?gallery=1"
        send("Page.navigate", {"url": url})
        if not wait_for("Boolean(document.querySelector('.sidebar')) && document.body.innerText.includes('VISION')"):
            print("App did not mount.", file=sys.stderr)
            return 1
        time.sleep(1.2)
        ev(FRAME_SAMPLER)

        metrics = {m["name"]: m["value"] for m in send("Performance.getMetrics").get("metrics", [])}
        results["load"] = {
            "LayoutCount": metrics.get("LayoutCount"),
            "RecalcStyleCount": metrics.get("RecalcStyleCount"),
            "LayoutDuration_s": round(metrics.get("LayoutDuration", 0), 4),
            "RecalcStyleDuration_s": round(metrics.get("RecalcStyleDuration", 0), 4),
            "ScriptDuration_s": round(metrics.get("ScriptDuration", 0), 4),
            "TaskDuration_s": round(metrics.get("TaskDuration", 0), 4),
            "JSHeapUsedSize": metrics.get("JSHeapUsedSize"),
            "Nodes": metrics.get("Nodes"),
            "Documents": metrics.get("Documents"),
        }

        # Settings open latency (click -> sheet in DOM and painted).
        results["settings_open_ms"] = ev(
            """new Promise((resolve) => {
              const t0 = performance.now();
              document.querySelector('.settings-footer-btn')?.click();
              (function check() {
                if (document.querySelector('.settings-sheet')) {
                  requestAnimationFrame(() => resolve(Math.round(performance.now() - t0)));
                } else requestAnimationFrame(check);
              })();
            })""",
            await_promise=True,
        )
        ev("document.querySelector('.settings-head .icon-btn')?.click()")
        time.sleep(0.5)

        # Sidebar collapse + expand frame timings. Start the sampler first so
        # the whole 200ms+ transition is covered.
        for label in ("sidebar_collapse", "sidebar_expand"):
            ev("window.__f = window.__sampleFrames(750)")
            ev("document.querySelector('.logo-toggle, .title-logo-toggle')?.click()")
            results[label] = json.loads(ev("window.__f.then((r) => JSON.stringify(r))", await_promise=True))
            time.sleep(0.4)

        # Synthetic long transcript: clone the gallery rows to ~180 rows,
        # then auto-scroll top -> bottom while sampling frames.
        rows = ev(
            """(() => {
              const reading = document.querySelector('.reading');
              const base = [...reading.querySelectorAll('.msg-row')];
              if (!base.length) return 0;
              const frag = document.createDocumentFragment();
              for (let i = 0; i < 60; i++) base.forEach((r) => frag.appendChild(r.cloneNode(true)));
              reading.appendChild(frag);
              // React adds .many past 24 messages; mirror that real state.
              reading.classList.add('many');
              return reading.querySelectorAll('.msg-row').length;
            })()"""
        )
        time.sleep(0.8)
        ev("window.__f = window.__sampleFrames(2000)")
        ev(
            """(() => {
              const el = document.querySelector('.transcript');
              el.scrollTop = 0;
              const start = performance.now();
              const max = el.scrollHeight - el.clientHeight;
              (function step(t) {
                const p = Math.min(1, (performance.now() - start) / 1800);
                el.scrollTop = max * p;
                if (p < 1) requestAnimationFrame(step);
              })();
            })()"""
        )
        results["long_transcript_scroll"] = {
            "rows": rows,
            **json.loads(ev("window.__f.then((r) => JSON.stringify(r))", await_promise=True)),
        }
        metrics = {m["name"]: m["value"] for m in send("Performance.getMetrics").get("metrics", [])}
        results["after_scroll"] = {
            "LayoutCount": metrics.get("LayoutCount"),
            "RecalcStyleCount": metrics.get("RecalcStyleCount"),
            "LayoutDuration_s": round(metrics.get("LayoutDuration", 0), 4),
            "RecalcStyleDuration_s": round(metrics.get("RecalcStyleDuration", 0), 4),
            "Nodes": metrics.get("Nodes"),
        }
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        httpd.shutdown()

    print(json.dumps(results, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
