---
name: review-loop
description: Review a frozen factory change with independent reviewers and resolve grounded findings before delivery.
---
Give independent reviewers the spec, base SHA, reviewed HEAD, worktree, round
folder and the project's `.factory/profile.md`. Run code and security reviews;
include UX for CLI/API/tool/user flows, UI for visible surfaces, and data review
for pipeline/extraction/storage/analytics/scoring/training work. A nonapplicable
lens records SKIP with a reason; an unavailable required check records BLOCKED,
never PASS. Wait for all assigned reviewers before edits.

Reviewers report VERDICT: PASS, CHANGES, SKIP or BLOCKED, commands/checks,
severity, exact path/line, concrete impact and evidence. The builder addresses
blocking findings then regenerates proof. Review the new HEAD, invalidating old
approval. Use at most three rounds per run; if unresolved, preserve artifacts
and identify the exact blocker without describing incomplete work as finished.

The approver verifies acceptance and final reviews against the exact HEAD and
reports READY, CHANGES or BLOCKED. READY does not merge. Keep merge/deploy and
external messaging within the user's actual authorization. If an external review
service is already configured, use its real feedback; local reviewers are the
default here and cannot supply a fabricated Greptile confidence score.
