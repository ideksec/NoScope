# NoScope — Project Review & Plan (August 2026)

> Status review of the codebase as of 2026-08-06, six months after the initial
> build (Feb 2026), and the plan for making NoScope a project worth releasing.

## 1. Verdict

**The concept is still relevant. The implementation is not.**

NoScope's thesis — *a time-boxed, capability-gated, one-shot agent run that
always ends with a runnable artifact and a report, never a hanging process or a
surprise bill* — is still a differentiated product position in mid-2026:

- **Claude Code / Codex / Cursor** are interactive power tools: an engineer
  drives them. None of them offer a hard wall-clock spend cap or a one-shot
  spec-in → MVP-out contract.
- **Devin-style cloud agents** offer async autonomy but are open-ended on cost
  and opaque on permissions — the exact gaps NoScope was designed around.
- **Anthropic Managed Agents** (hosted loop + sandbox + rubric-graded
  "Outcomes") overlap with parts of NoScope, but are a platform primitive, not
  a local, provider-agnostic, auditable CLI product.

What has changed is that the *bottom half* of NoScope — the hand-rolled agent
loop, tool registry, retry logic, sandboxing, and context management — is now
commodity infrastructure. In 2026, agent harnesses (Claude Agent SDK, the SDK
tool runners, Managed Agents) provide all of it, better tested and continuously
maintained. NoScope's value is the **top half**: the timebox engine, the
capability/approval model, the spec contract, the event log, and the guaranteed
handoff. The plan below re-architects the project so we keep building the part
that is differentiated and stop maintaining the part that is commodity.

## 2. What is broken or outdated today

### 2.1 Dead on arrival: model defaults

| Location | Value | Status (Aug 2026) |
|---|---|---|
| `noscope/llm/providers/anthropic.py` | `claude-sonnet-4-20250514` | Deprecated; scheduled for retirement June 2026 — likely 404s today |
| `noscope/llm/providers/openai.py` | `gpt-4o` | Two generations behind (current family: GPT-5.6) |
| `noscope/ui/console.py` (×2, duplicated) | 2025 pricing table | Wrong prices, wrong models, silent fallback rates |

The tool's out-of-the-box run path on Anthropic is probably a hard API error.
This alone makes the current `main` unreleasable.

### 2.2 The LLM layer predates the modern API surface

Pinned `anthropic==0.80.0` / `openai==2.21.0`; every call is a bare
`messages.create` / `chat.completions.create` with `model`, `max_tokens=8192`,
`messages`, `system`, `tools` — nothing else. Missing, all of which existed and
were stable well before today:

- **Structured outputs** — the planner asks for JSON in prose and hand-strips
  markdown fences with 3 repair retries, while `PlanOutput` is already a
  Pydantic model that could be enforced via `output_config.format` /
  `messages.parse()`. The `json_schema` parameter in our own provider protocol
  is accepted and silently ignored.
- **Prompt caching** — up to 200 iterations per agent × ~6 concurrent agents,
  each re-sending its full growing history and tool schemas at full price on
  every turn. This is the single largest cost multiplier in the system —
  ironic for a tool whose pitch is cost control.
- **Adaptive thinking / effort** — planning, building, and verification all run
  as non-reasoning single calls. Current models expect
  `thinking: {type: "adaptive"}` + `output_config.effort`.
- **Context management** — none. No compaction, no truncation, no token
  accounting, no `count_tokens`. Whole-file reads and 50 KB shell dumps
  accumulate until the run dies or the deadline saves it.
- **OpenAI provider** — legacy Chat Completions with the nested-`function` tool
  schema and `response_format: {"type": "json_object"}` (schema discarded);
  no Responses API; and a provider-parity bug: the agent loop only exits on
  `stop_reason == "end_turn"`, which OpenAI never emits (`"stop"`), so the
  OpenAI path burns iterations re-sending an unchanged conversation.
- **Retries** — hand-rolled, Anthropic-`complete`-only, retries only 429; no
  handling of 529/5xx/timeouts; ignores `Retry-After`. Both SDKs already ship
  configurable retry logic we bypass.

### 2.3 Architectural assumptions from early 2025

- **No edit/patch tool** — whole-file `write_file` only. Forces full-file
  re-emission (token cost) and makes multi-agent conflicts last-write-wins.
- **No search/grep/glob, no partial reads, no web fetch** — agents discover
  code one `list_directory` at a time.
- **`MAX_WORKERS = 2` "to avoid API rate limits"** — a 2025 rate-limit posture
  hardcoded as an architecture constant.
- **Model-as-oracle verification** — HARDEN's pass criterion is exit code 0;
  VERIFY's is the model emitting the literal string `VERIFIED:` (parsed by
  substring offset); task completion is self-reported via
  `mark_task_complete`. No independent verification anywhere.
- **Prompt style** — six large ALL-CAPS imperative prompt blocks with
  hardcoded shell incantations, duplicated rules, and a stack-selection ladder
  keyed to the timebox. Current models follow instructions literally; this
  style now *causes* over-triggering and rigidity.
