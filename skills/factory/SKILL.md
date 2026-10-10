---
name: factory
description: Run or resume feature delivery in the current project with a spec, isolated build, evidence, independent review and an isolated observer. Use for /factory <feature>, resume, status, rework, approve or next.
---
# Software factory

Use /factory <feature>, /factory resume <job-id>, /factory status <job-id>, or
/factory rework <job-id> <feedback>. This is an on-demand workflow, not a daemon.

`FACTORY` below means `"${CLAUDE_PLUGIN_ROOT}/bin/factory"`; run it from the
project's main checkout. Job state lives in the project (`.factory/jobs` by
default), never in the plugin.

## First use in a project

If `.factory/profile.md` is missing, run `FACTORY init`. It writes a profile
template and gitignores job state and worktrees; it never overwrites. Then fill
the profile with the user from what the repository actually says (CLAUDE.md,
AGENTS.md, tests, CI): the "All roles" rules, verification commands, forbidden
live services, and one checklist section per reviewer. A thin profile means thin
reviews: the factory's quality on a project is bounded by its profile. Optional
`.factory/config.json` sets `branch_prefix` (default `factory/`),
`worktrees_dir` (default `.worktrees`) and `state_dir` (default `.factory`).

## Run

Prefer the code harness when it is set up (`FACTORY setup` once per machine
installs claude-agent-sdk): run `FACTORY run "<the user's words>"` in the
background and report its result. It runs this same graph with the role policy
enforced in code and appends each role's profile section to its instructions.
Follow the steps below by hand only when the harness is unavailable or the user
wants to steer each step.

1. Read AGENTS.md, CLAUDE.md, `.factory/profile.md` and the project's
   architecture/roadmap docs when present. Verify implemented behavior in code.
   Use the factory state helper below for durable job progress.
2. Claim a unique job with `FACTORY state new "<the user's words>"` (atomic
   mkdir under the state directory). This directory is operational evidence, not
   another to-do list. The orchestrator alone writes job.json, through the
   helper, which saves atomically. Record feature, stage, branch, base_sha,
   reviewed_head, worktree, round, assigned reviewers and verdicts, artifact
   paths and blocker. Keep private user data out. A job has one active
   orchestrator; on resume inspect owner/live workers before taking over rather
   than racing another run.
3. Have `factory:spec-writer` write spec.md with verified context, requirements,
   testable acceptance, scope and assumptions. Apply ../new-feature/SKILL.md to
   isolate application changes, using the configured branch prefix. Pass explicit
   absolute paths (project root, job directory, worktree) and file ownership.
4. Have `factory:builder` implement in that checkout using
   ../code-structure/SKILL.md and ../evidence-driven-testing/SKILL.md. It writes
   build.md; it cannot approve its own work. Resume the same builder for
   findings when the harness supports it.
5. Apply ../review-loop/SKILL.md. Freeze HEAD; assign independent reviewers
   (`factory:reviewer-<lens>`) and unique round-N/review-<lens>.md outputs. Run
   in parallel within available slots, scheduling waves if necessary. Wait for
   every assigned result before restarting the builder. No writer shares files
   with another feature writer.
6. Run the removal audit (`FACTORY guard <id> --head <sha>`) before every review
   round and before ready. Any removal without a complete removals.md line (why
   it existed, why safe, regression check) goes back to the builder to research
   and record, or to restore.
7. Run the observer loop (below) until the observer answers DONE at the HEAD
   you intend to mark ready.
8. Have `factory:approver` inspect the exact final HEAD and write decision.md.
   Use the helper to update stage to ready only with all acceptance met,
   required checks passed, and no unresolved blocking findings. Otherwise return
   to build or retain blocked state with the precise reason. End with evidence
   and the concrete result.
9. Merge/push/deploy only within actual user authorization. Before merging check
   the current base, conflicts and dirty files; revalidate after integration.
   Retain recoverable work and clean up only owned worktrees when safe.

## Orchestrator ⇄ observer loop

You (the session running /factory) are the orchestrator, the only node that
talks to the `factory:observer` agent (Opus 5.5). The observer stays outside
the worker graph:

    user task ─► orchestrator ◄── "Did we accomplish the given task end to end?" ──► observer
                     │                                  (DONE / NOT_DONE + gaps + critique)
                     ▼
     spec-writer ─► builder ⇄ reviewers (≤3 rounds) ─► approver

