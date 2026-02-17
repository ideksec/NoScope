---
name: "CLI Calculator"
timebox: "5m"
constraints:
  - "Python only"
  - "No external dependencies"
  - "Command-line interface"
acceptance:
  - "cmd: python calc.py add 2 3"
  - "cmd: python calc.py multiply 4 5"
  - "Handles basic error cases"
---

# CLI Calculator

Build a command-line calculator tool.

## Requirements

- `calc.py` — a CLI tool that supports:
  - `add <a> <b>` — addition
  - `subtract <a> <b>` — subtraction
  - `multiply <a> <b>` — multiplication
  - `divide <a> <b>` — division (with zero-division handling)
- Use `argparse` or `sys.argv` for argument parsing
- Print the result to stdout
- Handle invalid input gracefully (non-numeric values, wrong number of args)
- Exit with code 0 on success, 1 on error
