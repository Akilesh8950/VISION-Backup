"""Internet connectivity awareness and download reconnect logic.

One shared monitor answers three questions for the whole app:

* downloads: "the connection just dropped - wait, then resume"
  (:class:`ReconnectWaiter` drives the retry loop around resumable
  transfers so a lost connection pauses a 5.6 GB model download
  instead of failing it);
* the model: "am I online?" is injected into the chat system prompt
  so it does not call web tools that cannot work, and says so;
* web tools: fail fast with a clear message when known offline
  (:func:`internet_tool`).

Probing only ever contacts hosts the app already uses for its own
updates (Hugging Face, GitHub raw) - no third-party beacons.  Probes
are cached for a few seconds and deduplicated across threads.
"""

from __future__ import annotations

import functools
import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

logger = logging.getLogger(__name__)

PROBE_URLS = ("https://huggingface.co", "https://raw.githubusercontent.com")
NO_INTERNET_MESSAGE = "No internet connection is available right now."

_NETWORK_MESSAGE_HINTS = (
    "connect",
    "timeout",
    "timed out",
    "network",
    "unreachable",
    "name or service not known",
    "nodename",
    "getaddrinfo",
    "temporary failure in name resolution",
    "connection reset",
    "connection aborted",
    "broken pipe",
    "server disconnected",
    "remote protocol",
    "eof occurred",
    "offline",
)
_NETWORK_ERRNOS = {101, 103, 104, 110, 111, 113}  # ENETUNREACH..EHOSTUNREACH (POSIX)
_NETWORK_WINERRORS = {10053, 10054, 10060, 10061, 10065, 11001}


def probe_internet(timeout: float = 3.0) -> bool:
    """True when any known update host answers at HTTP level.

    Any HTTP response - even a 404 - proves reachability; only
    connection-level failures count as offline.
    """

    for url in PROBE_URLS:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                client.head(url)
            return True
        except Exception:
            continue
    return False


def is_network_error(exc: BaseException) -> bool:
    """Classify an exception as connectivity-related (retryable later).

    Deliberately conservative about "not found" failures: a missing
    repo/file will still be missing after reconnecting, so those must
    surface immediately instead of being retried.
    """

    name = type(exc).__name__
    if "NotFound" in name:
        return False
    message = str(exc).lower()
    if "404" in message and ("not found" in message or "entry not found" in message):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror)):
        return True
    if isinstance(exc, httpx.HTTPError):
        return True
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) in _NETWORK_ERRNOS:
            return True
        if getattr(exc, "winerror", None) in _NETWORK_WINERRORS:
            return True
    lowered_name = name.lower()
    if any(hint in lowered_name for hint in ("connect", "timeout", "network", "protocol")):
        return True
    return any(hint in message for hint in _NETWORK_MESSAGE_HINTS)


class ConnectivityMonitor:
    """Cached, thread-safe online/offline state."""

    def __init__(
        self,
        probe: Callable[[], bool] = probe_internet,
        *,
        fresh_seconds: float = 20.0,
    ) -> None:
        self._probe = probe
        self._fresh_seconds = max(0.0, float(fresh_seconds))
        self._lock = threading.Lock()
        self._online: bool | None = None
        self._checked_at = 0.0
        self._probing = False

    # -- state ---------------------------------------------------------
    def cached_online(self) -> bool | None:
        """Last known state without probing (None = never checked)."""

        with self._lock:
            return self._online

    def note_success(self) -> None:
        with self._lock:
            self._online = True
            self._checked_at = time.monotonic()

    def note_failure(self) -> None:
        with self._lock:
            self._online = False
            self._checked_at = time.monotonic()

    def _is_fresh_locked(self) -> bool:
        return self._online is not None and (time.monotonic() - self._checked_at) < self._fresh_seconds

    # -- probing ---------------------------------------------------------
    def is_online(self, *, force: bool = False) -> bool:
        """Current connectivity; probes (blocking) when the cache is stale.

        ``force=True`` always probes.  Download retry loops use it right
        after a transfer failure, where deciding "server trouble" vs
        "connection lost" from a seconds-old cached answer would pick
        the wrong recovery strategy.
        """

        with self._lock:
            if not force and self._is_fresh_locked():
                return bool(self._online)
            if self._probing and self._online is not None:
                return bool(self._online)
            self._probing = True
        try:
            result = bool(self._probe())
        except Exception:
            result = False
        with self._lock:
            self._online = result
            self._checked_at = time.monotonic()
            self._probing = False
        return result

    def refresh_async(self) -> None:
        """Probe on a daemon thread when stale (chat path must not block)."""

        with self._lock:
            if self._is_fresh_locked() or self._probing:
                return
            self._probing = True

        def run() -> None:
            try:
                result = bool(self._probe())
            except Exception:
                result = False
            with self._lock:
                self._online = result
                self._checked_at = time.monotonic()
                self._probing = False

        threading.Thread(target=run, daemon=True, name="vision-connectivity").start()

    def wait_until_online(self, *, timeout: float | None = None, poll_seconds: float = 5.0) -> bool:
        """Block until a probe succeeds or the timeout expires."""

        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        while True:
            # Force a real probe each round: the cached "offline" from
            # the failure that brought us here must not short-circuit.
            with self._lock:
                self._checked_at = 0.0
            if self.is_online():
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            wait = max(0.2, float(poll_seconds))
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic()))
            if wait > 0:
                time.sleep(wait)


