"""In-process event bus that routes events through registered adapters."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.adapters.base import Adapter, IncomingEvent
from app.engine import apply_transition, EntityNotFoundError, InvalidTransitionError

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._adapters: list[Adapter] = []

    def register(self, adapter: Adapter) -> None:
        self._adapters.append(adapter)

    def dispatch(self, event: IncomingEvent, db: Session) -> dict:
        """
        Route an event through registered adapters.

        Returns a summary dict with the result.
        """
        for adapter in self._adapters:
            if not adapter.can_handle(event):
                continue

            transition = adapter.map_event_to_transition(event)
            if transition is None:
                logger.info("Adapter %s ignored event '%s'.", type(adapter).__name__, event.type)
                return {"status": "ignored", "reason": "adapter returned no transition"}

            try:
                state = apply_transition(db, transition)
                return {
                    "status": "transitioned",
                    "entity_id": transition.entity_id,
                    "new_state": state.current_state.value,
                }
            except EntityNotFoundError:
                logger.warning("Transition failed: entity not found | entity_id=%s", transition.entity_id)
                return {"status": "error", "detail": "Entity not found."}
            except InvalidTransitionError as exc:
                logger.warning("Transition failed: invalid transition | %s", exc)
                return {"status": "error", "detail": "Invalid lifecycle transition."}

        logger.info("No adapter handled event source='%s' type='%s'.", event.source, event.type)
        return {"status": "unhandled", "reason": "no adapter matched"}


# Singleton bus used by the FastAPI app
bus = EventBus()