- **Docker sandbox** is Python-only (`python:3.12-slim` — no Node/git/curl,
  though planner and prompts assume all three), has unrestricted network,
  unvalidated path interpolation into shell strings, a content-corrupting
  heredoc write, and leaves git tools + the post-run server launch running on
  the host.

### 2.4 Correctness bugs (independent of staleness)

1. OpenAI `stop_reason` mismatch (loop never terminates normally) —
   `agents.py`, `phases.py`.
2. Docker heredoc write corrupts backslashes and can be truncated by content.
3. Docker file tools: path traversal / command injection inside the container.
4. Git tools operate on the (empty) host workspace during `--sandbox` runs.
5. Git subprocesses inherit the unsanitized host env (API keys included).
6. `advance_phase` never called for BUILD/VERIFY → the event log stamps the
   wrong phase on nearly every tool event.
7. Planner catches bare `Exception` → transient 529s become "invalid plan".
8. Setup tasks marked complete on "no exception", not verified output.
9. Post-run `_run_server` blocks on the host indefinitely — contradicting the
   hard-deadline guarantee.
10. Event-log redaction called with an empty secrets map for non-tool events.

### 2.5 Dead weight

`--tui` flag + entire Textual TUI (never imported), `stream()` on both
providers (never called), panic mode (unreachable), `cost_summary()`
(duplicated inline), `risk_policy`/`repo_mode`/`secrets:` grants (parsed, never
read), `Capability.DOCKER`/`NET_HTTP` (no consumer), plus README/CLAUDE.md
drift (phase budgets, phase list, TUI, panic mode) and 5 open Dependabot
alerts.

## 3. Strategic decision

**Rebuild the execution layer on the Claude Agent SDK; keep NoScope as the
product layer.**

The Claude Agent SDK (`claude-agent-sdk`) ships the harness NoScope hand-rolls
today: the agent loop, built-in Read/Write/**Edit**/Bash/Glob/Grep/WebSearch
tools, context management/compaction, subagents, hooks, and a permission
system. Rebuilding on it means:

- NoScope's ~2,000 lines of loop/tools/provider plumbing (agents.py,
  supervisor worker loop, tools/filesystem, tools/shell duplication, the
  streaming dead code) shrink to configuration + hooks.
- We inherit an edit tool, search tools, and context management on day one —
  three of our biggest capability gaps — for free.
- Every future model/API change (new model IDs, thinking semantics, caching)
  is absorbed by the SDK instead of by us. The last six months proved we do
  not keep up by hand.

**What NoScope keeps and owns (the differentiated layer):**

- `Deadline` — wall-clock timebox with phase budgets, driving cancellation.
- **Capability model** → reimplemented as SDK permission hooks (approve/deny
  per tool class), which is strictly stronger than today's coarse gate.
- **Spec format + contract** — unchanged; it holds up well.
- **Event log** → fed from SDK hooks, with per-call token/cost attribution
  added (today we only log a grand total).
- **Phases + handoff report** — the run lifecycle and the always-produced
  artifact/report remain the product.

**Trade-off accepted:** the SDK is Anthropic-only. The OpenAI provider is
demoted from "co-equal backend" to "not in v0.2" (see §5). Keeping true
provider parity means continuing to own a bespoke harness forever — that cost
is what killed the last six months. If provider-agnosticism turns out to be a
real adoption requirement, the fallback design is the Anthropic *tool runner*
+ OpenAI Responses API behind our existing provider protocol — but not for the
next release.

## 4. Roadmap

### Phase 0 — Stop the bleeding (small, immediate; keeps current architecture)

Goal: `main` runs again on today's APIs. No re-architecture.

- [ ] Default models: `claude-sonnet-5` (Anthropic), current GPT-5.6 tier if
      the OpenAI path is exercised at all. Make model configurable via
      `NOSCOPE_DEFAULT_MODEL`.
- [ ] Fix the OpenAI `stop_reason` loop-exit bug (map `"stop"` → `"end_turn"`
      at the provider boundary) — or gate the OpenAI provider behind a warning.
- [ ] Replace the duplicated 2025 pricing tables with a single table for
      current models; label unknown models "unknown" instead of billing them
      at Sonnet-4 rates.
- [ ] Fix the phase-stamping bug (`advance_phase` for BUILD/VERIFY).
- [ ] Narrow the planner's `except Exception`; let API errors surface as API
      errors.
- [ ] Sanitize env for git subprocesses (reuse `build_execution_env`).
- [ ] Bound `_run_server` (opt-in flag + timeout) so the hard-deadline promise
      is honest.
- [ ] Resolve the 5 Dependabot alerts; pin SDK minimums in `pyproject.toml`.
- [ ] Docs truth pass: phase budgets, phase list, remove `--tui` and panic-mode
      claims (and the dead code behind them), update SECURITY_AUDIT numbers.

### Phase 1 — Modernize the LLM usage (still current architecture)

Goal: the existing loop stops leaving free wins on the table. Everything here
survives the Phase 2 re-architecture (planner, prompts, verification design).

- [ ] Planner: structured outputs (`output_config.format` from
      `PlanOutput.model_json_schema()`), adaptive thinking + `effort: high`.
      Delete the JSON-repair retry loop.
