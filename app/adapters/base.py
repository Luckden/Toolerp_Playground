from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class IncomingEvent:
    source: str
    type: str
    payload: dict[str, Any]


@dataclass
class Transition:
    entity_id: str
    target_state: str
    event_type: str
    metadata: dict[str, Any] | None = None


class Adapter(ABC):
    @abstractmethod
    def can_handle(self, event: IncomingEvent) -> bool:
        """Return True if this adapter can process the given event."""

    @abstractmethod
    def map_event_to_transition(self, event: IncomingEvent) -> Optional[Transition]:
        """Map an incoming event to a lifecycle Transition, or None to ignore."""
