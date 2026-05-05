# Toolerp Playground – Lifecycle Control Plane (POC)

A minimal, runnable proof-of-concept for a **Lifecycle Control Plane**.

Demonstrates:
- A canonical lifecycle state machine (6 fixed states)
- An adapter that maps GitHub Actions events to state transitions
- Event-driven state progression (in-process event bus)
- Basic observability (structured logs + `/metrics` endpoint)

---

## Tech Stack

| Layer | Tool |
|-------|------|
| Language | Python 3.11 |
| Framework | FastAPI |
| Storage | SQLite (SQLAlchemy) |
| Messaging | In-process event bus |
| Config | YAML |

---

## Canonical Lifecycle States

```
CREATED → IN_PROGRESS → VALIDATED → RELEASED → OPERATING → RETIRED
```

Transitions must always move **forward** in this sequence.
Adapters may skip intermediate states (e.g., CREATED → VALIDATED).

---

## Project Structure

```
.
├── app/
│   ├── main.py                    # FastAPI app (routes, startup)
│   ├── models.py                  # SQLAlchemy models + state enum
│   ├── engine.py                  # State transition engine
│   ├── event_bus.py               # In-process event dispatcher
│   ├── config.py                  # YAML template loader
│   └── adapters/
│       ├── base.py                # Abstract Adapter interface
│       └── github_actions.py      # GitHub Actions adapter
├── templates/
│   └── backend_service.yaml       # Process template
├── requirements.txt
└── README.md
```

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload
```

The app starts on `http://127.0.0.1:8000`.
A SQLite database `lifecycle.db` is created automatically.

---

## API

### POST /entities – Create an entity

```bash
curl -s -X POST http://localhost:8000/entities \
  -H "Content-Type: application/json" \
  -d '{"type": "service", "name": "my-backend"}'
```

Response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "type": "service",
  "name": "my-backend",
  "current_state": "CREATED",
  "updated_at": "2026-01-01T00:00:00"
}
```

---

### GET /entities/{id} – Get current state

```bash
curl -s http://localhost:8000/entities/550e8400-e29b-41d4-a716-446655440000
```

---

### POST /events – Ingest an event

**CI success → VALIDATED:**
```bash
curl -s -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "source": "github_actions",
    "type": "workflow_run.completed",
    "payload": {
      "workflow": "ci",
      "conclusion": "success",
      "entity_id": "550e8400-e29b-41d4-a716-446655440000"
    }
  }'
```

**CD success → RELEASED:**
```bash
curl -s -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "source": "github_actions",
    "type": "workflow_run.completed",
    "payload": {
      "workflow": "cd",
      "conclusion": "success",
      "entity_id": "550e8400-e29b-41d4-a716-446655440000"
    }
  }'
```

---

### GET /metrics – Observability

```bash
curl -s http://localhost:8000/metrics
```

Response:
```json
{
  "total_entities": 1,
  "transitions_count": 2,
  "avg_time_between_states_seconds": 0.03
}
```

---

## Demo Flow (end-to-end)

```bash
# 1. Create entity
ENTITY_ID=$(curl -s -X POST http://localhost:8000/entities \
  -H "Content-Type: application/json" \
  -d '{"type": "service", "name": "my-backend"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo "Created: $ENTITY_ID"

# 2. CI success -> VALIDATED
curl -s -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d "{\"source\":\"github_actions\",\"type\":\"workflow_run.completed\",\"payload\":{\"workflow\":\"ci\",\"conclusion\":\"success\",\"entity_id\":\"$ENTITY_ID\"}}"

# 3. Verify state (VALIDATED)
curl -s http://localhost:8000/entities/$ENTITY_ID

# 4. CD success -> RELEASED
curl -s -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d "{\"source\":\"github_actions\",\"type\":\"workflow_run.completed\",\"payload\":{\"workflow\":\"cd\",\"conclusion\":\"success\",\"entity_id\":\"$ENTITY_ID\"}}"

# 5. Verify state (RELEASED)
curl -s http://localhost:8000/entities/$ENTITY_ID

# 6. Metrics
curl -s http://localhost:8000/metrics
```

---

## Process Template

`templates/backend_service.yaml` declares which adapter/workflow drives each milestone state:

```yaml
entity_type: service
initial_state: CREATED
bindings:
  VALIDATED:
    adapter: github_actions
    workflow: ci
  RELEASED:
    adapter: github_actions
    workflow: cd
```

---

## GitHub Actions Adapter Rules

| Condition | Target State |
|-----------|-------------|
| `source == github_actions` AND `workflow == ci` AND `conclusion == success` | `VALIDATED` |
| `source == github_actions` AND `workflow == cd` AND `conclusion == success` | `RELEASED` |
| Any other event | ignored |
