# Software factory

A Claude Code plugin for feature delivery in any repository: spec → isolated
build → evidence → independent review → approval, with an isolated observer
that checks the user's request against the repository itself, a removal audit
that stops agents deleting code without knowing why it was there, and a hook
that blocks commands which throw away work.

The plugin holds the workflow, the roles and the code. Each project holds its
own rules (`.factory/profile.md`) and its own job state (`.factory/jobs/`).

## Install

This repository is both the plugin and its marketplace.

```
/plugin marketplace add yugandhar-g/claude-factory
/plugin install factory@claude-factory
```

From a shell: `claude plugin marketplace add yugandhar-g/claude-factory` then
`claude plugin install factory@claude-factory --scope user` (or `--scope
project` to record it in the project's `.claude/settings.json` for the team).
For local development: `claude --plugin-dir /path/to/claude-factory`.

The repository is private (see Licensing), so installing needs GitHub access
to it on that machine.

## Use in a project

1. `/factory` once in the project, or run `bin/factory init`: writes
   `.factory/profile.md` from the template and gitignores job state and
   worktrees. It never overwrites.
2. Fill the profile and **commit it**. Each agent applies the "All roles"
   section plus the section for its role. This is where the project's
   invariants, test commands, forbidden live services and reviewer checklists
   go. **Review quality on a project is bounded by its profile**: the generic
   roles check generic things. The harness reads the profile from the job's
   base commit, so no agent can change the rules mid-job; an uncommitted
   profile falls back to a per-job snapshot, with a warning. `run` refuses to
   start until job state is gitignored, and warns about unfilled placeholders
   or headings that match no role.
3. Optional `.factory/config.json`:

   ```json
   { "branch_prefix": "factory/", "worktrees_dir": ".worktrees", "state_dir": ".factory" }
   ```

4. `/factory <feature, in your words>`.

## What's inside

| Path | What it is |
| --- | --- |
| `skills/factory`, `skills/review-loop` | The orchestrator workflow and review rules |
| `skills/new-feature`, `code-structure`, `evidence-driven-testing`, `before-and-after`, `greploop`, `greploop-apps`, `unslop` | Vendored upstream skills, byte-identical to the pinned commit in `upstream-manifest.json` |
| `agents/` | spec-writer, builder, five reviewers (code, security, ux, ui, data), approver, observer (Opus 5.5). Invoked as `factory:<role>` |
| `hooks/hooks.json` | PreToolUse work-loss guard on every Bash call in every project where the plugin is enabled (it blocks bulk `rm -r` of non-scratch paths and history-discarding git commands; enable the plugin per project if that is too broad) |
| `factory/scripts/` | `state.py` (job state and gates), `guard.py` (removal audit), `destructive_guard.py` (the hook), `serve.py` (loopback dashboard), `home.py` (project, config, profile, state location) |
| `factory/harness/` | The same graph in code on the Claude Agent SDK, with each role's tool policy enforced on every tool call |
| `factory/codex.py` | Generates the Codex form for a project |
| `bin/factory` | CLI for all of the above |
| `templates/profile.md` | Starting profile |

## The harness

```
bin/factory setup                    # once per machine: claude-agent-sdk into ~/.factory/venv
bin/factory run "<task, verbatim>"   # from inside the project
bin/factory resume <job> | status <job>
```

Each agent runs as its own SDK session behind a gate that allows only its
listed tools, lets it write only its own artifacts, keeps the observer away
from every other agent's output (and the workers away from the observer's),
and gives every role except the builder a read-only shell. The orchestrator is
code, so stage order, wait-for-all reviews, the observer loop, the removal
audit, the budget (`--budget-usd`, default $25) and pass limits cannot be
skipped. Each role's profile section is appended to its instructions in code.
Every run is logged with cost and transcript in the job's `runs.jsonl`.

Known limit, carried over from the original: the shell and search checks are
heuristic. A relative path or a Grep/Glob over an ancestor of the job store
could still reach artifacts a role should not see; the default `.factory`
state directory being hidden and gitignored keeps ripgrep away from it, and
the profile's integrity does not depend on these checks (it comes from Git).

## Codex

Codex does not load Claude Code plugins. From this checkout:

```
bin/factory codex install /path/to/project
```

writes `.agents/skills/`, `.codex/agents/*.toml` and `.codex/hooks.json` into
the project from the same sources, with absolute paths to this checkout.
Generated files are marked; anything without the marker is left alone unless
you pass `--force`. Rerun after updating the plugin.

## Updating

`plugin.json` pins `version`, so installed copies update only when it changes:
bump it with each release, tag `vX.Y.Z`, and users run `/plugin marketplace
update claude-factory`. Job state lives in projects, so updates never touch it.

## Tests

`bin/factory test` runs the offline suites (no SDK, network or API key): job
state and gates, removal audit and hook (`test_state.py`); graph, role policy,
profile delivery and config (`test_harness.py`); manifests, project neutrality,
vendored-file hashes, state location, init, Codex generation and the CLI
(`test_plugin.py`). `claude plugin validate --strict .` checks the manifests.

## Licensing

The factory's own code and roles have no license grant yet. The vendored skills
keep their own terms: greploop and greploop-apps (MIT, Greptile AI), unslop
(MIT, Lauren Tan), before-and-after (PolyForm Shield 1.0.0, from
vercel-labs/before-and-after). new-feature, code-structure and
evidence-driven-testing come from michaelshimeles/skills, which publishes no
license, so keep this repository private unless you get permission or drop
those three. Details in `THIRD_PARTY.md`.
