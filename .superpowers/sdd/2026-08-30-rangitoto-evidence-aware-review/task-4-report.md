# Task 4 Report — Compose the Lossless Review Input V2

## RED

```powershell
& C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe tools\test_active_review_evidence.mjs
```

Observed exit `1`: `ERR_MODULE_NOT_FOUND` for `tools/compose_active_review_evidence.mjs`, as required before the composer existed.

## GREEN

```powershell
& C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe tools\test_active_review_evidence.mjs
```

Observed exit `0` with no diagnostic output. This exercises deterministic v2 composition independently of the optional XLSX runtime.

```powershell
& C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe tools\test_active_review_batch.mjs
```

Observed exit `1` before test execution: this worktree lacks the `@oai/artifact-tool` package/junction required by the workbook integration suite. The failure is environmental, not a reported assertion failure.

## Self-review

- The composer takes one frozen snapshot of each mutable JSON/XLSX source, binds all verification and publication work to those bytes, then byte-compares each source and hashes the frozen selection's video immediately before no-replace publication.
- V2 composition preserves canonical source rows and normalization audit data, separates action/outcome/visibility/participant records, uses stable result-scoped references, and requires explicit same-side coverage for off-camera or fully occluded actions.
- The pure composition test asserts exact top-level fields, schema discriminators, default direct evidence, override provenance, supplemental references, source-second timing, lossless audit emission, and absent Python-owned training projection.
- `--real` performs a pure frozen-byte regeneration and byte-for-byte comparison without calling the publisher. Real artifacts are authored by Task 9, so that acceptance path cannot run yet.

## Fix round 1 RED/GREEN

```powershell
& C:\Users\Fakelove\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe tools\test_active_review_evidence.mjs
```

RED observed exit `1`: the new golden assertion expected `batch-1/result-549fd199e806acab`, while the previous implementation returned the incompatible `active-review-e593e3a42132357a67b77dcf`.

After replacing the identifier with the specified NUL-delimited canonical SHA-256 formula, rejecting empty related-action arrays during envelope parsing, adding injectable composer publication-boundary tests, and threading explicit `repoRoot` into workbook binding verification, the same command observed exit `0`.

The fresh test coverage mutates each frozen selection/workbook/override/video source after verification and asserts no output or temporary sibling leaks; it also covers collision, write/sync/publish failure cleanup, deterministic cross-directory bytes, and the explicit non-default repository root received by the verifier hook.

## Fix round 2 RED/GREEN

The composer verifier seam was removed. Trust-boundary tests now build a real synthetic proxy batch and XLSX, populate valid review rows, bind a real override to frozen source bytes, and invoke the production workbook verifier for mutation, collision, publication-failure, and deterministic-publication cases. The synthetic fixture uses a unique `tests/.active-review-evidence-fixture-*` directory and never mutates repository evidence artifacts.

The artifact runtime is supplied without a worktree junction through a percent-encoded data-URL loader. Set `SPIKETRACE_PYTHON` to `.venv\\Scripts\\python.exe`, `PYTHONPATH` to `src`, and put the same loader in `NODE_OPTIONS` so spawned extraction CLI processes inherit it. With that setup, `tools/test_active_review_evidence.mjs` exits `0`, and `tools/test_active_review_batch.mjs` exits `0` after the completed/rejected review, corruption, race, and rollback cases.

The workbook semantic verifier also accepts only the exact artifact-tool `HYPERLINK is not implemented. linkLocation=clips/<expected-id>.mp4, friendlyName=播放` diagnostic when its separately validated formula names the same expected clip; arbitrary display text remains rejected.
