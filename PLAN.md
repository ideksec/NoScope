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

**Build out NoScope's own agent harness. Do not wrap the Claude Agent SDK.**

NoScope's purpose is to be a compelling, self-contained demonstration of
time-boxed autonomous AI — a way to learn agent orchestration, show it off, and
run quick "sculpt" POCs. That purpose is the deciding factor.

The rejected alternative was to rebuild the execution layer on the Claude Agent
SDK (Claude Code packaged as a library). It would have made builds more reliable
with less code to maintain — but the Claude Agent SDK *is* Claude Code, so
NoScope would become "Claude Code with a timer and a permission prompt." For a
tool whose value is the orchestration itself, that guts the point: if it's a
thin wrapper, people can just use Claude Code. It's also Anthropic-only, which
would kill the provider-agnostic story.

So the harness stays hand-rolled and owned — the agent loop, the tool registry,
the multi-agent supervisor, the provider abstraction. The work ahead is to make
that harness genuinely good rather than to outsource it:

- Close the capability gaps that make builds unreliable (editing, search,
  real verification) with our own well-tested tools.
- Keep the provider abstraction (Anthropic + OpenAI) as a real feature.
- Own the model/API churn deliberately — Phase 1 already showed it's tractable
  (structured outputs, caching, thinking, SDK-native retries) when the LLM
  layer is small and well-factored.

**What this costs us, honestly:** we keep maintaining the harness, and we chase
model/API changes ourselves (the thing that rotted the project the first time).
Phase 1's changes are the mitigation — a thin, well-tested LLM layer where a
model swap is a one-line default and the modern features degrade gracefully.
The differentiators from §1 (timebox engine, capability/approval model, spec
contract, event log, guaranteed handoff) all stay, and now they sit on a
harness we can actually demo and explain.

## 4. Roadmap

### Phase 0 — Stop the bleeding (small, immediate; keeps current architecture)

Goal: `main` runs again on today's APIs. No re-architecture.

- [x] Default models: `claude-sonnet-5` (Anthropic), current GPT-5.6 tier if
      the OpenAI path is exercised at all. Make model configurable via
      `NOSCOPE_DEFAULT_MODEL`.
- [x] Fix the OpenAI `stop_reason` loop-exit bug (map `"stop"` → `"end_turn"`
      at the provider boundary) — or gate the OpenAI provider behind a warning.
- [x] Replace the duplicated 2025 pricing tables with a single table for
      current models; label unknown models "unknown" instead of billing them
      at Sonnet-4 rates.
- [x] Fix the phase-stamping bug (`advance_phase` for BUILD/VERIFY).
- [x] Narrow the planner's `except Exception`; let API errors surface as API
      errors.
- [x] Sanitize env for git subprocesses (reuse `build_execution_env`).
- [x] Bound `_run_server` (opt-in flag + timeout) so the hard-deadline promise
      is honest.
- [x] Resolve the 5 Dependabot alerts; pin SDK minimums in `pyproject.toml`.
- [x] Docs truth pass: phase budgets, phase list, remove `--tui` and panic-mode
      claims (and the dead code behind them), update SECURITY_AUDIT numbers.

### Phase 1 — Modernize the LLM usage (still current architecture)

Goal: the existing loop stops leaving free wins on the table. Everything here
survives the Phase 2 re-architecture (planner, prompts, verification design).

- [x] Planner: structured outputs (`output_config.format` from
      `PlanOutput.model_json_schema()`, prepared with `additionalProperties:
      false`), adaptive thinking + `effort: high`. JSON-repair loop reduced to
      a single corrective re-ask.
- [~] Prompt caching: `cache_control` on system prompts + tool schemas — done.
      Surfacing cache-read/write tokens in the cost summary is deferred to
      Phase 4 (real cost reporting; needs `Usage` to carry cache tokens).
- [x] Prompt rewrite pass: VERIFY and planner prompts moved to goal +
      constraints style; kept timebox-scaled ambition as genuine product logic.
      (Supervisor setup/worker prompts still to de-shout.)
