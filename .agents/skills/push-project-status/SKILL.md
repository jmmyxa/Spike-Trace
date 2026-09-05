---
name: push-project-status
description: "Use when closing a Spike-Trace session, preparing a Git commit, pushing a branch to GitHub, or synchronizing docs/PROJECT_STATUS.md."
---

# Push Project Status

Treat project closeout as an auditable transaction: the repository status, the
project log, the verification evidence, and the remote ref must agree.

## Required closeout

For every Spike-Trace work session, update `docs/PROJECT_STATUS.md` before
yielding. Add a concise dated entry containing:

- what changed and what remains;
- checks actually run and their results;
- blockers or skipped checks;
- next action;
- branch, remote, and sync state.

If the session made no code change, record that explicitly instead of creating
an empty commit. A crash or user interruption can prevent this closeout; the
next session must detect and report an unsynced status file.

## Push sequence

1. Read repository instructions. Inspect `git status --short --branch`,
   `git branch -vv`, `git remote -v`, and the latest log. Record the starting
   state and resolve the actual upstream; never assume `main` or `origin`.
2. Separate this session's paths from pre-existing user changes. Stage only
   reviewed paths. Do not use `git reset`, `git checkout`, `git clean`, or an
   automatic stash to hide or overwrite user work.
3. Update the status log and `README.md` when structure or workflow changed.
   Inspect the staged diff for unrelated files, secrets, generated artifacts,
   and accidental large files. Run the narrowest relevant tests first, then
   broader checks when practical; record failures honestly.
4. Commit with a descriptive message. Push normally to the resolved remote and
   branch when the user or task explicitly authorizes that external mutation.
   Never use `--force`, `--no-verify`, or an automatic pull/rebase to bypass a
   rejected push. Without push authorization, leave the reviewed commit local
   and report that remote synchronization is pending.
5. Verify the remote ref/commit SHA, local tracking state, and working tree.
   If the status entry must contain the exact new SHA, make a second status
   commit after the first push and push it too; otherwise do not self-reference
   a not-yet-created commit. Do not claim “synced” while a status change is
   still unpushed.

## Stop conditions

Pause and report instead of guessing when the branch is detached, upstream is
missing or ambiguous, the remote has diverged, files overlap with user edits,
or a push times out/rejects. On timeout, check the remote ref before retrying.
Preserve the local commit and the original error; ask before fetch/merge/rebase
or any force operation.

## Handoff

Use this compact report only after evidence is available:

```text
结果：成功 / 未推送 / 远端状态不确定
目标：<remote>/<branch>
提交：<sha or none>
证据：<push output and remote ref check>
验证：<commands and actual results; skipped checks with reasons>
工作树：<clean or preserved user paths>
状态日志：<path and whether its latest change is pushed>
下一步：<one concrete action>
```

Red flags are `git add -A` on a dirty user tree, a force push, a success claim
without remote verification, and a status file modified after the last push.
