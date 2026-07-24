"""Workflow execution states."""

from __future__ import annotations

from enum import StrEnum


class WorkflowState(StrEnum):
    """Supported lifecycle states for workflows and workflow steps."""

    PENDING = "Pending"
    RUNNING = "Running"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"