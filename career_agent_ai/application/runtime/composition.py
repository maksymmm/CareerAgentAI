"""Production composition helpers that enforce runtime safety flags."""

from __future__ import annotations

from dataclasses import dataclass

from career_agent_ai.application.career.opportunity_signal_provider import (
    OpportunitySignalProvider,
)
from career_agent_ai.application.career.opportunity_signal import OpportunitySignal
from career_agent_ai.application.external_actions import (
    ExternalActionAdapter,
    ExternalActionService,
)
from career_agent_ai.application.external_actions.external_action_repository import (
    ExternalActionOperationRepository,
)

from .config import RuntimeConfig


@dataclass(frozen=True)
class RuntimeGuardedSignalProvider:
    """Block network-backed signal collection unless runtime policy enables it."""

    config: RuntimeConfig
    provider: OpportunitySignalProvider

    def collect(self) -> tuple[OpportunitySignal, ...]:
        """Collect signals only when network providers are explicitly enabled."""
        if not self.config.allow_network_providers:
            raise PermissionError("Network providers are disabled by runtime policy.")
        return self.provider.collect()


def build_external_action_service(
    config: RuntimeConfig,
    repository: ExternalActionOperationRepository,
    adapter: ExternalActionAdapter,
) -> ExternalActionService:
    """Build an action service whose provider call obeys the runtime kill switch."""
    if not isinstance(config, RuntimeConfig):
        raise TypeError("config must be a RuntimeConfig.")
    return ExternalActionService(
        repository,
        adapter,
        execution_allowed=lambda: config.allow_consequential_actions,
    )


def guard_signal_provider(
    config: RuntimeConfig,
    provider: OpportunitySignalProvider,
) -> RuntimeGuardedSignalProvider:
    """Wrap a signal provider so disabled network policy prevents collection."""
    if not isinstance(config, RuntimeConfig):
        raise TypeError("config must be a RuntimeConfig.")
    return RuntimeGuardedSignalProvider(config=config, provider=provider)
