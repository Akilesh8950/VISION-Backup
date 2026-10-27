"""Three-channel update support for VISION.

The app, model, and remote-configuration channels are intentionally
independent: an app package is staged for the outer Windows launcher, a model
is staged for the next launch, and a validated remote configuration can be
applied to the current process.
"""

from .service import (
    APP_VERSION,
    MANIFEST_URL,
    MODEL_FILE,
    MODEL_REPO,
    UPDATE_CHANNEL,
    UPDATES_ENABLED,
    AppManifest,
    RemoteConfig,
    SemVer,
    UpdateService,
    UpdateState,
)

__all__ = [
    "APP_VERSION",
    "MANIFEST_URL",
    "MODEL_FILE",
    "MODEL_REPO",
    "UPDATES_ENABLED",
    "UPDATE_CHANNEL",
    "AppManifest",
    "RemoteConfig",
    "SemVer",
    "UpdateService",
    "UpdateState",
]
