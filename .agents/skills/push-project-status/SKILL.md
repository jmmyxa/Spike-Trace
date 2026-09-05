---
name: push-project-status
description: "Use when closing a project session, preparing a Git commit, pushing a branch to a remote, or synchronizing a project status log."
---

# Push Project Status

Treat closeout as an auditable transaction: the status log, evidence, local
history, and remote state must agree.

## Locate the project

1. Find the project root, read applicable `AGENTS.md`/instructions, and detect
   the VCS. For non-Git projects or undocumented VCS sync, update an existing
   log when authorized and report local-only; never imply a push occurred.
2. Resolve the log in this order: explicit configuration/instructions,
   existing `docs/PROJECT_STATUS.md`, root `PROJECT_STATUS.md`, then another
   clearly named existing status file. If candidates lack a documented choice,
   stop and ask—never guess. If none exists, create `docs/PROJECT_STATUS.md`
   only with explicit authorization and no conflicting convention; otherwise
   report that no log was persisted. Record the exact path.

## Required closeout

Every covered session needs a dated entry: changes/remaining work, checks,
blockers/skips, one next action, and sync state. For no-code, say “no code
change”; never manufacture an empty commit. After interruption, look for an
unsynced log first.

## Git sequence

1. Snapshot `git status --porcelain=v1 --branch`, `git branch -vv`,
   `git remote -v`, and the latest log. Record pre-existing staged/unstaged
   paths and resolve the actual upstream; never assume a branch, remote, or
   remote name. No remote is a valid local-only outcome.
2. Isolate this session's paths and preserve the index. If staged paths overlap
   or cannot be isolated, stop. Prefer `git commit --only <reviewed paths>`;
   never broad-stage or unstage user work. Never use `reset`, `checkout`,
   `clean`, automatic stash, `--force`, or `--no-verify` to bypass uncertainty.
3. Update the resolved log; update an existing README/equivalent only when
   structure or workflow changed. Inspect the staged diff for unrelated,
   secret, generated, large, or conflicting files. Run relevant checks and
   record exact results, including reasons for skips.
4. Commit descriptively. Push only with explicit authorization and an
   unambiguous target; otherwise report the local commit as pending sync.
5. After each successful push, compare the remote ref with local HEAD using
   `git ls-remote <remote> refs/heads/<branch>` or an equivalent API. Failure
   means remote state is unknown. Verify tracking and the working tree. If the
   log must contain the new SHA, make a second status-only commit, push it, and
   repeat every verification; never claim synced while the latest log is
   unpushed.

## Stop and report

Stop before mutation for a detached/ambiguous branch, conflicting log edits,
divergent remote, missing authorization, or unclear target. On timeout or
rejection, preserve the local commit/error; check the remote ref before retrying
and ask before fetch, merge, or rebase. Use `N/A`, `not configured`, or
`unknown` for valid local-only states.

## Handoff

```text
结果：成功 / 本地已提交未推送 / 未提交 / 远端状态不确定
项目与状态日志：<root and exact path, or not persisted>
目标：<remote>/<branch or local-only>
提交：<sha or none>
证据：<push output and remote-ref check, or why unavailable>
验证：<actual results; skipped checks and reasons>
工作树：<clean or preserved user paths>
下一步：<one concrete action>
```

Red flags: broad staging on a dirty tree, guessed paths/remotes, force pushes,
and success language without remote evidence.
