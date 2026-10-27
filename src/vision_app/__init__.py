"""VISION desktop application package."""

__version__ = "1.0.0"
try:
    from ._build_info import BUILD_COMMIT  # type: ignore
except Exception:  # pragma: no cover - generated only during packaging
    BUILD_COMMIT = "dev"

__all__ = ["BUILD_COMMIT", "__version__"]
