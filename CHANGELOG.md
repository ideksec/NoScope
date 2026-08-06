# Changelog

All notable changes to NoScope are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-08

A six-months-later revival: un-break the tool on today's APIs, modernize the LLM
layer, and build out NoScope's own agent harness (rather than wrapping the
Claude Agent SDK — the orchestration is the point). See `PLAN.md` for the full
review and roadmap.

### Fixed

- **Runs out of the box again.** Default models updated to `claude-sonnet-5` /
  `gpt-5.6-terra` (the previous `claude-sonnet-4-20250514` was retired June
  2026, making the default run a hard API error). Defaults are pinned by a test.
- OpenAI `stop_reason` is normalized (`stop` → `end_turn`), so the OpenAI agent
  loop can actually terminate instead of burning iterations.
- `advance_phase` is called for BUILD and VERIFY, so the event log stamps the
  correct phase on tool events.
- The planner no longer swallows API errors as "invalid plan".
- Git subprocesses run with the sanitized environment (API keys were visible).
- App launch is opt-in via `--serve`; by default NoScope prints the run command
  instead of blocking on a foreground server, honoring the hard-deadline promise.
- Docker sandbox: file writes use base64 (the old heredoc corrupted backslashes
  and could truncate on content); all container paths are validated against
  traversal and shell-injection.

### Added

- **Editing and discovery tools:** `edit_file` (exact-string replacement with a
  uniqueness guard + diff, replacing whole-file rewrites), `search_files`
  (regex/grep), `find_files` (glob), and `read_file` line windows.
- **Verification that actually verifies:** acceptance checks assert expected
  output (`cmd: <command> ==> <text>`), VERIFY confirms the app runs by having
  the harness execute the agent's proof command (not by trusting a `VERIFIED:`
  string), and a failing check gets a bounded auto-repair-and-retry.
- **Token budgets:** an optional `--token-budget` spend cap that stops the build
  the same way the timebox does — the spend guarantee is now true in tokens too.
- **Cache-aware cost reporting:** the summary shows cached-read tokens and the
  dollars prompt caching saved.
- Structured outputs, prompt caching, adaptive thinking + effort, and SDK-native
  retries in the LLM layer; a cheaper model (Haiku) for the handoff report.
- `--serve` flag; `NOSCOPE_FAST_MODEL`, `NOSCOPE_MAX_TOKENS`, `NOSCOPE_EFFORT`,
  `NOSCOPE_TOKEN_BUDGET` settings.

### Changed

- Prompts moved from ALL-CAPS imperatives to goal + constraints style.
- All dependencies upgraded (`anthropic` 0.80 → 0.120, `openai` 2.21 → 2.53)
  with version floors.
- Example specs modernized (`python3`, expected-output checks) and README given
  an honest "When to use NoScope" section.

### Removed

- The never-wired Textual TUI (`--tui`), unreachable panic mode, the dead
  streaming code path, and duplicated pricing tables.

## [0.1.0] — 2026-02

Initial release: time-boxed autonomous agent orchestration that builds runnable
MVPs from written specs.
