import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spiketrace.constants import ACTION_LABELS
from spiketrace.domain import VideoMetadata
from spiketrace.errors import ValidationError
from spiketrace.validation_contract import (
    ValidationVideoBinding,
    canonical_json_bytes,
    sha256_file,
)
from spiketrace.validation_evaluation import ValidationReport
from spiketrace.validation_inference import (
    ValidationInferenceResult,
    ValidationPrediction,
)
from spiketrace.validation_outputs import (
    verify_validation_outputs,
    write_validation_outputs,
)
from spiketrace.validation_truth import (
    CSV_HEADER,
    ValidationTruth,
    _binding_dict,
    _lock_digest,
)


class ValidationOutputTests(unittest.TestCase):
    @staticmethod
    def _write_truth_files(root: Path, binding: ValidationVideoBinding) -> tuple[Path, Path, ValidationTruth]:
        csv_bytes = ("\ufeff" + CSV_HEADER + "\n").encode("utf-8")
        csv_digest = hashlib.sha256(csv_bytes).hexdigest()
        authority = {
            "format_version": 1,
            "state": "locked",
            "video": _binding_dict(binding),
            "set_intervals": [],
            "side_intervals": [],
            "coverage": [],
            "actions": [],
            "visibility_events": [],
            "annotation": {"annotation_version": "truth-v1", "code_sha": "test", "created_at": "now"},
        }
        locked_digest = _lock_digest(authority, csv_digest)
        truth_payload = {**authority, "integrity": {"locked_sha256": locked_digest, "csv_sha256": csv_digest}}
        truth_json = root / "truth.json"
        truth_csv = root / "truth.csv"
        truth_json.write_bytes(json.dumps(truth_payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        truth_csv.write_bytes(csv_bytes)
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, locked_digest, csv_digest)
        return truth_json, truth_csv, truth

    def _bundle(self, root: Path, report: ValidationReport | None = None):
        video = root / "video.bin"
        video.write_bytes(b"video")
        checkpoint = root / "checkpoint.bin"
        checkpoint.write_bytes(b"checkpoint")
        metadata = VideoMetadata(video, 1.0, 1, 2, 2, 1.0)
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", sha256_file(video), metadata)
        truth_json, truth_csv, truth = self._write_truth_files(root, binding)
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video))
        params = {"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)}
        output = root / "output"
        write_validation_outputs(output, truth=truth, inference=inference, report=report or ValidationReport({}, {}, {}, {}, ()), checkpoint_path=checkpoint, code_sha="abc", parameters=params, created_at="2026-09-01T00:00:00Z")
        return output, video, checkpoint, truth_json, truth_csv

    def test_publishes_five_files_and_rejects_collision(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        self.assertEqual({path.name for path in output.iterdir()}, {"metrics.json", "confusion_matrix.csv", "predicted-events.json", "predicted-events.csv", "run-manifest.json"})
        self.assertEqual(verify_validation_outputs(output, repo_root=root, require_source_files=False)["match_id"], "socal-cup-final-2025")
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", sha256_file(video), VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        metrics = json.loads((output / "metrics.json").read_text())
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video))
        with self.assertRaisesRegex(ValidationError, "already exists"):
            write_validation_outputs(output, truth=truth, inference=inference, report=ValidationReport({}, {}, {}, {}, ()), checkpoint_path=checkpoint, code_sha="abc", parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)}, created_at="2026-09-01T00:00:00Z")

    def test_fixed_timestamp_is_byte_reproducible_and_csv_tamper_fails(self):
        root = Path(tempfile.mkdtemp())
        first, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        second = root / "second"
        metrics = json.loads((first / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", sha256_file(video), VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video))
        params = {"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)}
        write_validation_outputs(second, truth=truth, inference=inference, report=ValidationReport({}, {}, {}, {}, ()), checkpoint_path=checkpoint, code_sha="abc", parameters=params, created_at="2026-09-01T00:00:00Z")
        for name in ("metrics.json", "confusion_matrix.csv", "predicted-events.json", "predicted-events.csv", "run-manifest.json"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        (first / "predicted-events.csv").write_text("bad\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            verify_validation_outputs(first, repo_root=root, require_source_files=False)

    def test_manifest_tamper_fails_offline(self):
        root = Path(tempfile.mkdtemp()); output, _, _, _, _ = self._bundle(root)
        manifest_path = output / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text()); manifest["match_id"] = "tampered"; manifest_path.write_bytes(json.dumps(manifest).encode())
        with self.assertRaises(ValidationError):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_rejects_boolean_format_versions_even_with_recomputed_integrity(self):
        root = Path(tempfile.mkdtemp())
        output, _, _, _, _ = self._bundle(root)
        metrics_path = output / "metrics.json"
        manifest_path = output / "run-manifest.json"
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        metrics["format_version"] = True
        manifest["format_version"] = True

        metrics_content = dict(metrics)
        metrics_content.pop("manifest_core_sha256", None)
        manifest["metrics_content_sha256"] = hashlib.sha256(
            canonical_json_bytes(metrics_content)
        ).hexdigest()
        manifest_core = dict(manifest)
        manifest_core.pop("output_files", None)
        manifest_core.pop("metrics_file", None)
        metrics["manifest_core_sha256"] = hashlib.sha256(
            canonical_json_bytes(manifest_core)
        ).hexdigest()
        metrics_bytes = canonical_json_bytes(metrics) + b"\n"
        metrics_path.write_bytes(metrics_bytes)

        manifest["metrics_file"] = {
            "sha256": hashlib.sha256(metrics_bytes).hexdigest(),
            "bytes": len(metrics_bytes),
        }
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")

        with self.assertRaisesRegex(ValidationError, "format version"):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_writer_rejects_stale_inference_hashes(self):
        root = Path(tempfile.mkdtemp()); video = root / "v"; video.write_bytes(b"v"); checkpoint = root / "c"; checkpoint.write_bytes(b"c")
        metadata = VideoMetadata(video, 1.0, 1, 2, 2, 1.0); binding = ValidationVideoBinding("m", video, root, "v", sha256_file(video), metadata)
        truth_json, truth_csv, truth = self._write_truth_files(root, binding); report = ValidationReport({}, {}, {}, {}, ())
        inference = ValidationInferenceResult((), (), {}, "0" * 64, sha256_file(video))
        with self.assertRaisesRegex(ValidationError, "checkpoint SHA"):
            write_validation_outputs(root / "out", truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="x", parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)}, created_at="now")

    def test_writer_requires_paired_locked_truth_paths(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, _, _ = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", sha256_file(video), VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video))
        with self.assertRaisesRegex(ValidationError, "truth paths"):
            write_validation_outputs(
                root / "missing-paths",
                truth=truth,
                inference=inference,
                report=ValidationReport({}, {}, {}, {}, ()),
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={},
                created_at="2026-09-01T00:00:00Z",
            )

    def test_partial_output_is_rejected(self):
        root = Path(tempfile.mkdtemp())
        output, _, _, _, _ = self._bundle(root)
        (output / "confusion_matrix.csv").unlink()
        with self.assertRaisesRegex(ValidationError, "incomplete"):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_stale_truth_video_and_checkpoint_are_rejected(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, _, truth_csv = self._bundle(root)
        truth_csv.write_bytes(truth_csv.read_bytes() + b"\n")
        with self.assertRaises(ValidationError):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, _, truth_csv = self._bundle(root)
        video.write_bytes(b"changed video")
        with self.assertRaises(ValidationError):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, _, truth_csv = self._bundle(root)
        checkpoint.write_bytes(b"changed checkpoint")
        with self.assertRaises(ValidationError):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_atomic_publication_failure_cleans_staging_directory(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", sha256_file(video), VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video))
        destination = root / "failed-output"
        with patch("spiketrace._active_learning_review_outputs.rename_directory_noreplace", side_effect=OSError("simulated publish failure")), self.assertRaisesRegex(ValidationError, "Could not publish"):
            write_validation_outputs(
                destination,
                truth=truth,
                inference=inference,
                report=ValidationReport({}, {}, {}, {}, ()),
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                created_at="2026-09-01T00:00:00Z",
            )
        self.assertFalse(destination.exists())
        self.assertEqual(list(root.glob(f".{destination.name}.staging-*")), [])

    def test_confusion_matrix_requires_all_action_cells(self):
        values = [[0 for _ in ACTION_LABELS] for _ in ACTION_LABELS]
        values[0][1] = 1
        report = ValidationReport({}, {"samples": 1, "confusion_matrix": {"labels": list(ACTION_LABELS), "values": values}}, {}, {}, ())
        root = Path(tempfile.mkdtemp())
        output, _, _, _, _ = self._bundle(root, report=report)
        self.assertEqual(sum(1 for _ in (output / "confusion_matrix.csv").read_text().splitlines()) , 50)
        lines = (output / "confusion_matrix.csv").read_text().splitlines()
        (output / "confusion_matrix.csv").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_metadata_path_tampering_is_rejected(self):
        root = Path(tempfile.mkdtemp())
        output, _, _, _, _ = self._bundle(root)
        manifest_path = output / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["video_metadata"]["path"] = str(root / "other.bin")
        manifest_path.write_bytes(json.dumps(manifest).encode("utf-8"))
        with self.assertRaises(ValidationError):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_writer_rejects_invalid_prediction_schema(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", sha256_file(video), VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        prediction = ValidationPrediction("prediction", "segment", 1, "near", 0.0, 0.5, "not-an-action", 0.5, (0,))
        inference = ValidationInferenceResult((), (prediction,), {}, sha256_file(checkpoint), sha256_file(video))
        destination = root / "invalid-output"
        with self.assertRaisesRegex(ValidationError, "JSON schema is invalid"):
            write_validation_outputs(
                destination,
                truth=truth,
                inference=inference,
                report=ValidationReport({}, {}, {}, {}, ()),
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                created_at="2026-09-01T00:00:00Z",
            )
        self.assertFalse(destination.exists())

    def test_metrics_tampering_cannot_be_repaired_by_only_updating_manifest_hash(self):
        root = Path(tempfile.mkdtemp())
        output, _, _, _, _ = self._bundle(root)
        metrics_path = output / "metrics.json"
        metrics = json.loads(metrics_path.read_text())
        metrics["event_metrics"] = {"tampered": True}
        metrics_bytes = canonical_json_bytes(metrics) + b"\n"
        metrics_path.write_bytes(metrics_bytes)
        manifest_path = output / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["metrics_file"] = {"sha256": hashlib.sha256(metrics_bytes).hexdigest(), "bytes": len(metrics_bytes)}
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
        with self.assertRaises(ValidationError):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_metrics_noncanonical_bytes_cannot_be_repaired_by_manifest_hash(self):
        root = Path(tempfile.mkdtemp())
        output, _, _, _, _ = self._bundle(root)
        metrics_path = output / "metrics.json"
        metrics_bytes = metrics_path.read_bytes() + b" \n"
        metrics_path.write_bytes(metrics_bytes)
        manifest_path = output / "run-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["metrics_file"] = {"sha256": hashlib.sha256(metrics_bytes).hexdigest(), "bytes": len(metrics_bytes)}
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")

        with self.assertRaises(ValidationError):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_writer_rechecks_source_video_hash_before_publication(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", metrics["video"]["sha256"], VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), metrics["video"]["sha256"])
        video.write_bytes(b"changed after inference")
        destination = root / "stale-source-output"
        with self.assertRaises(ValidationError):
            write_validation_outputs(
                destination,
                truth=truth,
                inference=inference,
                report=ValidationReport({}, {}, {}, {}, ()),
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                created_at="2026-09-01T00:00:00Z",
            )
        self.assertFalse(destination.exists())

    def test_writer_rechecks_checkpoint_hash_before_publication(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", metrics["video"]["sha256"], VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), metrics["video"]["sha256"])
        destination = root / "stale-checkpoint-output"
        from spiketrace import validation_outputs

        original_write = validation_outputs._write_fsync
        writes = 0

        def write_and_change_checkpoint(path, payload):
            nonlocal writes
            original_write(path, payload)
            writes += 1
            if writes == 1:
                checkpoint.write_bytes(b"changed after checkpoint hash")

        with patch("spiketrace.validation_outputs._write_fsync", side_effect=write_and_change_checkpoint), self.assertRaises(ValidationError):
            write_validation_outputs(
                destination,
                truth=truth,
                inference=inference,
                report=ValidationReport({}, {}, {}, {}, ()),
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                created_at="2026-09-01T00:00:00Z",
            )
        self.assertFalse(destination.exists())

    def test_writer_rechecks_locked_truth_hashes_before_publication(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", metrics["video"]["sha256"], VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), metrics["video"]["sha256"])
        destination = root / "stale-truth-output"
        from spiketrace import validation_outputs

        original_write = validation_outputs._write_fsync
        writes = 0

        def write_and_change_truth(path, payload):
            nonlocal writes
            original_write(path, payload)
            writes += 1
            if writes == 1:
                truth_csv.write_bytes(truth_csv.read_bytes() + b"\n")

        with patch("spiketrace.validation_outputs._write_fsync", side_effect=write_and_change_truth), self.assertRaises(ValidationError):
            write_validation_outputs(
                destination,
                truth=truth,
                inference=inference,
                report=ValidationReport({}, {}, {}, {}, ()),
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                created_at="2026-09-01T00:00:00Z",
            )
        self.assertFalse(destination.exists())

    def test_verifier_does_not_swallow_source_hash_errors_offline(self):
        root = Path(tempfile.mkdtemp())
        output, video, _, _, _ = self._bundle(root)
        from spiketrace import validation_outputs

        original_hash = validation_outputs.sha256_file

        def fail_for_source(path):
            if Path(path).resolve() == video.resolve():
                raise ValidationError("simulated source hash failure")
            return original_hash(path)

        with patch("spiketrace.validation_outputs.sha256_file", side_effect=fail_for_source), self.assertRaisesRegex(ValidationError, "source video hash"):
            verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_writer_rejects_malformed_inference_hashes(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", metrics["video"]["sha256"], VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        for bad_hash in (None, 123):
            with self.subTest(bad_hash=bad_hash), self.assertRaises(ValidationError):
                write_validation_outputs(
                    root / f"invalid-inference-hash-{bad_hash}",
                    truth=truth,
                    inference=ValidationInferenceResult((), (), {}, bad_hash, metrics["video"]["sha256"]),
                    report=ValidationReport({}, {}, {}, {}, ()),
                    checkpoint_path=checkpoint,
                    code_sha="abc",
                    parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                    created_at="2026-09-01T00:00:00Z",
                )

    def test_writer_rejects_nonfinite_metrics(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", metrics["video"]["sha256"], VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        with self.assertRaises(ValidationError):
            write_validation_outputs(
                root / "invalid-nonfinite-output",
                truth=truth,
                inference=ValidationInferenceResult((), (), {}, sha256_file(checkpoint), metrics["video"]["sha256"]),
                report=ValidationReport({"nan": float("nan")}, {}, {}, {}, ()),
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                created_at="2026-09-01T00:00:00Z",
            )

    def test_writer_rejects_confusion_confidence_above_one(self):
        root = Path(tempfile.mkdtemp())
        output, video, checkpoint, truth_json, truth_csv = self._bundle(root)
        metrics = json.loads((output / "metrics.json").read_text())
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", sha256_file(video), VideoMetadata(video, 1.0, 1, 2, 2, 1.0))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, metrics["truth"]["json_sha256"], metrics["truth"]["csv_sha256"])
        report = ValidationReport(
            {},
            {},
            {},
            {},
            ({"prediction_id": "p", "truth_ref": "t", "predicted_label": "attack", "truth_label": "attack", "center_error_seconds": 0.0, "confidence": 1.1},),
        )
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video))
        with self.assertRaisesRegex(ValidationError, "Confusion report schema"):
            write_validation_outputs(
                root / "invalid-confidence",
                truth=truth,
                inference=inference,
                report=report,
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                created_at="2026-09-01T00:00:00Z",
            )

    def test_writer_rejects_rebound_truth_csv_projection(self):
        root = Path(tempfile.mkdtemp())
        video = root / "video.bin"
        video.write_bytes(b"video")
        checkpoint = root / "checkpoint.bin"
        checkpoint.write_bytes(b"checkpoint")
        metadata = VideoMetadata(video, 1.0, 1, 2, 2, 1.0)
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "video.bin", sha256_file(video), metadata)
        truth_json, truth_csv, _ = self._write_truth_files(root, binding)
        invalid_csv = b"\xef\xbb\xbfbad-header\n"
        truth_csv.write_bytes(invalid_csv)
        payload = json.loads(truth_json.read_text(encoding="utf-8"))
        authority = dict(payload)
        authority.pop("integrity")
        csv_digest = hashlib.sha256(invalid_csv).hexdigest()
        locked_digest = _lock_digest(authority, csv_digest)
        payload["integrity"] = {"locked_sha256": locked_digest, "csv_sha256": csv_digest}
        truth_json.write_bytes(canonical_json_bytes(payload))
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, locked_digest, csv_digest)
        inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video))

        with self.assertRaisesRegex(ValidationError, "CSV header mismatch"):
            write_validation_outputs(
                root / "invalid-truth-output",
                truth=truth,
                inference=inference,
                report=ValidationReport({}, {}, {}, {}, ()),
                checkpoint_path=checkpoint,
                code_sha="abc",
                parameters={"truth_json_path": str(truth_json), "truth_csv_path": str(truth_csv)},
                created_at="2026-09-01T00:00:00Z",
            )


if __name__ == "__main__":
    unittest.main()