- Record the user's request verbatim as the job feature (`FACTORY state new
  "<the user's words>"`). That text is the given task.
- Each pass, ask exactly that question and pass only the given task, the
  absolute project root/job/worktree paths, base SHA, current HEAD (from `git
  rev-parse HEAD`) and the pass number. Never pass spec, build, review or
  decision content, and never summarize other agents' work for it. On later
  passes you may reply to its critique; it re-verifies everything anyway.
- Ask after the builder first reports READY, after each settled review round,
  and before ready. NOT_DONE: turn each gap into your own instruction to the
  right node (spec gap → spec-writer, missing behavior → builder, then a new
  review round) and ask again. Weigh the workflow critique and fix the process
  where it is right. BLOCKED: stop and take the question to the user.
- Never tell a worker that the observer exists, and never forward its report.
- Resume the same observer between passes when the harness supports it;
  otherwise start a fresh one. Either way it re-checks at the current HEAD.
- Stop after 5 passes without DONE: mark the job blocked with the observer's
  open gaps and report to the user. `FACTORY state ready` refuses unless
  observer/final.md says `OBSERVER: DONE` for that exact HEAD and guard.json
  is clean for it.

## Delete with knowledge

Agents may remove code, but only after learning why it was there, so a bug the
old code fixed cannot come back. The removal audit finds every deleted file,
removed function/class/method (Python symbols; other languages by file and bulk
deletion) since base, including uncommitted changes, and requires a matching
line in the job's removals.md:

    - path::Symbol | existed-for: <real commit sha> <what it protected> | why-safe: <why> | regression-check: <test>

The commit must exist, which forces a look at the history. Reviewers and the
observer independently check that the regression really is covered.

Separately, the plugin's PreToolUse hook (`factory/scripts/destructive_guard.py`)
blocks commands that throw away work with no trace: git reset --hard, clean,
checkout --, restore, stash drop, force push, branch -D and recursive
rm/find -delete in the project. Deleting specific files or code is allowed. It
pattern-matches command text, so it is a safety net, not a sandbox.

Resume from saved artifacts and live Git state after app closure. Do not claim
workers kept running. Reuse existing completed evidence if HEAD has not changed;
if it changed, invalidate review decisions and re-review affected behavior.
Status is read-only. Rework preserves prior review rounds and records the user's
feedback. Delegation tools differ by harness: Claude Code uses the plugin's
registered agents (`factory:<role>`); Codex uses the agents `FACTORY codex
install` generates. If delegation is unavailable, disclose that review is a
self-review and leave independent review pending rather than fabricating an
agent run.

Use the user's current model setting; no model override is required beyond the
observer's. No paid review service, deployment credential, permissive security
setting or scheduled automation is installed by this skill.

## State and delivery commands

Run these from the project's main checkout (the helper also accepts --root for
isolated validation). It records operational state; it never builds or merges.

- New: FACTORY state new "<feature>"
- Status/list: FACTORY state show <id> / list
- Bind: FACTORY state bind <id> --worktree <absolute-path> --branch <branch> --base <sha>
- Build: FACTORY state stage <id> build
- Review: FACTORY state round <id> --head <sha> --assigned code security ux ui data --required code security
- Gate: FACTORY state check <id> --head <current-sha>
- Deletion audit: FACTORY guard <id> --head <current-sha>
- Observer verdict: <job>/observer/final.md (written by the observer only)
- Approval: FACTORY state stage <id> approve
- Ready: FACTORY state ready <id> --head <current-sha>
- Block: FACTORY state stage <id> blocked --reason "<reason>"
- Delivered: FACTORY state delivered <id> --evidence "<PR URL or delivery artifact>"

Every review and decision includes a separate HEAD: <sha> line. SKIP includes
Reason: <specific reason>. Add ux/data to --required when the change touches
those behaviors; add ui when a visual surface changes. Assign only applicable
lenses or record a justified SKIP for optional lenses. The helper rejects missing
reviews, required skips, stale HEAD attestations and failed approval. The
orchestrator must obtain current SHA from git rev-parse HEAD; a CLI argument is
not proof by itself. Inspect uncommitted diff too; freeze both while reviewing.

After READY, assemble before/after evidence and plain PR prose with the relevant
skills (before-and-after, unslop). For a GitHub delivery request, commit only
owned files, update against the intended base, rerun required checks and
open/update the PR with proof. In Codex attach every created PR with
attach_artifact. If Greptile is configured, run greploop with its actual
feedback; otherwise report independent local review and the missing external
integration. Present PR URL and unresolved limits. Merge only when the user has
authorized it. Mark delivered after the actual requested handoff succeeds, not
merely after tests pass. This setup creates no background runner. `/factory
next` reads the canonical pending task from exposed work-state tools when
available; otherwise ask for the feature rather than inventing a product
backlog. `/factory approve <id>` means the user's instruction to deliver that
reviewed job; recheck current HEAD, base, conflicts and evidence before the
requested merge. Never interpret approval as deployment authorization.

Dashboard: FACTORY serve --port 8765, then open
http://127.0.0.1:8765/dashboard.html. It reads saved artifacts and stays on
loopback. Ignored job files survive app restarts on this disk, but are not Git
backups and are not copied into a fresh clone.
