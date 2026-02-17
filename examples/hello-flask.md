---
name: "Hello Flask"
timebox: "5m"
constraints:
  - "Use Flask"
  - "Python only"
  - "No database needed"
acceptance:
  - "cmd: pip install -r requirements.txt && python -c \"import app\""
  - "Server has a / route that returns HTML"
---

# Hello Flask

Build a minimal Flask web application.

## Requirements

- A single `app.py` file with a Flask application
- A `/` route that returns a simple HTML page with:
  - A heading that says "Hello from NoScope!"
  - A paragraph with the current date/time
- A `requirements.txt` with Flask pinned
- A `README.md` with instructions to run it

## How to run

```
pip install -r requirements.txt
python app.py
```

The server should start on port 5000.
