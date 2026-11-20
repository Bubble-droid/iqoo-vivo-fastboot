"""Exception types raised by the converter."""

from __future__ import annotations


class ToolError(RuntimeError):
    """A recoverable build error that the CLI reports without a traceback."""
