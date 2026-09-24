"""Provider-neutral, human-gated communication capability."""

from .communication_adapter import CommunicationAdapter
from .communication_repository import CommunicationRepository
from .communication_service import CommunicationService
from .fake_communication_adapter import FakeCommunicationAdapter
from .models import CommunicationMessage, MessageDirection

__all__ = [
    "CommunicationAdapter",
    "CommunicationMessage",
    "CommunicationRepository",
    "CommunicationService",
    "FakeCommunicationAdapter",
    "MessageDirection",
]
