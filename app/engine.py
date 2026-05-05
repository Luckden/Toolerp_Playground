"""State Transition Engine – enforces valid lifecycle progressions."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapters.base import Transition
from app.models import (
    Entity,
    LifecycleState,
    LifecycleStateEnum,
    TransitionLog,
)

logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    pass


class EntityNotFoundError(Exception):
    pass


def apply_transition(db: Session, transition: Transition) -> LifecycleState:
    """
    Apply a lifecycle transition to an entity.

    Raises:
        EntityNotFoundError: if entity does not exist.
        InvalidTransitionError: if the transition is not allowed.
    """
    entity: Entity | None = db.get(Entity, transition.entity_id)
    if entity is None:
        raise EntityNotFoundError(f"Entity '{transition.entity_id}' not found.")

    state: LifecycleState | None = entity.lifecycle_state
    if state is None:
        raise EntityNotFoundError(f"Entity '{transition.entity_id}' has no lifecycle state.")

    current = state.current_state
    target = LifecycleStateEnum(transition.target_state)

    # Build ordered state list to verify the target is strictly forward
    STATE_ORDER = list(LifecycleStateEnum)
    current_idx = STATE_ORDER.index(current)
    try:
        target_idx = STATE_ORDER.index(target)
    except ValueError:
        raise InvalidTransitionError(f"Unknown target state '{target}'.")

    if target_idx <= current_idx:
        raise InvalidTransitionError(
            f"Cannot transition '{current}' → '{target}'. "
            f"Target must be a future state in the lifecycle."
        )

    from_state = current.value

    state.current_state = target
    state.updated_at = datetime.now(timezone.utc)

    log = TransitionLog(
        entity_id=transition.entity_id,
        from_state=from_state,
        to_state=target.value,
        event_type=transition.event_type,
        timestamp=datetime.now(timezone.utc),
        metadata_=transition.metadata,
    )
    db.add(log)
    db.commit()
    db.refresh(state)

    logger.info(
        "Transition applied | entity=%s | %s → %s | event=%s",
        transition.entity_id,
        from_state,
        target.value,
        transition.event_type,
    )
    return state
