"""Tests for the Lifecycle Control Plane POC."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.base import IncomingEvent, Transition
from app.adapters.github_actions import GitHubActionsAdapter
from app.engine import apply_transition, InvalidTransitionError, EntityNotFoundError
from app.event_bus import EventBus
from app.models import (
    Base,
    Entity,
    LifecycleState,
    LifecycleStateEnum,
    TransitionLog,
)
from app.main import app, get_db
from app.config import load_templates, get_template
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# In-memory SQLite fixture
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite://"  # pure in-memory


@pytest.fixture()
def db_session():
    test_engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    Session = sessionmaker(bind=test_engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client(db_session):
    """FastAPI test client with in-memory DB."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_entity(db_session, name="svc") -> str:
    import uuid
    eid = str(uuid.uuid4())
    entity = Entity(id=eid, type="service", name=name)
    state = LifecycleState(
        entity_id=eid,
        current_state=LifecycleStateEnum.CREATED,
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(entity)
    db_session.add(state)
    db_session.commit()
    return eid


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------

class TestLifecycleStateEnum:
    def test_all_six_states_defined(self):
        states = [s.value for s in LifecycleStateEnum]
        assert states == ["CREATED", "IN_PROGRESS", "VALIDATED", "RELEASED", "OPERATING", "RETIRED"]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TestEngine:
    def test_forward_transition_one_step(self, db_session):
        eid = _make_entity(db_session)
        t = Transition(entity_id=eid, target_state="IN_PROGRESS", event_type="manual")
        state = apply_transition(db_session, t)
        assert state.current_state == LifecycleStateEnum.IN_PROGRESS

    def test_forward_transition_skip(self, db_session):
        """Adapter may jump ahead e.g. CREATED → VALIDATED."""
        eid = _make_entity(db_session)
        t = Transition(entity_id=eid, target_state="VALIDATED", event_type="ci.success")
        state = apply_transition(db_session, t)
        assert state.current_state == LifecycleStateEnum.VALIDATED

    def test_backward_transition_rejected(self, db_session):
        eid = _make_entity(db_session)
        apply_transition(db_session, Transition(eid, "VALIDATED", "ci"))
        with pytest.raises(InvalidTransitionError):
            apply_transition(db_session, Transition(eid, "CREATED", "manual"))

    def test_same_state_rejected(self, db_session):
        eid = _make_entity(db_session)
        with pytest.raises(InvalidTransitionError):
            apply_transition(db_session, Transition(eid, "CREATED", "manual"))

    def test_transition_log_written(self, db_session):
        eid = _make_entity(db_session)
        apply_transition(db_session, Transition(eid, "VALIDATED", "ci"))
        logs = db_session.query(TransitionLog).filter_by(entity_id=eid).all()
        assert any(log.to_state == "VALIDATED" for log in logs)

    def test_entity_not_found(self, db_session):
        with pytest.raises(EntityNotFoundError):
            apply_transition(db_session, Transition("nonexistent", "VALIDATED", "ci"))


# ---------------------------------------------------------------------------
# GitHub Actions Adapter
# ---------------------------------------------------------------------------

class TestGitHubActionsAdapter:
    def _make_event(self, workflow, conclusion):
        return IncomingEvent(
            source="github_actions",
            type="workflow_run.completed",
            payload={"workflow": workflow, "conclusion": conclusion, "entity_id": "abc"},
        )

    def test_can_handle_correct_source(self):
        adapter = GitHubActionsAdapter()
        event = self._make_event("ci", "success")
        assert adapter.can_handle(event) is True

    def test_cannot_handle_other_source(self):
        adapter = GitHubActionsAdapter()
        event = IncomingEvent(source="jenkins", type="workflow_run.completed", payload={})
        assert adapter.can_handle(event) is False

    def test_ci_success_maps_to_validated(self):
        adapter = GitHubActionsAdapter()
        t = adapter.map_event_to_transition(self._make_event("ci", "success"))
        assert t is not None
        assert t.target_state == LifecycleStateEnum.VALIDATED

    def test_cd_success_maps_to_released(self):
        adapter = GitHubActionsAdapter()
        t = adapter.map_event_to_transition(self._make_event("cd", "success"))
        assert t is not None
        assert t.target_state == LifecycleStateEnum.RELEASED

    def test_failure_conclusion_ignored(self):
        adapter = GitHubActionsAdapter()
        t = adapter.map_event_to_transition(self._make_event("ci", "failure"))
        assert t is None

    def test_unknown_workflow_ignored(self):
        adapter = GitHubActionsAdapter()
        t = adapter.map_event_to_transition(self._make_event("deploy", "success"))
        assert t is None

    def test_missing_entity_id_returns_none(self):
        adapter = GitHubActionsAdapter()
        event = IncomingEvent(
            source="github_actions",
            type="workflow_run.completed",
            payload={"workflow": "ci", "conclusion": "success"},
        )
        assert adapter.map_event_to_transition(event) is None


# ---------------------------------------------------------------------------
# Event Bus
# ---------------------------------------------------------------------------

class TestEventBus:
    def test_dispatch_transitions_entity(self, db_session):
        eid = _make_entity(db_session)
        bus = EventBus()
        bus.register(GitHubActionsAdapter())
        event = IncomingEvent(
            source="github_actions",
            type="workflow_run.completed",
            payload={"workflow": "ci", "conclusion": "success", "entity_id": eid},
        )
        result = bus.dispatch(event, db_session)
        assert result["status"] == "transitioned"
        assert result["new_state"] == "VALIDATED"

    def test_unhandled_source_returns_unhandled(self, db_session):
        bus = EventBus()
        bus.register(GitHubActionsAdapter())
        event = IncomingEvent(source="unknown", type="whatever", payload={})
        result = bus.dispatch(event, db_session)
        assert result["status"] == "unhandled"


# ---------------------------------------------------------------------------
# API (integration)
# ---------------------------------------------------------------------------

class TestAPI:
    def test_create_entity(self, client):
        resp = client.post("/entities", json={"type": "service", "name": "svc"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["current_state"] == "CREATED"
        assert data["name"] == "svc"

    def test_get_entity(self, client):
        eid = client.post("/entities", json={"type": "service", "name": "svc"}).json()["id"]
        resp = client.get(f"/entities/{eid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == eid

    def test_get_entity_not_found(self, client):
        resp = client.get("/entities/nonexistent-id")
        assert resp.status_code == 404

    def test_post_ci_event_transitions_to_validated(self, client):
        eid = client.post("/entities", json={"type": "service", "name": "svc"}).json()["id"]
        resp = client.post("/events", json={
            "source": "github_actions",
            "type": "workflow_run.completed",
            "payload": {"workflow": "ci", "conclusion": "success", "entity_id": eid},
        })
        assert resp.json()["status"] == "transitioned"
        assert resp.json()["new_state"] == "VALIDATED"

    def test_post_cd_event_transitions_to_released(self, client):
        eid = client.post("/entities", json={"type": "service", "name": "svc"}).json()["id"]
        client.post("/events", json={
            "source": "github_actions",
            "type": "workflow_run.completed",
            "payload": {"workflow": "ci", "conclusion": "success", "entity_id": eid},
        })
        resp = client.post("/events", json={
            "source": "github_actions",
            "type": "workflow_run.completed",
            "payload": {"workflow": "cd", "conclusion": "success", "entity_id": eid},
        })
        assert resp.json()["status"] == "transitioned"
        assert resp.json()["new_state"] == "RELEASED"

    def test_backward_transition_returns_error(self, client):
        eid = client.post("/entities", json={"type": "service", "name": "svc"}).json()["id"]
        client.post("/events", json={
            "source": "github_actions",
            "type": "workflow_run.completed",
            "payload": {"workflow": "ci", "conclusion": "success", "entity_id": eid},
        })
        # ci again → VALIDATED already, same state rejected
        resp = client.post("/events", json={
            "source": "github_actions",
            "type": "workflow_run.completed",
            "payload": {"workflow": "ci", "conclusion": "success", "entity_id": eid},
        })
        assert resp.json()["status"] == "error"

    def test_metrics_endpoint(self, client):
        client.post("/entities", json={"type": "service", "name": "svc"})
        resp = client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_entities"] >= 1
        assert "transitions_count" in data
        assert "avg_time_between_states_seconds" in data


# ---------------------------------------------------------------------------
# Config / YAML template
# ---------------------------------------------------------------------------

class TestConfig:
    def test_backend_service_template_loads(self):
        load_templates()
        tmpl = get_template("backend_service")
        assert tmpl is not None
        assert tmpl["entity_type"] == "service"
        assert tmpl["initial_state"] == "CREATED"
        assert "VALIDATED" in tmpl["bindings"]
        assert "RELEASED" in tmpl["bindings"]
