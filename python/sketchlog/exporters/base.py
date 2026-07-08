"""Base classes and shared exceptions for SketchLog exporters."""
from __future__ import annotations


class ExporterError(Exception):
    """Raised when an exporter cannot deliver data to the target system.

    Attributes:
        status_code: The HTTP status code returned by the remote endpoint,
            or ``None`` for non-HTTP errors (e.g. timeout, network error).
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
