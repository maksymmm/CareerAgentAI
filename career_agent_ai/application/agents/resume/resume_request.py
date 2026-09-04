from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResumeRequest:
    """
    Immutable Resume Agent request.
    """

    text: str