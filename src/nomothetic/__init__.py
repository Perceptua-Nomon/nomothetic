"""Package initialization for nomothetic."""

import importlib.metadata as _meta

try:
    __version__ = _meta.version("nomothetic")
except _meta.PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
__author__ = "Perceptua"

from .camera import Camera

try:
    from .streaming import StreamServer
    from .telemetry import TelemetryPublisher

    __all__ = ["Camera", "StreamServer", "TelemetryPublisher"]
except ImportError:
    try:
        from .streaming import StreamServer

        __all__ = ["Camera", "StreamServer"]
    except ImportError:
        try:
            from .telemetry import TelemetryPublisher

            __all__ = ["Camera", "TelemetryPublisher"]
        except ImportError:
            __all__ = ["Camera"]
