"""Production runtime configuration and lazily loaded composition primitives."""

from typing import TYPE_CHECKING, Any

from .config import RuntimeConfig, RuntimeEnvironment

if TYPE_CHECKING:
    from .candidate_service import (
        CandidateServiceConfig,
        CandidateServiceRuntime,
        build_candidate_service_from_env,
    )
    from .composition import (
        RuntimeGuardedJobProvider,
        RuntimeGuardedSignalProvider,
        RuntimeCandidateApp,
        RuntimeOperationalApp,
        build_operational_app_from_env,
        build_candidate_sandbox_app_from_env,
        build_external_action_service,
        guard_job_provider,
        guard_signal_provider,
    )

_COMPOSITION_EXPORTS = {
    "RuntimeGuardedJobProvider",
    "RuntimeGuardedSignalProvider",
    "RuntimeCandidateApp",
    "RuntimeOperationalApp",
    "build_operational_app_from_env",
    "build_candidate_sandbox_app_from_env",
    "build_external_action_service",
    "guard_job_provider",
    "guard_signal_provider",
}

_SERVICE_EXPORTS = {
    "CandidateServiceConfig",
    "CandidateServiceRuntime",
    "build_candidate_service_from_env",
}


def __getattr__(name: str) -> Any:
    """Load composition helpers on demand to keep config imports cycle-safe."""
    if name in _SERVICE_EXPORTS:
        from . import candidate_service

        value = getattr(candidate_service, name)
        globals()[name] = value
        return value
    if name not in _COMPOSITION_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import composition

    value = getattr(composition, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy composition helpers to documentation and plugin discovery."""
    return sorted(set(globals()) | _COMPOSITION_EXPORTS | _SERVICE_EXPORTS)

__all__ = [
    "RuntimeConfig",
    "RuntimeEnvironment",
    "CandidateServiceConfig",
    "CandidateServiceRuntime",
    "RuntimeGuardedJobProvider",
    "RuntimeGuardedSignalProvider",
    "RuntimeCandidateApp",
    "RuntimeOperationalApp",
    "build_operational_app_from_env",
    "build_candidate_sandbox_app_from_env",
    "build_candidate_service_from_env",
    "build_external_action_service",
    "guard_job_provider",
    "guard_signal_provider",
]
