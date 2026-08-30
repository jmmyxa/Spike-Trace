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
