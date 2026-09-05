# player-identity worklog

## 2026-08-10

### Lane and scope

- Active lane ID: `player-identity`
- Active worklog path: `.agent-lanes/player-identity/worklog.md`
- Authoritative write scope for phase 1 in this task:
  - `docs/identity/**`
  - `.agent-lanes/player-identity/worklog.md`
- Runtime code under `src/spiketrace/**` and tests under `tests/**` were not modified in phase 1.

### User instructions recorded

- Audit the repository for player identity and jersey number recognition in Spike-Trace.
- Cover current action labels, USA half-court crops, video metadata, and evaluation method.
- Design stable interfaces for distinguishing USA players, tracking them across frames, reading jersey numbers, and supporting later per-player stats.
- Explicitly document:
  - input and output contracts
  - human review gates
  - jersey number accuracy metrics
  - handling for occlusion, back-facing players, and unreadable numbers
  - boundaries with the action model and data platform
  - product decisions that must be confirmed before implementation
- Phase 1 must not modify shared runtime code.
- Use the ASCII lane ID `player-identity` and this ASCII worklog path instead of the earlier Chinese directory name.

### Migration decision

- The active lane key was normalized to ASCII for Windows-script compatibility.
- This task now records all material notes only in `.agent-lanes/player-identity/worklog.md`.
- A legacy Chinese-named lane directory is present in the working tree, but it was not used for new writes in this phase.

### Repository facts verified

- `src/spiketrace/domain.py` already defines nullable `team_side` and `player_number` fields for annotation records and action events.
- `src/spiketrace/events.py` currently emits run-local `event_id` values and writes `team_side=None` and `player_number=None`.
- `src/spiketrace/outputs.py` exports `events.json` and `events.csv` with `team_side` and `player_number`, but without identity evidence.
- `src/spiketrace/metrics.py` and `src/spiketrace/pretrained.py` evaluate action windows only, not player identity, tracking, or OCR.
- `data/annotations/usa_germany_2024_match.json` records:
  - match ID `usa-germany-2024-olympics`
  - `fps=30.0`
  - `width=1280`
  - `height=720`
  - `duration_seconds=7687.333333333333`
  - `usa_side_segments=6`
- `data/annotations/usa_germany_2024_annotations_expanded_batch_02.csv` contains:
  - `90` records
  - split set `train` only
  - `player_number` empty in all `90` rows
  - `team_side` values limited to `far` and `near`

### Design decisions recorded

- The identity layer is a sidecar consumer of action outputs and does not rewrite action labels, windows, or confidences.
- Detection boxes, `track_id`, `identity_ref`, and jersey number are separate concepts and must not be conflated.
- USA side segments are search priors, not team-identity truth.
- Unknown, occluded, back-facing, unreadable, or conflicting cases must remain unresolved instead of guessed.
- Downstream stats may consume only confirmed identity assignments.
- Identity evaluation must be reported on a dedicated annotation set rather than the current same-match 90-window training manifest.

### Files authored or rewritten in this phase

- `docs/identity/2026-08-10-audit.md`
- `docs/identity/2026-08-10-design.md`
- `.agent-lanes/player-identity/worklog.md`

### Verification

- Workspace status before closeout showed only untracked `.agent-lanes/` and `docs/identity/` content for this lane.
- Repository audit facts were rechecked against:
  - `src/spiketrace/domain.py`
  - `src/spiketrace/events.py`
  - `src/spiketrace/outputs.py`
  - `src/spiketrace/metrics.py`
  - `src/spiketrace/pretrained.py`
  - `src/spiketrace/review.py`
  - `data/annotations/usa_germany_2024_match.json`
  - `data/annotations/usa_germany_2024_annotations_expanded_batch_02.csv`
- Document self-review requirements:
  - no unresolved marker strings in the phase 1 docs
  - no runtime code edits outside the allowed scope
- Test command selected from repository README:
  - `python -m unittest discover -s tests -v`
- Environment note:
  - the system `python` command resolves to the Windows Store alias, so tests must run with the bundled Codex runtime Python.
- Fresh verification results:
  - unresolved-marker scan across `docs/identity/**` and this worklog returned no matches after rewrite
  - `git status --short` after the phase 1 edits still shows only:
    - `?? .agent-lanes/`
    - `?? docs/identity/`
  - unit tests were rerun with bundled runtime Python and `PYTHONPATH=src`
  - result: `53` tests run, `50` passed, `3` failed
  - failing tests:
    - `tests/test_ml.py::CheckpointTests`
    - `tests/test_training.py::TrainingConfigTests`
  - failure cause:
    - bundled runtime does not include `torch`
    - errors come from `src/spiketrace/ml.py` `require_torch()`
    - no failure was caused by the phase 1 docs-only changes

### Open product decisions before implementation

- official roster source and canonical `player_ref`
- multi-actor event policy versus single primary actor
- acceptance thresholds for USA precision, jersey accuracy, and confirmed coverage
- occlusion gap and cross-segment relinking rules
- dedicated identity annotation-set creation and match-isolated splits
- OCR license and deployment constraints
- human review storage, authority, and publish flow
- downstream stable key format for stats joins

## 2026-09-05

### MVP runtime slice

- Added `src/spiketrace/identity/` with immutable `PlayerDetection`, `Track`,
  `NumberObservation`, `NumberResolution`, and `IdentityAssignment` models.
- Kept camera court position (`court_side`) separate from visual team identity
  (`team`); all unknown and out-of-scope values remain explicit.
- Added conservative multi-frame OCR aggregation with roster filtering,
  leading-zero preservation, and conflict handling.
- Added `apply_identity_assignments` adapter that joins only overlapping,
  confirmed USA assignments to action events without changing action metadata.
- Added six focused tests under `tests/identity/`.

### Verification

- `python -m unittest discover -s tests/identity -v`: 6 tests passed.
- No detector/OCR weights were downloaded and no action or SoCal validation
  modules were changed.

### Component integration note

- Added `docs/identity/2026-09-05-component-integration.md` describing optional
  YOLO/RT-DETR detection, ByteTrack/BoT-SORT tracking, and PaddleOCR/Tesseract
  adapters.
- The note keeps components replaceable and defines `Unknown`, conservative
  OCR statuses, and mandatory manual-review boundaries.
