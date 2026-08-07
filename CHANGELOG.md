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
- **Timed-out commands no longer leak their process trees.** Killing the shell
  left grandchildren (a dev server, say) running as orphans still holding the
  output pipes, so the port stayed bound for the rest of the run and the
  harness blocked until the orphan happened to exit. Children now run in their
  own session and a timeout terminates the whole group. Same fix for the
  `docker exec` client and for Ctrl+C on `--serve`.
- **BUILD no longer idles waiting for the audit agent.** The auditor loops
  until the phase is nearly over, and it was gathered together with the
  workers — so a build that genuinely finished early still blocked for the
  rest of BUILD's budget before HARDEN could start (on a 30-minute run, up to
  ~16 wasted minutes). It's now cancelled once the workers return; findings
  are read from the shared feed, so none are lost.
- `noscope doctor` exits non-zero when a requirement is missing, so it works as
  a gate — and no longer counts "OpenAI key not set" as a failure when a valid
  Anthropic key is present.
- App launch is opt-in via `--serve`; by default NoScope prints the run command
  instead of blocking on a foreground server, honoring the hard-deadline promise.
- Docker sandbox: file writes use base64 (the old heredoc corrupted backslashes
  and could truncate on content); all container paths are validated against
  traversal and shell-injection; container command timeouts are now
  deadline-aware like the host shell tool.
- `noscope new` writes its spec with a real YAML dumper — a project name or
  constraint containing a quote or colon previously produced an invalid file —
  and slugifies the filename safely.

### Added

- **`--dry-run`:** exercises the entire pipeline — tools, acceptance checks,
  verification, event log, handoff report — with no API calls and no tokens,
  so the harness can be smoke-tested before spending anything.
- **`doctor --live`:** makes one minimal API call so a bad key or wrong model
  fails in seconds rather than part-way through a build.
- **Actionable API errors:** auth failures, unknown models, rate limits,
  overload, and network errors now print a diagnosis and the fix instead of a
  bare traceback.
- **Context management:** tool results are capped and the conversation is
  trimmed to a character budget before each request, so long runs no longer
  risk failing on context length (trimming never orphans a tool result).
- **Real audit checks:** the audit agent compile-checks Python (and parses
  JavaScript when `node` is available), so broken code is caught during BUILD
  and fed back to workers instead of only existing files being counted.
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
- **Write-conflict detection:** a shared `WriteLedger` records the last writer
  of every file, so when parallel agents overwrite each other the clobbering
  agent is warned in-conversation, a `write.conflict` event is logged, and the
  handoff report names the affected files. Previously two workers could both
  "succeed" while one's work was silently discarded.
- **Sandbox preflight:** `--sandbox` verifies Docker is actually usable before
  PLAN instead of failing part-way through a build, and distinguishes
  not-installed from daemon-down. `doctor` reports the daemon, not just the
  binary on `PATH`.
- Structured outputs, prompt caching, adaptive thinking + effort, and SDK-native
  retries in the LLM layer; a cheaper model (Haiku) for the handoff report.
- `--serve` and `--workers` flags; `NOSCOPE_FAST_MODEL`, `NOSCOPE_MAX_TOKENS`,
  `NOSCOPE_EFFORT`, `NOSCOPE_TOKEN_BUDGET`, `NOSCOPE_MAX_WORKERS` settings.

### Changed

- Prompts moved from ALL-CAPS imperatives to goal + constraints style.
- All dependencies upgraded (`anthropic` 0.80 → 0.120, `openai` 2.21 → 2.53)
  with version floors.
- Example specs modernized (`python3`, expected-output checks) and README given
  an honest "When to use NoScope" section.
- CI now checks formatting and runs a no-key smoke job that drives the real CLI
  through all six phases, so a broken pipeline fails CI even when every unit
  test passes. Provider request/response shapes are covered by mocked-SDK tests
  — previously no SDK call was asserted anywhere.

### Removed

- The never-wired Textual TUI (`--tui`), unreachable panic mode, the dead
  streaming code path, and duplicated pricing tables.

## [0.1.0] — 2026-02

Initial release: time-boxed autonomous agent orchestration that builds runnable
MVPs from written specs.
