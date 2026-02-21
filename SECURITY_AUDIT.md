# Security Audit Notes (Application Layer)

Date: 2026-02-21  
Scope: NoScope application behavior and runtime safety controls (not generated MVP app security posture)

## Executive Summary

This audit focused on end-user security risks in NoScope itself, with extra emphasis on accidental secret leakage.  
Several high-risk leak paths were identified and fixed:

1. Tool telemetry logged sensitive arguments/results directly to `events.jsonl`
2. Shell subprocesses inherited sensitive environment variables (including API keys)
3. Path boundary checks used unsafe prefix matching, allowing workspace-escape edge cases
4. Event log file permissions were not hardened (potential overexposure on permissive umask)
5. Redaction coverage was narrow and missed common credential/header/private-key patterns

## Findings and Fixes

### 1) Sensitive tool telemetry persisted unredacted

- **Affected file(s):** `noscope/tools/dispatcher.py`
- **Risk:** Tool call args/results (including file content and command output) were logged raw.  
  This could persist secrets in `.noscope/runs/.../events.jsonl`.
- **Fix implemented:**
  - Added recursive redaction for nested tool payloads before logging
  - Added payload trimming for bulky/sensitive fields (`content`, `stdout`, `stderr`)
  - Added string truncation guard for large values

### 2) Event log accepted and stored raw secret-looking values

- **Affected file(s):** `noscope/logging/events.py`
- **Risk:** Non-tool events could still persist token-like data in summaries/data/results.
- **Fix implemented:**
  - Added automatic redaction on all event payload strings
  - Added owner-only file permissions (`0600`) for `events.jsonl` (best effort)

### 3) Shell and launched app inherited full host environment

- **Affected file(s):** `noscope/tools/shell.py`, `noscope/orchestrator.py`
- **Risk:** LLM-directed shell commands (and launched demos) could access provider keys or other tokens from environment.
- **Fix implemented:**
  - Introduced `build_execution_env()` to strip sensitive env vars and clean `.venv` path entries
  - Reused hardened env construction for both shell tool execution and app launch
  - Added runtime secret map into `ToolContext` so known provider keys are explicitly redacted in outputs

### 4) Workspace path checks were bypassable via prefix collision

- **Affected file(s):** `noscope/tools/safety.py`, `noscope/tools/shell.py`
- **Risk:** Prefix-based `startswith` checks could treat sibling paths like `/tmp/ws-evil` as if inside `/tmp/ws`.
- **Fix implemented:**
  - Replaced prefix matching with robust `Path.relative_to(...)` containment checks
  - Applied this containment enforcement to shell `cwd` resolution

### 5) Redaction coverage insufficient for modern secret formats

- **Affected file(s):** `noscope/tools/redaction.py`, `noscope/tools/docker.py`
- **Risk:** Existing redaction missed common auth headers, multiple token formats, and private key blocks.
- **Fix implemented:**
  - Expanded redaction patterns for:
    - assignment-style secret values (quoted/unquoted)
    - auth headers (`Authorization`, `x-api-key`)
    - common provider token formats
    - PEM private key blocks
  - Added `redact_text()` and `redact_structured()` helpers
  - Updated Docker shell output redaction to use expanded redaction pipeline

## Validation

- Lint: `python3 -m ruff check noscope tests` ✅
- Tests: `python3 -m pytest -q` ✅ (102 passed)
- Added regression tests covering:
  - path prefix collision bypass prevention
  - env stripping for subprocess execution
  - nested payload redaction and log field omission
  - event log redaction and file permission hardening

## Residual Risk / Future Hardening

- A warning in shell-tool tests indicates asyncio subprocess transport cleanup behavior under test shutdown; not a functional failure but worth future cleanup.
- Optional future work:
  - introduce explicit redaction/deny controls for reading highly sensitive file classes (`.env`, private keys) in `read_file`
  - add structured security mode toggles for stricter enterprise defaults
