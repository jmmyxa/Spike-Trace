import csv
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from spiketrace.errors import ValidationError
from spiketrace.validation_contract import (
    assert_no_content_overlap,
    canonical_json_bytes,
    freeze_video_binding,
    load_video_binding,
    sha256_file,
    write_new_bytes,
    write_video_binding,
)


class ValidationContractTests(unittest.TestCase):
    def _video(self, path: Path) -> None:
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (32, 24))
        for _ in range(10):
            writer.write(np.zeros((24, 32, 3), dtype=np.uint8))
        writer.release()

    def test_hash_binding_and_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "data" / "fixture.avi"
            video.parent.mkdir()
            self._video(video)
            binding = freeze_video_binding(
                video,
                match_id="socal-cup-final-2025",
                expected_sha256=sha256_file(video),
                repo_root=root,
                expected_metadata={"fps": 10.0, "frame_count": 10, "width": 32, "height": 24},
            )
            self.assertEqual(binding.match_id, "socal-cup-final-2025")
            self.assertEqual(binding.repo_video_path, "data/fixture.avi")
            self.assertEqual(binding.metadata.frame_count, 10)

            with self.assertRaisesRegex(ValidationError, "SHA-256"):
                freeze_video_binding(video, match_id="socal-cup-final-2025", expected_sha256="0" * 64, repo_root=root)

    def test_atomic_publication_and_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "binding.json"
            write_new_bytes(destination, b"winner")
            with self.assertRaises(ValidationError):
                write_new_bytes(destination, b"loser")
            self.assertEqual(destination.read_bytes(), b"winner")

    def test_overlap_rejects_copy_match_selection_sha_and_allows_unrelated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "data" / "fixture.avi"
            video.parent.mkdir()
            self._video(video)
            binding = freeze_video_binding(video, match_id="match-a", expected_sha256=sha256_file(video), repo_root=root)
            binding_path = root / "binding.json"
            write_video_binding(binding_path, binding, repo_root=root)
            self.assertEqual(load_video_binding(binding_path, repo_root=root).match_id, "match-a")

            manifest = root / "manifest.csv"
            with manifest.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["video_path", "split", "match_id", "video_sha256"])
                writer.writeheader()
                writer.writerow({"video_path": "data/other.avi", "split": "val", "match_id": "match-a", "video_sha256": ""})
            selection = root / "selection.json"
            selection.write_text(json.dumps({"match_id": "other", "video_sha256": sha256_file(video)}), encoding="utf-8")
            with self.assertRaises(ValidationError):
                assert_no_content_overlap(binding, manifest_paths=[manifest], selection_paths=[], repo_root=root)
            with self.assertRaises(ValidationError):
                assert_no_content_overlap(binding, manifest_paths=[], selection_paths=[selection], repo_root=root)

            unrelated = root / "unrelated.json"
            unrelated.write_text(json.dumps({"match_id": "other", "sha256": "f" * 64}), encoding="utf-8")
            assert_no_content_overlap(binding, manifest_paths=[], selection_paths=[unrelated], repo_root=root)

    def test_canonical_json_is_stable(self):
        self.assertEqual(canonical_json_bytes({"b": 1, "a": 2}), b'{"a":2,"b":1}')

    def test_rejects_absolute_binding_repo_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "fixture.avi"
            self._video(video)
            binding = freeze_video_binding(video, match_id="m", expected_sha256=sha256_file(video), repo_root=root)
            invalid = replace(binding, repo_video_path=str(video))
            with self.assertRaises(ValidationError):
                write_video_binding(root / "binding.json", invalid, repo_root=root)

    def test_selection_missing_video_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "fixture.avi"
            self._video(video)
            binding = freeze_video_binding(video, match_id="m", expected_sha256=sha256_file(video), repo_root=root)
            selection = root / "selection.json"
            selection.write_text(json.dumps({"video": {"path": "missing.avi"}}), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "missing"):
                assert_no_content_overlap(binding, manifest_paths=[], selection_paths=[selection], repo_root=root)

    def test_manifest_split_must_be_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "fixture.avi"
            self._video(video)
            binding = freeze_video_binding(video, match_id="m", expected_sha256=sha256_file(video), repo_root=root)
            manifest = root / "manifest.csv"
            manifest.write_text("video_path,split,match_id\nother.avi,invalid,other\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "split"):
                assert_no_content_overlap(binding, manifest_paths=[manifest], selection_paths=[], repo_root=root)

    def test_manifest_short_row_fails_with_validation_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "fixture.avi"
            self._video(video)
            binding = freeze_video_binding(video, match_id="m", expected_sha256=sha256_file(video), repo_root=root)
            manifest = root / "manifest.csv"
            manifest.write_text("video_path,split,match_id\nother.avi\n", encoding="utf-8")
            with self.assertRaises(ValidationError):
                assert_no_content_overlap(binding, manifest_paths=[manifest], selection_paths=[], repo_root=root)


if __name__ == "__main__":
    unittest.main()