- [ ] Prompt caching: `cache_control` on system prompts + tool schemas;
      surface cache-read/write tokens in the cost summary.
- [ ] Prompt rewrite pass: de-shout, de-duplicate, drop the hardcoded stack
      ladder and shell incantations; move to goal + constraints style (current
      models follow instructions literally — the 2025 style now over-triggers).
- [ ] Per-role models: cheap fast model (Haiku 4.5) for the audit agent and
      handoff report; default model for build; high-effort for planning.
- [ ] Token accounting: per-call usage into the event log; per-agent
      attribution in the final summary.
- [ ] Use SDK-native retries (`max_retries`, honors `Retry-After`) and delete
      the hand-rolled retry code. Delete the dead `stream()` path.

### Phase 2 — Re-architecture on the Claude Agent SDK

Goal: NoScope becomes the timebox/capability/contract harness around SDK-run
agents.

- [ ] Replace `BuildAgent`/tool dispatcher internals with `claude-agent-sdk`
      `query()` sessions: one session per work stream, system prompt from our
      phase templates, cwd = workspace.
- [ ] Capabilities → SDK permission hooks: `WORKSPACE_RW`/`SHELL_EXEC`/`GIT`
      map to tool allow/deny lists; `--danger` maps to permissive mode; the
      REQUEST phase approves the hook policy instead of a homegrown gate.
- [ ] Deadline → session control: cancel sessions at phase boundaries; inject
      time-remaining via hook-driven system reminders instead of fake user
      turns.
- [ ] Event log → SDK hooks (tool-use pre/post), keeping our JSONL format and
      redaction, now with correct phases and per-call usage.
- [ ] Supervisor keeps task partitioning (union-find + topo sort — recent, keep)
      but delegates execution; audit agent becomes a real checker (run
      lint/build/import checks via a cheap model session, feed findings back —
      the AuditFeed mechanism from Aug 2026 carries over).
- [ ] Retire: hand-rolled filesystem/shell tools (SDK built-ins + our
      permission hooks), the custom Docker tool layer (prefer: run the whole
      NoScope process inside a container; document `--sandbox` as
      containerized-run rather than per-tool `docker cp` choreography).
- [ ] Decide OpenAI provider fate explicitly in CHANGELOG (park with warning,
      or delete).

### Phase 3 — Verification that actually verifies

Goal: replace model-as-oracle with independent checks — this is the credibility
core of "you always get a runnable artifact."

- [ ] Acceptance checks: keep `cmd:` checks but add expected-output assertions
      (`cmd: ... expect: <substring|status>`); run them from code, not prompts.
- [ ] Replace the `VERIFIED:` sentinel-string protocol with a structured
      verdict (structured output schema: status, evidence, failures) from a
      fresh-context verifier session that did not build the code.
- [ ] HARDEN gains a bounded repair loop (budgeted by the phase deadline):
      failing check → targeted fix session → re-run check.
- [ ] Task completion requires evidence: `mark_task_complete` accepts a claim
      that the audit checker can spot-verify against the workspace.
- [ ] Adopt API task budgets (`output_config.task_budget`) so agents pace to a
      token budget the same way the Deadline paces wall-clock — the two
      budgets are the product's core promise, now enforced in both dimensions.

### Phase 4 — Release readiness

- [ ] Positioning rewrite in README: "timebox as spend cap + capability gating
      + auditable one-shot runs" vs. interactive agents (Claude Code) and
      open-ended cloud agents (Devin) — with honest "when NOT to use NoScope".
- [ ] Real cost reporting (cached vs. uncached, per phase, per agent) — make
      the cost-control pitch demonstrable in the handoff report.
- [ ] Fresh example specs that exercise current stacks; CI smoke test that
      runs a 3-minute spec end-to-end against the live API (nightly, keyed).
- [ ] Version 0.2.0, CHANGELOG, and a release checklist.

## 5. Explicit cuts

| Item | Decision |
|---|---|
| Textual TUI (`ui/tui.py`, `--tui`) | Delete. Never wired up; Rich console is sufficient. |
| Panic mode | Delete or implement in the Deadline→session cancellation path; no half-state. |
| OpenAI provider | Park in Phase 2 (warning + known-broken list) — revisit only if adoption demands it. |
| Per-tool Docker choreography (`tools/docker.py`) | Replace with containerized-process sandboxing. |
| `risk_policy`, `repo_mode`, `secrets:` grants | Delete from spec schema until a phase actually consumes them. |
| Hand-rolled retries, `stream()` dead code, duplicate pricing tables | Delete in Phases 0–1. |

## 6. Success criteria for "worth releasing"

1. `uv run noscope run --spec examples/todo-api.md --time 10m --yes` completes
   on current models, under budget, producing a passing artifact — in CI.
2. The handoff report shows real cost (with cache savings) and a verification
   verdict backed by executed checks, not model assertion.
3. A reader of the README can say in one sentence why they'd use NoScope
   instead of Claude Code or a cloud agent.
4. No hardcoded model IDs outside one config module; SDK upgrades don't
   require touching agent code.
