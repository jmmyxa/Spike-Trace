# Rangitoto Active Learning Round 01 — Final Review Fixes

Commit: `556a7aa` (`fix: close Rangitoto review handoff gaps`)

## Scope

- Enforce the complete Task-2 contract for every `active-learning-selection-v1` payload, including a regression that strips all optional surfaces.
- Pin deterministic merged-candidate CSV artifacts to LF and verify the real committed CSV through the dual-crop verifier.
- Verify workbook A1/A2 titles and instructions on all four sheets, with corruption coverage in the Node test harness.
- Reject non-finite positive CLI floats and add direct invalid `output_fps`/`max_width` proxy-video coverage.
- Exercise the real dual-crop builder → verifier → selector handoff, including mutation-based RED checks.
- Refresh the README handoff/tree and remove the superseded `task-2-report.md`.

## Verification

Evidence from the completed RED/GREEN verification pass:

- `python -m unittest discover -s tests -v`: 200 tests, `OK`.
- `ruff check .`: `All checks passed!`.
- `python -m compileall -q src tests`: exit 0.
- `node tools/test_active_review_batch.mjs`: exit 0.
- `node tools/test_rangitoto_review.mjs`: exit 0.
- Real workbook verifier: 40 selected clips, exit 0.
- Real dual-crop verifier: `verified: true`, expected hashes and counts.
- Protected-artifact comparison: 49 files unchanged.
- Staged diff check: clean.

The current worktree intentionally retains an untracked local `node_modules/` directory; it is excluded from the commit.
