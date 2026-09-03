import csv
import json
import tempfile
import unittest
from pathlib import Path

from spiketrace.domain import VideoMetadata
from spiketrace.errors import ValidationError
from spiketrace.validation_contract import ValidationVideoBinding, sha256_file
from spiketrace.validation_evaluation import ValidationReport
from spiketrace.validation_inference import ValidationInferenceResult
from spiketrace.validation_truth import ValidationTruth
from spiketrace.validation_outputs import write_validation_outputs, verify_validation_outputs


class ValidationOutputTests(unittest.TestCase):
    def test_publishes_five_files_and_rejects_collision(self):
        root = Path(tempfile.mkdtemp())
        video = root / "match.mp4"
        video.write_bytes(b"video")
        checkpoint = root / "best.pt"
        checkpoint.write_bytes(b"checkpoint")
        metadata = VideoMetadata(video, 1.0, 1, 2, 2, 1.0)
        binding = ValidationVideoBinding("socal-cup-final-2025", video, root, "match.mp4", sha256_file(video), metadata)
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, "truth-json", "truth-csv")
        inference = ValidationInferenceResult((), (), {"stride_seconds": 0.4}, sha256_file(checkpoint), sha256_file(video))
        report = ValidationReport({}, {}, {}, {}, ())
        output = root / "out"
        paths = write_validation_outputs(output, truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="abc", parameters={"stride_seconds": 0.4}, created_at="2026-09-01T00:00:00Z")
        self.assertEqual({path.name for path in paths.values()}, {"metrics.json", "confusion_matrix.csv", "predicted-events.json", "predicted-events.csv", "run-manifest.json"})
        self.assertEqual(verify_validation_outputs(output, repo_root=root, require_source_files=False)["match_id"], "socal-cup-final-2025")
        with self.assertRaisesRegex(ValidationError, "already exists"):
            write_validation_outputs(output, truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="abc", parameters={}, created_at="2026-09-01T00:00:00Z")

    def test_fixed_timestamp_is_byte_reproducible_and_csv_tamper_fails(self):
        root = Path(tempfile.mkdtemp()); video = root / "v"; video.write_bytes(b"v"); checkpoint = root / "c"; checkpoint.write_bytes(b"c")
        metadata = VideoMetadata(video, 1.0, 1, 2, 2, 1.0); binding = ValidationVideoBinding("m", video, root, "v", sha256_file(video), metadata)
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, "j", "c"); inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video)); report = ValidationReport({}, {}, {}, {}, ())
        first = root / "one"; second = root / "two"
        write_validation_outputs(first, truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="x", parameters={}, created_at="2026-01-01T00:00:00Z")
        write_validation_outputs(second, truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="x", parameters={}, created_at="2026-01-01T00:00:00Z")
        for name in ("metrics.json", "confusion_matrix.csv", "predicted-events.json", "predicted-events.csv", "run-manifest.json"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        (first / "predicted-events.csv").write_text("bad\n", encoding="utf-8")
        with self.assertRaises(ValidationError): verify_validation_outputs(first, repo_root=root, require_source_files=False)

    def test_manifest_and_extra_file_tamper_fail_offline(self):
        root = Path(tempfile.mkdtemp()); video = root / "v"; video.write_bytes(b"v"); checkpoint = root / "c"; checkpoint.write_bytes(b"c")
        metadata = VideoMetadata(video, 1.0, 1, 2, 2, 1.0); binding = ValidationVideoBinding("m", video, root, "v", sha256_file(video), metadata)
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, "j", "c"); inference = ValidationInferenceResult((), (), {}, sha256_file(checkpoint), sha256_file(video)); report = ValidationReport({}, {}, {}, {}, ())
        output = root / "out"; write_validation_outputs(output, truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="x", parameters={}, created_at="now")
        manifest = json.loads((output / "run-manifest.json").read_text()); manifest["match_id"] = "tampered"; (output / "run-manifest.json").write_bytes(json.dumps(manifest).encode())
        with self.assertRaises(ValidationError): verify_validation_outputs(output, repo_root=root, require_source_files=False)
        (output / "run-manifest.json").write_bytes(json.dumps({**manifest, "match_id": "m"}).encode()); (output / "extra.txt").write_text("x")
        with self.assertRaises(ValidationError): verify_validation_outputs(output, repo_root=root, require_source_files=False)

    def test_writer_rejects_stale_inference_hashes(self):
        root = Path(tempfile.mkdtemp()); video = root / "v"; video.write_bytes(b"v"); checkpoint = root / "c"; checkpoint.write_bytes(b"c")
        metadata = VideoMetadata(video, 1.0, 1, 2, 2, 1.0); binding = ValidationVideoBinding("m", video, root, "v", sha256_file(video), metadata)
        truth = ValidationTruth(binding, (), (), (), (), (), "truth-v1", True, "j", "c"); report = ValidationReport({}, {}, {}, {}, ())
        inference = ValidationInferenceResult((), (), {}, "0" * 64, sha256_file(video))
        with self.assertRaisesRegex(ValidationError, "checkpoint SHA"):
            write_validation_outputs(root / "out", truth=truth, inference=inference, report=report, checkpoint_path=checkpoint, code_sha="x", parameters={}, created_at="now")


if __name__ == "__main__":
    unittest.main()
