## Workflow

Every task runs through the software factory: `/factory <task, in your words>`. Each step below names the skill that does it. The project's own rules for each role are in the "Factory profile" section of this file; `factory onboard` copies them to `.factory/profile.md`.

1. **Isolate** ({{isolate}}). One task, one branch `{{branch_prefix}}<task>`, one worktree under `{{worktrees_dir}}/`, from the latest `origin/main`. Scope-check open pull requests first; on overlap, stop and ask. Never build on `main`.
2. **Specify** (the `spec-writer` role). A spec grounded in code that was opened: requirements, testable acceptance, scope. A change to a protected path stops here until a person approves the spec.
3. **Build** (the `builder` role{{build_skill}}). Tests first; the fix or feature next; a bug fix gets a test that fails without it.
4. **Prove** ({{prove}}). The project's checks plus evidence: measured numbers or output pairs, and before and after images when there is a visible surface. Evidence comes from fixtures, never from private data.
5. **Review** ({{review}}). Independent reviewers on a frozen HEAD: code and security always, plus ux, ui and data when the change touches them. Every deletion is explained in the job's removals record. An isolated observer checks the result against the task as it was asked. An approver checks acceptance at the exact HEAD.
6. **Ship.** A pull request with the proof in its body{{ship}}. A person merges.

Rules for every agent in this repository:

- Never commit to `main`. Never reuse or touch another agent's worktree, branch or uncommitted work.
- Never plain `--force`; only `--force-with-lease`, only on your own branch.
- Regenerate lockfiles on conflict; never hand-merge them.
- Worktrees share ports, databases and files outside the repository: confirm a port answers your own process before trusting it.
- Merging, deploying, publishing and anything sent outside the repository stay with a person.

### Skills

| Skill | What it does here |
| --- | --- |
{{skills}}
{{skills_off}}
