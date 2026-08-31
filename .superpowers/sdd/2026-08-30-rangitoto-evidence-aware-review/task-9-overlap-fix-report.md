# Task 9 Prerequisite Report: Source Overlap and Training Conflicts

Status: complete for the overlap prerequisite. The lossless workbook source now preserves same-side timed overlaps, evidence composition derives effective background scope after source matching, and Python projection rejects only conflicting eligible human windows. The real read-only verifier reaches clip 023 and asserts all six source rows without publishing a review artifact.

## Scope

- Starting HEAD: `5d66ef5 fix: verify local ZIP entry checksums`.
- Committed overlap implementation in `daa07d4 fix: separate source overlap from training conflicts`.
- Modified only the Node workbook/composer tests and source semantics, Python contract/projection code, and their tests; the frozen selection, workbook, and evidence override were not edited or staged.
- Kept v1 shared-formula fixes and all source identity, raw values, normalized values, slots, rows, repairs, and side inheritance semantics.

## RED/GREEN Cycles

### Node source canonicalization

RED was observed with the existing real semantic path and synthetic fixture:

```text
Error: Clip round-01-clip-001 has overlapping timed rows.
```

The RED fixture covered same-label exact duplicates, different-label exact overlap, partial overlap, and containment. It also retained rejection coverage for conflicting sides and untimed-background sentinel mixing.

GREEN removes only the source-layer timed-overlap rejection. Sentinel isolation remains fail-closed and now checks the canonical row's actual `normalized_values.review_label` field. `tools/test_active_review_evidence.mjs` exits `0`.

### Node composition

The producer-shape fixture covers an evidence override changing source `dig` at `6–7` to effective `background`, while retaining source `dig` and source `background_scope: null`. It also covers changing a source timed background to effective `attack`, which must clear the effective scope.

GREEN derives effective scope from effective label and source timing: timed effective background is `timed_interval`, untimed effective background is `clip_sentinel`, and non-background effective labels are `null`.

### Python source/effective contract

RED initially failed with:

```text
ValueError: action observation does not preserve its source row.
```

GREEN validates source row scope from source normalized label/times, compares source action raw/normalized/identity fields exactly while excluding only the source-vs-effective scope field, and validates effective label/scope/times independently. The contract test also rejects mismatched effective scope, moved times, and changed source raw data.

Focused result: `20` contract tests passed in `73.219s`.

### Python training projection

RED showed all four same-side eligible overlap shapes were accepted. The excluded timed `sequence_context` background fixture already produced no human window and preserved its protection interval.

GREEN adds a same-clip/same-side overlap guard over eligible human windows. Different sides remain allowed; excluded timed observations are not windows but remain protected.

Focused result: `15` projection tests passed.

### Real read-only verification

The frozen workbook path now passes canonicalization and returns exactly six clip-023 rows:

| slot | row | source label | interval | side | note |
| ---: | ---: | --- | --- | --- | --- |
| 1 | 268 | `receive` | `4–5` | `near` | `null` |
| 2 | 269 | `set` | `5–6` | inherited `near` | `null` |
| 3 | 270 | `attack` | `6–7` | inherited `near` | `null` |
| 4 | 271 | `dig` | `6–7` | inherited `near` | `被拦回的保护` |
| 5 | 272 | `set` | `8–9` | inherited `near` | `null` |
| 6 | 273 | `attack` | `9–10` | inherited `near` | `null` |

The read-only shared-formula command exits `0` with `40` shared blocks, `39` standard blocks, mixed block `C28:C39`, and canonicalization `clip-023 source overlaps preserved`.

The real evidence test now asserts clip 035 has zero `fully_occluded` action observations and has an occlusion `clip_bounds` visibility event, avoiding invented action evidence. The full Node evidence suite exits `0`.

## Regression Evidence

- Node: `tools/test_active_review_evidence.mjs` — exit `0`.
- Node real shared formulas: `tools/test_active_review_shared_formulas.mjs --real ...` — exit `0`.
- Python: `tests.test_active_learning_review_contract` — `20` passed.
- Python: `tests.test_active_learning_review_projection` — `15` passed.
- `git diff --check` — no whitespace errors; Git emitted only existing LF-to-CRLF warnings.

## Frozen Source Hashes

```text
selection.json  c7c9d4c21ae8fb041eece192b9c4f2c66648c863fdf79c278f08ca11e6cfe06c
review.xlsx     3b3baa474bf5d20e24a2e979b389e5d1b6df755b3c8516c993d8cc719b53535b
```

The evidence override remains an untracked Task 9 owner artifact and was intentionally not staged. `round-01-review-v2.json` is outside this prerequisite and was not created or modified by this implementation.

## Self-Review

- Source authority remains lossless; no overlap is merged, deleted, or rewritten.
- Effective scope is derived only after raw expected-source matching, so source scope cannot be silently rewritten by an override.
- Training overlap rejection is clip/side scoped and applies to all four interval shapes, including same-label duplicates; different sides remain independent.
- Excluded timed sequence-context observations contribute protection and cannot create background training rows.
- Sentinel isolation, clip bounds, paired whole-second times, side inheritance/conflict, hard-negative protection, and shared-formula verification remain unchanged.
- No new columns/entities, selection/workbook edits, v2 bundle publication, or override staging are part of this commit.
