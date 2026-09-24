"""Crash-safe coordination primitives for consequential external actions."""

from .external_action_operation import ExternalActionOperation, ExternalActionStatus
from .external_action_service import ExternalActionAdapter, ExternalActionService

__all__ = [
    "ExternalActionAdapter",
    "ExternalActionOperation",
    "ExternalActionService",
    "ExternalActionStatus",
]
