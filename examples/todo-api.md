---
name: "Todo API"
timebox: "10m"
constraints:
  - "Use Flask or FastAPI"
  - "Use SQLite for storage"
  - "REST API only, no frontend"
acceptance:
  - "cmd: pip install -r requirements.txt"
  - "cmd: python -c \"import app\""
  - "API supports CRUD operations for todos"
---

# Todo API

Build a simple REST API for managing todo items.

## Requirements

- CRUD endpoints:
  - `POST /todos` — create a todo (title, optional description)
  - `GET /todos` — list all todos
  - `GET /todos/<id>` — get a single todo
  - `PUT /todos/<id>` — update a todo (title, description, completed)
  - `DELETE /todos/<id>` — delete a todo
- SQLite database for persistence
- JSON request/response format
- Basic error handling (404 for missing todos, 400 for bad input)
- `requirements.txt` with dependencies
- `README.md` with API documentation

## Data Model

```
Todo:
  id: integer (auto)
  title: string (required)
  description: string (optional)
  completed: boolean (default false)
  created_at: datetime
```
