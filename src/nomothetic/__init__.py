"""Package initialization for nomothetic."""

__version__ = "0.1.0"
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