- [~] Per-role models: handoff report runs on the fast model (Haiku 4.5) at
      low effort; planner runs at high effort. Audit agent doesn't call an LLM
      yet — its fast-model use lands with the real checker in Phase 3.
- [ ] Token accounting: per-call usage into the event log; per-agent
      attribution in the final summary. (Deferred — Phase 2 restructures the
      loop; folds into Phase 4 cost reporting.)
- [x] SDK-native retries (`max_retries`, honors `Retry-After`, covers
      429/5xx/timeouts) replace the hand-rolled loop. Dead `stream()` path removed.

### Phase 2 — Build out the owned harness (capability gaps)

Goal: give NoScope's own agents the tools a credible harness needs, so builds
are reliable enough to demo. Every tool is ours, tested, and capability-gated.

- [x] `edit_file` — exact-string replacement with a uniqueness guard and a
      returned diff, replacing whole-file rewrites for edits. Mutations now run
      sequentially so same-file edits can't race.
- [ ] Search + discovery tools: `search_files` (regex/grep across the tree) and
      `find_files` (glob) so agents stop discovering code one `list_directory`
      at a time.
- [ ] Partial reads: `read_file` gains optional line `offset`/`limit` so large
      files don't dump whole into context.
- [ ] Docker sandbox correctness (from §2.3/§2.4): fix the heredoc write
      corruption and path injection, validate paths, use an image with the
      toolchain the plan actually needs (or document Python-only), and keep git
      operating on the real tree. Or replace per-tool `docker cp` choreography
      with running the whole NoScope process in a container.
- [ ] Context management: cap/summarize the ever-growing agent history and the
      50 KB shell dumps so long runs don't die on context length.
- [ ] Make `MAX_WORKERS` configurable rather than a hardcoded rate-limit posture.

### Phase 3 — Verification that actually verifies

Goal: replace model-as-oracle with independent checks — this is the credibility
core of "you always get a runnable artifact," and the most demo-visible upgrade.

- [ ] Acceptance checks: keep `cmd:` checks but add expected-output assertions
      (`cmd: ... expect: <substring|status>`); run them from code, not prompts.
- [ ] Replace the `VERIFIED:` sentinel-string protocol with a structured
      verdict (structured output schema: status, evidence, failures) from a
      fresh-context verifier that did not build the code. (Structured outputs
      from Phase 1 make this clean.)
- [ ] HARDEN gains a bounded repair loop (budgeted by the phase deadline):
      failing check → targeted fix session → re-run check.
- [ ] Turn the audit agent into a real checker: run lint/build/import checks
      via the cheap fast model and feed findings back through the existing
      `AuditFeed`; task completion accepts a claim the checker can spot-verify.
- [ ] Adopt API task budgets (`output_config.task_budget`) so agents pace to a
      token budget the same way the `Deadline` paces wall-clock — the two
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
| Textual TUI (`ui/tui.py`, `--tui`) | Deleted in Phase 0. Never wired up; Rich console is sufficient. |
| Panic mode | Deleted in Phase 0. Revisit only if the timebox UX needs an explicit end-game state. |
| OpenAI provider | **Keep** — provider-agnosticism is a real feature under the owned-harness direction (§3). Modernized in Phase 1. |
| Per-tool Docker choreography (`tools/docker.py`) | Fix in place (Phase 2) or replace with containerized-process sandboxing; do not ship it broken. |
| `risk_policy`, `repo_mode`, `secrets:` grants | Delete from spec schema until a phase actually consumes them. |
| Hand-rolled retries, `stream()` dead code, duplicate pricing tables | Deleted in Phases 0–1. |

## 6. Success criteria for "worth releasing"

1. `uv run noscope run --spec examples/todo-api.md --time 10m --yes` completes
   on current models, under budget, producing a passing artifact — in CI.
2. The handoff report shows real cost (with cache savings) and a verification
   verdict backed by executed checks, not model assertion.
3. A reader of the README can say in one sentence why they'd use NoScope
   instead of Claude Code or a cloud agent.
4. No hardcoded model IDs outside one config module; SDK upgrades don't
   require touching agent code.