_default_monitor: ConnectivityMonitor | None = None
_default_lock = threading.Lock()


def default_monitor() -> ConnectivityMonitor:
    """The process-wide shared monitor (one connectivity truth)."""

    global _default_monitor
    with _default_lock:
        if _default_monitor is None:
            _default_monitor = ConnectivityMonitor()
        return _default_monitor


class ReconnectWaiter:
    """Retry bookkeeping for resumable downloads.

    Two distinct failure modes get two distinct budgets:

    * still online but the transfer keeps failing (server trouble):
      a few attempts with exponential backoff, then give up loudly;
    * offline: wait for connectivity to return, up to a generous
      total budget, then give up - the caller's partial file stays on
      disk, so a later manual retry resumes where this stopped.
    """

    def __init__(
        self,
        monitor: ConnectivityMonitor | None = None,
        *,
        max_offline_wait_seconds: float = 2 * 3600.0,
        max_online_attempts: int = 5,
        base_delay_seconds: float = 2.0,
        poll_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.monitor = monitor or default_monitor()
        self.max_offline_wait_seconds = max(0.0, float(max_offline_wait_seconds))
        self.max_online_attempts = max(1, int(max_online_attempts))
        self.base_delay_seconds = max(0.0, float(base_delay_seconds))
        self.poll_seconds = max(0.2, float(poll_seconds))
        self._sleep = sleep
        self._online_failures = 0
        self._offline_spent = 0.0

    def wait_after_failure(
        self,
        *,
        on_waiting: Callable[[], None] | None = None,
        on_resumed: Callable[[], None] | None = None,
    ) -> bool:
        """Wait appropriately after a network failure. True = retry now."""

        if self.monitor.is_online(force=True):
            self._online_failures += 1
            if self._online_failures >= self.max_online_attempts:
                return False
            delay = min(60.0, self.base_delay_seconds * (2 ** (self._online_failures - 1)))
            if delay > 0:
                self._sleep(delay)
            return True

        # Offline: this is the mid-download connection-loss case.
        remaining = self.max_offline_wait_seconds - self._offline_spent
        if remaining <= 0:
            return False
        if on_waiting is not None:
            on_waiting()
        started = time.monotonic()
        online = self.monitor.wait_until_online(timeout=remaining, poll_seconds=self.poll_seconds)
        self._offline_spent += time.monotonic() - started
        if not online:
            return False
        self._online_failures = 0
        if on_resumed is not None:
            on_resumed()
        return True


F = TypeVar("F", bound=Callable[..., Any])


def internet_tool(fn: F) -> F:
    """Guard for web-backed tools: fast-fail offline, learn from errors."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        monitor = default_monitor()
        if monitor.cached_online() is False:
            raise RuntimeError(f"{NO_INTERNET_MESSAGE} The tool was not run.")
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            if is_network_error(exc):
                monitor.note_failure()
                raise RuntimeError(NO_INTERNET_MESSAGE) from exc
            raise
        monitor.note_success()
        return result

    return wrapper  # type: ignore[return-value]
