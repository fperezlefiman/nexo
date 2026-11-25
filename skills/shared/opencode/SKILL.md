---
name: opencode
description: "Delegate coding to OpenCode CLI (features, PR review)."
version: 1.4.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Coding-Agent, OpenCode, Autonomous, Refactoring, Code-Review]
    related_skills: [hermes-agent]
---

# OpenCode CLI

Use [OpenCode](https://opencode.ai) as an autonomous coding worker orchestrated by Hermes terminal/process tools. OpenCode is a provider-agnostic, open-source AI coding agent with a TUI and CLI.

## When to Use

- User explicitly asks to use OpenCode
- You want an external coding agent to implement/refactor/review code
- You need long-running coding sessions with progress checks
- You want parallel task execution in isolated workdirs/worktrees

## Prerequisites

- OpenCode installed. Two tracks exist (Sept 2026):
  - **v1 (legacy):** `npm i -g opencode-ai@latest` — bin `opencode`, config `~/.config/opencode/opencode.jsonc`
  - **v2 (current):** `curl -fsSL https://opencode.ai/v2/install | bash` or `npm i -g @opencode/cli` or `brew install anomalyco/tap/opencode-v2`
    - The curl installer puts the binary in `~/.opencode/bin/opencode` and adds an `opencode2` shim. It also appends `export PATH=$HOME/.opencode/bin:$PATH` to the first existing shell rc file.
    - **Pitfall:** run the installer with an explicit `HOME=/home/<user>` — inside Hermes, `$HOME` points at the *profile* home (`~/.local/share/nexo/<p>/home`), so a bare `curl … | bash` installs to the wrong place.
    - On the homelab the v1→v2 migration is **complete** (Sept 30 2026): v2 is the only install, exposed as `opencode` via `/usr/local/bin/opencode` → `~/.opencode/bin/opencode`. The v1 `opencode-ai` npm package is uninstalled.
    - v2 runs a **managed background service** (`opencode serve --service`, default port 49374). Reload config/plugins with `opencode service restart`.
- Auth configured: `opencode auth login` or set provider env vars (OPENROUTER_API_KEY, etc.)
- Verify: `opencode auth list` should show at least one provider
- Git repository for code tasks (recommended)
- `pty=true` for interactive TUI sessions

## Binary Resolution (Important)

Shell environments may resolve different OpenCode binaries. If behavior differs between your terminal and Hermes, check:

```
terminal(command="which -a opencode")
terminal(command="opencode --version")
```

If needed, pin an explicit binary path:

```
terminal(command="$HOME/.opencode/bin/opencode run '...'", workdir="~/project", pty=true)
```

## OpenCode V2 specifics (Sept 2026)

V1 and V2 are different majors with **three breaking changes** (see <https://opencode.ai/v2/docs/migrate-v1/>): plugins use a new API, the server API/clients changed, and TUI config moved to a single global `cli.json` (auto-migrated). Everything else — config locations, `opencode.json(c)`, agents/commands/skills files, MCP servers, permissions — keeps working; V2 normalizes V1 config in memory.

**Native V2 config keys** (optional conversion): `plugin` → `plugins`; `mcp.<name>` → `mcp.servers.<name>` with `enabled` → `disabled`; `permission` (grouped) → ordered `permissions: [{action, resource, effect}]` (`bash`→`shell`, `write`/`patch`→`edit`, `task`→`subagent`); `agent`→`agents` (`prompt`→`system`, `disable`→`disabled`, `model`+`variant` → `model#variant`); `command`→`commands`; `snapshot`→`snapshots`; `attachment`→`media`.

**Verifying & hardening V2 config:**

- `opencode debug config` prints the **merged** config as JSON (one entry per config source: `{type, path, info}`). This is how you confirm your keys were *actually parsed* — e.g. `agents.explore.model` arrives resolved as `{"providerID": "...", "model": "..."}` — instead of guessing from behaviour.
- Ordered `permissions: [{action, resource, effect}]`. **Last matching rule wins**; lower-priority config loads first, then global, then agent rules. Actions: `read`, `edit`, `glob`, `grep`, `shell`, `subagent`, `skill`, `question`, `webfetch`, `websearch`, `external_directory`, `execute` (Code Mode), and `<server>_<tool>` for MCP tools. `resource` uses whole-value wildcards where `*` also crosses `/`. If nothing matches ⇒ `ask`.
- **A `deny` in `permissions` can be overridden by a later (agent-level) rule.** For a guard no project/agent can lift, use `experimental.policies: [{"action":"permission","resource":"read:*.env","effect":"deny"}]` — binary, never prompts, only ever tightens. A blocked read surfaces as `Error: Blocked by configuration policy` (agent-side: `permission.rejected: Blocked by configuration policy`).
- `references: {"alias": {"path": "~/dir" | "repository": "org/repo", "branch"?, "description"?}}` = named access to directories **outside** the project. String shorthand counts as a local path only when it starts with `.`, `/` or `~`.
- **Websearch is a client-side tool, not a model capability.** It needs one of four search providers (`exa`, `firecrawl`, `parallel`, `tavily`), via env var (`TAVILY_API_KEY`, …) or an account via `/connect` — **but a subscription provider can bundle them**: observed with OpenCode Go serving Tavily and Firecrawl under a bare `env -i` with no local key, so `opencode auth list` showing only the model provider does NOT mean search is unavailable. Set `"websearch": {"provider": "random"}` to rotate providers on HTTP 429/503 instead of prompting. If the search tool itself fails, a Code Mode `fetch(...)` to the upstream API is a working fallback (models often do this unprompted).
- Other escape hatches: `formatter: true`, `watcher: {ignore: [...]}`, `tool_output: {max_lines, max_bytes}`, `compaction: {keep:{tokens}}`. `warming` is **off** by default and costs tokens — leave it off.
- **V2 has no default model.** With no root `"model"` set, new sessions fall back to the *newest available* model (observed: a free preview model) rather than the user's favourite. Pin `"model": "<provider>/<model>"` in the global config (the root model does **not** retain a `#variant`).

**Subagents (V2):**

- Define them as Markdown at `~/.config/opencode/agents/<name>.md` (global) or `.opencode/agents/<name>.md` (project); frontmatter = config fields, body = the agent's `system` prompt. Nested paths become the ID (`team/reviewer`). JSONC under `agents:` in any config file works too.
- `mode`: `primary` (default for custom), `subagent` (only via the `subagent` tool), `all`. Built-ins: `build`/`plan` (primary), `general`/`explore` (subagent). `default_agent` picks the primary for new sessions (must exist and be primary-capable).
- Read-only reviewer recipe (verified): deny `edit:*` and `shell:*`, then **after** those, allow `shell: "git status*"`, `"git diff*"`, `"git log*"`, `"git show*"`. It reports findings with file:line, writes nothing.
- **Gotcha — compound commands:** a chained command (`git status && echo --- && git diff`) is scanned as *several* resources and **any matching `deny` kills the whole command** — the read-only agent gets nothing and reports "no shell available". Tell it in its prompt to run **one command at a time, no `&&`/`;`/`|`**. Do **not** "fix" this by allowing `echo *`: that permits `echo data > file`, i.e. arbitrary writes.

**Model catalog: `opencode-go/*` vs `opencode/*` (and the non-chat trap):**

- `opencode models` lists what's actually selectable. `opencode-go/<x>` = the **OpenCode Go** subscription (Grok, GLM, GPT-5.6/6 Luna, Kimi, LongCat, MiMo, MiniMax, Muse Spark, Qwen, DeepSeek V4/V4.1). `opencode/<x>` = **OpenCode Console** (pay-as-you-go) models — the free/stealth ones (`big-pickle`, `space-bunny-free`, `longcat-2.5-preview-free`, `nemotron-*-free`, …).
- The authoritative catalog + prices live at `https://opencode.ai/v2/docs/console/models/` (and the Go list at `.../console/go/`) — crawl `/v2/llms.txt` for the page index, or the full list via `curl https://opencode.ai/zen/v1/models`.
- **Trap — not every catalog entry is a chat model.** `Jev 1.13` (`jev-1.13`, TypeSafe AI "System One", free variant `jev-1.13-free`) takes a `state` + typed questions (`noul` = yes/no, `choice`, `score`) and returns values/probabilities instead of text, on a separate `https://opencode.ai/zen/v1/systemone` endpoint — it is **not** in the Go plan and does **not** appear in `opencode models`, so it cannot be an agent's model. Before recommending a model by name, confirm it's listed *and* uses `/chat/completions`.

**Plugins must be rewritten** — V1 implementations do not run. V2 shape:

```ts
import { Plugin } from "@opencode/plugin"   // npm i @opencode/plugin@<matching version>

export default Plugin.define({
  id: "myplugin",
  async setup(ctx) {
    const controller = new AbortController()
    void (async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        // event.type, event.data
      }
    })()
    await ctx.permission.hook("evaluate", (e) => { /* e.action, e.effect, e.message */ })
    await ctx.tool.hook("execute.before", (e) => { /* e.tool, e.input */ })
    return () => controller.abort()   // cleanup
  },
})
```

Key V1→V2 event/hook renames:
- `event` hook → `ctx.event.subscribe({signal})` (async iterable, not a callback).
- `session.idle` → **`session.execution.succeeded`** (+ `.started`); the event payload is under `event.data` (not `.properties`).
- Child/subagent sessions: `session.created` data has `parentID` (no nested `info`), and their `session.execution.succeeded` also fires — filter them out to notify only on top-level turn end.
- `permission.ask` → `ctx.permission.hook("evaluate", e => ...)` with `e.effect === "ask"`.
- `tool.execute.before` → `ctx.tool.hook("execute.before", e => ...)`.

**Pitfall — plugins load twice.** V2 instantiates a plugin in more than one context (global + project), so each event reaches your handler **twice** and module-level state is *not* shared. Deduplicate with an atomic cross-process guard (e.g. `fs.mkdirSync("/tmp/lock-<key>")` in a try/catch, removed after a short timeout) or you'll send duplicate notifications.

**Asking the user / waiting for input.** In V2 the model can call the built-in `question` tool, and the terminal surfaces prompts as **forms**. The reliable signal to notify "OpenCode is waiting for you" is the **`form.created`** event (`data.form`): `form.metadata.kind === "question"` for the `question` tool, `form.fields[].description`/`.title` carry the question text, `form.fields[].options[].label` the choices. Any other `form.*` event (`form.cancelled`, …) means the form was dismissed/answered. `form.created` is emitted *before* the tool actually runs, so it covers both permission prompts and explicit questions. Note: in headless `opencode run` the form is auto-dismissed immediately (so `form.cancelled` follows at once), unlike the TUI where it stays until answered.

**Debugging:** `opencode --standalone` runs a private in-process server (inherits your env — handy for env-gated test modes); otherwise CLI commands talk to the shared service on port 49374.

## One-Shot Tasks

Use `opencode run` for bounded, non-interactive tasks:

```
terminal(command="opencode run 'Add retry logic to API calls and update tests'", workdir="~/project")
```

Attach context files with `-f`:

```
terminal(command="opencode run 'Review this config for security issues' -f config.yaml -f .env.example", workdir="~/project")
```

Show model thinking with `--thinking`:

```
terminal(command="opencode run 'Debug why tests fail in CI' --thinking", workdir="~/project")
```

Force a specific model:

```
terminal(command="opencode run 'Refactor auth module' --model openrouter/anthropic/claude-sonnet-4", workdir="~/project")
```

## Interactive Sessions (Background)

For iterative work requiring multiple exchanges, start the TUI in background:

```
terminal(command="opencode", workdir="~/project", background=true, pty=true)
# Returns session_id

# Send a prompt
process(action="submit", session_id="<id>", data="Implement OAuth refresh flow and add tests")

# Monitor progress
process(action="poll", session_id="<id>")
process(action="log", session_id="<id>")

# Send follow-up input
process(action="submit", session_id="<id>", data="Now add error handling for token expiry")

# Exit cleanly — Ctrl+C
process(action="write", session_id="<id>", data="\x03")
# Or just kill the process
process(action="kill", session_id="<id>")
```

**Important:** Do NOT use `/exit` — it is not a valid OpenCode command and will open an agent selector dialog instead. Use Ctrl+C (`\x03`) or `process(action="kill")` to exit.

### TUI Keybindings

| Key | Action |
|-----|--------|
| `Enter` | Submit message (press twice if needed) |
| `Tab` | Switch between agents (build/plan) |
| `Ctrl+P` | Open command palette |
| `Ctrl+X L` | Switch session |
| `Ctrl+X M` | Switch model |
| `Ctrl+X N` | New session |
| `Ctrl+X E` | Open editor |
| `Ctrl+C` | Exit OpenCode |

### Resuming Sessions

After exiting, OpenCode prints a session ID. Resume with:

```
terminal(command="opencode -c", workdir="~/project", background=true, pty=true)  # Continue last session
terminal(command="opencode -s ses_abc123", workdir="~/project", background=true, pty=true)  # Specific session
```

## Common Flags

| Flag | Use |
|------|-----|
| `run 'prompt'` | One-shot execution and exit |
| `--continue` / `-c` | Continue the last OpenCode session |
| `--session <id>` / `-s` | Continue a specific session |
| `--agent <name>` | Choose OpenCode agent (build or plan) |
| `--model provider/model` | Force specific model |
| `--format json` | Machine-readable output/events |
| `--file <path>` / `-f` | Attach file(s) to the message |
| `--thinking` | Show model thinking blocks |
| `--variant <level>` | Reasoning effort (high, max, minimal) |
| `--title <name>` | Name the session |
| `--attach <url>` | Connect to a running opencode server |

## Procedure

1. Verify tool readiness:
   - `terminal(command="opencode --version")`
   - `terminal(command="opencode auth list")`
2. For bounded tasks, use `opencode run '...'` (no pty needed).
3. For iterative tasks, start `opencode` with `background=true, pty=true`.
4. Monitor long tasks with `process(action="poll"|"log")`.
5. If OpenCode asks for input, respond via `process(action="submit", ...)`.
6. Exit with `process(action="write", data="\x03")` or `process(action="kill")`.
7. Summarize file changes, test results, and next steps back to user.

## PR Review Workflow

OpenCode has a built-in PR command:

```
terminal(command="opencode pr 42", workdir="~/project", pty=true)
```

Or review in a temporary clone for isolation:

```
terminal(command="REVIEW=$(mktemp -d) && git clone https://github.com/user/repo.git $REVIEW && cd $REVIEW && opencode run 'Review this PR vs main. Report bugs, security risks, test gaps, and style issues.' -f $(git diff origin/main --name-only | head -20 | tr '\n' ' ')", pty=true)
```

## Parallel Work Pattern

Use separate workdirs/worktrees to avoid collisions:

```
terminal(command="opencode run 'Fix issue #101 and commit'", workdir="/tmp/issue-101", background=true, pty=true)
terminal(command="opencode run 'Add parser regression tests and commit'", workdir="/tmp/issue-102", background=true, pty=true)
process(action="list")
```

## Session & Cost Management

List past sessions:

```
terminal(command="opencode session list")
```

Check token usage and costs:

```
terminal(command="opencode stats")
terminal(command="opencode stats --days 7 --models anthropic/claude-sonnet-4")
```

## Pitfalls

- Interactive `opencode` (TUI) sessions require `pty=true`. The `opencode run` command does NOT need pty.
- **v2 runs a managed background service** (`opencode serve --service`, default port 49374). A stale service holds the port → other invocations fail with `Managed service port ... is already in use`. Fix: kill the process or `opencode service set port <port>`.
- **v1 and v2 share state**: `~/.local/share/opencode/opencode.db` (sessions/cost) and `~/.local/state/opencode/`. v2 will write to the same DB as v1 — after the first v2 run, re-verify v1 still works.
- **Hermes HOME pitfall**: inside Hermes `$HOME` = `~/.local/share/nexo/<p>/home`, so CLI runs create a *separate, empty* opencode DB there and can emit bogus errors like `Database is not empty and has no session table`. Always reproduce with the user's real home: `env -i HOME=/home/<user> TERM=xterm PATH=/usr/local/bin:/usr/bin:/bin bash -lc '<cmd>'`.
- `/exit` is NOT a valid command — it opens an agent selector. Use Ctrl+C to exit the TUI.
- PATH mismatch can select the wrong OpenCode binary/model config.
- If OpenCode appears stuck, inspect logs before killing:
  - `process(action="log", session_id="<id>")`
- Avoid sharing one working directory across parallel OpenCode sessions.
- Enter may need to be pressed twice to submit in the TUI (once to finalize text, once to send).

## Verification

Smoke test:

```
terminal(command="opencode run 'Respond with exactly: OPENCODE_SMOKE_OK'")
```

Success criteria:
- Output includes `OPENCODE_SMOKE_OK`
- Command exits without provider/model errors
- For code tasks: expected files changed and tests pass

## Rules

1. Prefer `opencode run` for one-shot automation — it's simpler and doesn't need pty.
2. Use interactive background mode only when iteration is needed.
3. Always scope OpenCode sessions to a single repo/workdir.
4. For long tasks, provide progress updates from `process` logs.
5. Report concrete outcomes (files changed, tests, remaining risks).
6. Exit interactive sessions with Ctrl+C or kill, never `/exit`.
