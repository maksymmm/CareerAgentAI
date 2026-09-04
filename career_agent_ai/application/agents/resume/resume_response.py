from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResumeResponse:
    """
    Immutable Resume Agent response.
    """

    success: bool
    content: str