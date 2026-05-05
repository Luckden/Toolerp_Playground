from __future__ import annotations

from typing import Optional

from app.adapters.base import Adapter, IncomingEvent, Transition
from app.models import LifecycleStateEnum


class GitHubActionsAdapter(Adapter):
    """Handles GitHub Actions webhook-style events."""

    HANDLED_EVENT_TYPE = "workflow_run.completed"

    def can_handle(self, event: IncomingEvent) -> bool:
        return event.source == "github_actions" and event.type == self.HANDLED_EVENT_TYPE

    def map_event_to_transition(self, event: IncomingEvent) -> Optional[Transition]:
        payload = event.payload
        conclusion = payload.get("conclusion")
        workflow = payload.get("workflow")
        entity_id = payload.get("entity_id")

        if not entity_id:
            return None

        if conclusion == "success" and workflow == "ci":
            return Transition(
                entity_id=entity_id,
                target_state=LifecycleStateEnum.VALIDATED,
                event_type=event.type,
                metadata={"workflow": workflow, "conclusion": conclusion},
            )

        if conclusion == "success" and workflow == "cd":
            return Transition(
                entity_id=entity_id,
                target_state=LifecycleStateEnum.RELEASED,
                event_type=event.type,
                metadata={"workflow": workflow, "conclusion": conclusion},
            )

        return None
