"""Crash-safe coordination primitives for consequential external actions."""

from .external_action_operation import ExternalActionOperation, ExternalActionStatus
from .external_action_service import (
    AmbiguousExternalActionError,
    ExternalActionAdapter,
    ExternalActionService,
)

__all__ = [
    "AmbiguousExternalActionError",
    "ExternalActionAdapter",
    "ExternalActionOperation",
    "ExternalActionService",
    "ExternalActionStatus",
]
