"""Error hierarchy for pdf-edit-engine."""

from __future__ import annotations


class PDFEditError(Exception):
    """Base exception for all pdf-edit-engine errors."""


class FontNotFoundError(PDFEditError):
    """Font not found in PDF or on system."""


class FontStreamTooLargeError(FontNotFoundError):
    """Embedded font / CMap stream exceeds the decoded-size bound (decompression-bomb guard)."""


class EncodingError(PDFEditError):
    """CMap parse failure or unmappable characters."""


class OperatorError(PDFEditError):
    """Content stream parse/unparse failure or stale operator reference."""


class ReflowError(PDFEditError):
    """Paragraph reflow failure (overflow, etc.)."""
