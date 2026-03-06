from __future__ import annotations


def _escape_delimiters(text: str) -> str:
    """Escape untrusted_data XML tags to prevent delimiter injection."""
    return text.replace("<untrusted_data", "&lt;untrusted_data").replace(
        "</untrusted_data", "&lt;/untrusted_data"
    )
