from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .config import AppConfig
from .connectivity import default_monitor
from .telemetry.needs_log import NeedsLog
from .telemetry.uploader import upload_needs_log
from .updates.service import UpdateCheckResult, UpdateService

logger = logging.getLogger(__name__)

TELEMETRY_SYNC_INTERVAL_SECONDS = 6 * 60 * 60
UPLOAD_TIMEOUT_SECONDS = 60.0

UpdateCallback = Callable[[UpdateCheckResult], None]
TokenProvider = Callable[[], str]
UploadFunction = Callable[..., bool]


class SyncService:
    """One quiet background scheduler for updates and telemetry.

    Launch work (when enabled by the user's preferences):

    * check the App/Model/Config update channels; config may apply live;
    * retry any telemetry left queued by an earlier offline session.

    While running it wakes every six hours to re-check the update channels
    (the model channel is weekly: launch + this timer is its entire
    automatic polling cadence) and retry telemetry upload.  On stop it
    flushes the local queue and, if the user opted in, starts one bounded
    upload attempt.  Network failures are logged at debug level and never
    surfaced as modal/UI errors; unacknowledged rows simply stay local.
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        needs_log: NeedsLog,
        update_service: UpdateService,
        token_provider: TokenProvider,
        on_updates: UpdateCallback | None = None,
        upload_function: UploadFunction = upload_needs_log,
        interval_seconds: float = TELEMETRY_SYNC_INTERVAL_SECONDS,
        upload_timeout_seconds: float = UPLOAD_TIMEOUT_SECONDS,
    ):
        self.config = config
        self.needs_log = needs_log
        self.update_service = update_service
        self.token_provider = token_provider
        self.on_updates = on_updates
        self.upload_function = upload_function
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.upload_timeout_seconds = max(1.0, float(upload_timeout_seconds))

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._upload_guard = threading.Lock()
        self._started = False
        self._stopped = False
        self._close_upload_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        with self._state_lock:
            if self._started:
                return
            self._started = True
            self._stopped = False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="vision-sync-service",
            )
            self._thread.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        """Flush locally and make one close-time upload attempt if opted in.

        The upload itself is internally bounded to 60 seconds.  It is moved
        to a non-daemon helper so window shutdown does not have to wait for
        the network, while the interpreter can still let that one bounded
        attempt finish during final process teardown.
        """
        with self._state_lock:
            if self._stopped:
                return
            self._stopped = True
            scheduler = self._thread
        self._stop_event.set()

        try:
            self.needs_log.flush()
        except Exception:
            logger.debug("Could not flush telemetry during sync shutdown", exc_info=True)

        if bool(getattr(self.config, "telemetry_opt_in", False)):
            close_thread = threading.Thread(
                target=self.flush_and_upload,
                kwargs={"reason": "close"},
                daemon=False,
                name="vision-telemetry-close-upload",
            )
            self._close_upload_thread = close_thread
            close_thread.start()
            if close_thread is not threading.current_thread():
                close_thread.join(timeout=max(0.0, float(join_timeout)))

        if scheduler is not None and scheduler is not threading.current_thread():
            scheduler.join(timeout=1.0)

    # ------------------------------------------------------------------
    # Work items (safe to call directly from tests/worker threads)
    # ------------------------------------------------------------------
    def run_launch_update_check(self) -> UpdateCheckResult | None:
        if not bool(getattr(self.config, "updates_auto_check", True)):
            return None
        try:
            result = self.update_service.check_all(apply_config=True)
        except Exception:
            logger.debug("Scheduled update check failed quietly", exc_info=True)
            return None
        # Keep the shared connectivity state truthful: the check just
        # exercised the network on all three channels.
        try:
            if getattr(result, "offline", False):
                default_monitor().note_failure()
            else:
                default_monitor().note_success()
        except Exception:
            logger.debug("Could not record connectivity from update check", exc_info=True)
        if self.on_updates is not None:
            try:
                self.on_updates(result)
            except Exception:
                logger.debug("Update-status callback failed", exc_info=True)
        return result

    def flush_and_upload(self, *, reason: str = "scheduled", force: bool = False) -> bool:
        try:
            self.needs_log.flush()
        except Exception:
            logger.debug("Could not flush telemetry before %s upload", reason, exc_info=True)
            return False

        if not force and not bool(getattr(self.config, "telemetry_opt_in", False)):
            return False
        if not self._upload_guard.acquire(blocking=False):
            logger.debug("Telemetry upload already running; skipping %s attempt", reason)
            return False
        try:
            try:
                token = str(self.token_provider() or "")
            except Exception:
                logger.debug("Could not read the telemetry upload token", exc_info=True)
                token = ""
            try:
                return bool(
                    self.upload_function(
                        self.needs_log,
                        token,
                        timeout_seconds=self.upload_timeout_seconds,
                    )
                )
            except TypeError:
                # Small test doubles and older callables may not accept the
                # timeout keyword.  Production upload_needs_log does.
                return bool(self.upload_function(self.needs_log, token))
        except Exception:
            logger.debug("Telemetry %s upload failed quietly; queue retained", reason, exc_info=True)
            return False
        finally:
            self._upload_guard.release()

    def request_upload_async(self, *, reason: str = "manual", force: bool = False) -> threading.Thread:
        thread = threading.Thread(
            target=self.flush_and_upload,
            kwargs={"reason": reason, "force": force},
            daemon=True,
            name="vision-telemetry-manual-upload",
        )
        thread.start()
        return thread

    # ------------------------------------------------------------------
    def scheduled_tick(self) -> None:
        """One six-hour scheduler wake: re-check updates, retry upload.

        The model is rebuilt and republished roughly weekly, so this
        timer plus the launch check is deliberately the fastest the app
        ever polls Hugging Face on its own.  A tick after weeks offline
        behaves exactly like a launch check: compare the GGUF repo sha
        once, and flush whatever telemetry queued up meanwhile.
        """

        self.run_launch_update_check()
        self.flush_and_upload(reason="scheduled")

    def _run(self) -> None:
        self.run_launch_update_check()
        # Deliver anything left by an earlier offline/crashed session.  If
        # the user has not opted in, flush_and_upload only writes local files.
        self.flush_and_upload(reason="launch")
        while not self._stop_event.wait(self.interval_seconds):
            self.scheduled_tick()


__all__ = [
    "TELEMETRY_SYNC_INTERVAL_SECONDS",
    "UPLOAD_TIMEOUT_SECONDS",
    "SyncService",
]
