"""FastAPI application – Lifecycle Control Plane POC."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.github_actions import GitHubActionsAdapter
from app.config import load_templates
from app.event_bus import bus
from app.adapters.base import IncomingEvent
from app.models import (
    Entity,
    LifecycleState,
    LifecycleStateEnum,
    TransitionLog,
    create_tables,
    engine,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB session factory
# ---------------------------------------------------------------------------
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    load_templates()
    bus.register(GitHubActionsAdapter())
    logger.info("Lifecycle Control Plane started.")
    yield


app = FastAPI(title="Lifecycle Control Plane", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class EntityCreate(BaseModel):
    type: str
    name: str


class EntityResponse(BaseModel):
    id: str
    type: str
    name: str
    current_state: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class EventPayload(BaseModel):
    source: str
    type: str
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/entities", response_model=EntityResponse, status_code=201)
def create_entity(body: EntityCreate, db: Session = Depends(get_db)):
    entity_id = str(uuid.uuid4())
    entity = Entity(id=entity_id, type=body.type, name=body.name)
    state = LifecycleState(
        entity_id=entity_id,
        current_state=LifecycleStateEnum.CREATED,
        updated_at=datetime.now(timezone.utc),
    )
    log = TransitionLog(
        entity_id=entity_id,
        from_state=None,
        to_state=LifecycleStateEnum.CREATED.value,
        event_type="entity.created",
        timestamp=datetime.now(timezone.utc),
        metadata_=None,
    )
    db.add(entity)
    db.add(state)
    db.add(log)
    db.commit()
    db.refresh(entity)
    db.refresh(state)
    logger.info("Entity created | id=%s type=%s name=%s", entity_id, body.type, body.name)
    return EntityResponse(
        id=entity.id,
        type=entity.type,
        name=entity.name,
        current_state=state.current_state.value,
        updated_at=state.updated_at,
    )


@app.get("/entities/{entity_id}", response_model=EntityResponse)
def get_entity(entity_id: str, db: Session = Depends(get_db)):
    entity: Entity | None = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found.")
    state = entity.lifecycle_state
    return EntityResponse(
        id=entity.id,
        type=entity.type,
        name=entity.name,
        current_state=state.current_state.value,
        updated_at=state.updated_at,
    )


@app.post("/events")
def ingest_event(body: EventPayload, db: Session = Depends(get_db)):
    event = IncomingEvent(source=body.source, type=body.type, payload=body.payload)
    result = bus.dispatch(event, db)
    return result


@app.get("/metrics")
def get_metrics(db: Session = Depends(get_db)):
    total_entities = db.scalar(select(func.count()).select_from(Entity))

    transitions_count = db.scalar(
        select(func.count()).select_from(TransitionLog).where(TransitionLog.event_type != "entity.created")
    )

    # Average time between consecutive states per entity (seconds)
    logs = (
        db.execute(
            select(TransitionLog.entity_id, TransitionLog.timestamp)
            .order_by(TransitionLog.entity_id, TransitionLog.timestamp)
        )
        .fetchall()
    )

    deltas: list[float] = []
    prev_entity: str | None = None
    prev_ts: datetime | None = None
    for row in logs:
        eid, ts = row.entity_id, row.timestamp
        if prev_entity == eid and prev_ts is not None:
            # Ensure both are offset-aware
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if prev_ts.tzinfo is None:
                prev_ts = prev_ts.replace(tzinfo=timezone.utc)
            deltas.append((ts - prev_ts).total_seconds())
        prev_entity = eid
        prev_ts = ts

    avg_time = (sum(deltas) / len(deltas)) if deltas else 0.0

    return {
        "total_entities": total_entities,
        "transitions_count": transitions_count,
        "avg_time_between_states_seconds": round(avg_time, 3),
    }
