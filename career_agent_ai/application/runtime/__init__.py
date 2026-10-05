"""Production runtime configuration and lazily loaded composition primitives."""

from typing import TYPE_CHECKING, Any

from .config import RuntimeConfig, RuntimeEnvironment

if TYPE_CHECKING:
    from .composition import (
        RuntimeGuardedJobProvider,
        RuntimeGuardedSignalProvider,
        build_external_action_service,
        guard_job_provider,
        guard_signal_provider,
    )

_COMPOSITION_EXPORTS = {
    "RuntimeGuardedJobProvider",
    "RuntimeGuardedSignalProvider",
    "build_external_action_service",
    "guard_job_provider",
    "guard_signal_provider",
}


def __getattr__(name: str) -> Any:
    """Load composition helpers on demand to keep config imports cycle-safe."""
    if name not in _COMPOSITION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import composition

    value = getattr(composition, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy composition helpers to documentation and plugin discovery."""
    return sorted(set(globals()) | _COMPOSITION_EXPORTS)

__all__ = [
    "RuntimeConfig",
    "RuntimeEnvironment",
    "RuntimeGuardedJobProvider",
    "RuntimeGuardedSignalProvider",
    "build_external_action_service",
    "guard_job_provider",
    "guard_signal_provider",
]
