from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from spiketrace.active_learning_selection import (
    _relative_posix_path,
    load_review_selection,
    validate_merged_review_source,
    write_review_selection,
)
from spiketrace.dual_crop_review import build_dual_crop_review
from spiketrace.errors import ActiveLearningError

ROOT = Path(__file__).resolve().parents[1]
DUAL_CROP_FIXTURES = ROOT / "tests" / "fixtures" / "dual_crop_review"


LITERAL_CLIPS = (
    {
        "clip_id": "clip-001",
        "ordinal": 1,
        "start_seconds": 0.0,
        "end_seconds": 1.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-002",
        "ordinal": 2,
        "start_seconds": 2.0,
        "end_seconds": 3.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-003",
        "ordinal": 3,
        "start_seconds": 4.0,
        "end_seconds": 5.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-004",
        "ordinal": 4,
        "start_seconds": 6.0,
        "end_seconds": 7.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-005",
        "ordinal": 5,
        "start_seconds": 8.0,
        "end_seconds": 9.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-006",
        "ordinal": 6,
        "start_seconds": 10.0,
        "end_seconds": 11.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-007",
        "ordinal": 7,
        "start_seconds": 12.0,
        "end_seconds": 13.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-008",
        "ordinal": 8,
        "start_seconds": 14.0,
        "end_seconds": 15.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-009",
        "ordinal": 9,
        "start_seconds": 16.0,
        "end_seconds": 17.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-010",
        "ordinal": 10,
        "start_seconds": 18.0,
        "end_seconds": 19.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-011",
        "ordinal": 11,
        "start_seconds": 20.0,
        "end_seconds": 21.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-012",
        "ordinal": 12,
        "start_seconds": 22.0,
        "end_seconds": 23.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-013",
        "ordinal": 13,
        "start_seconds": 24.0,
        "end_seconds": 25.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-014",
        "ordinal": 14,
        "start_seconds": 26.0,
        "end_seconds": 27.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-015",
        "ordinal": 15,
        "start_seconds": 28.0,
        "end_seconds": 29.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-016",
        "ordinal": 16,
        "start_seconds": 30.0,
        "end_seconds": 31.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-017",
        "ordinal": 17,
        "start_seconds": 32.0,
        "end_seconds": 33.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-018",
        "ordinal": 18,
        "start_seconds": 34.0,
        "end_seconds": 35.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-019",
        "ordinal": 19,
        "start_seconds": 36.0,
        "end_seconds": 37.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-020",
        "ordinal": 20,
        "start_seconds": 38.0,
        "end_seconds": 39.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-021",
        "ordinal": 21,
        "start_seconds": 40.0,
        "end_seconds": 41.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-022",
        "ordinal": 22,
        "start_seconds": 42.0,
        "end_seconds": 43.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-023",
        "ordinal": 23,
        "start_seconds": 44.0,
        "end_seconds": 45.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-024",
        "ordinal": 24,
        "start_seconds": 46.0,
        "end_seconds": 47.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-025",
        "ordinal": 25,
        "start_seconds": 48.0,
        "end_seconds": 49.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-026",
        "ordinal": 26,
        "start_seconds": 50.0,
        "end_seconds": 51.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-027",
        "ordinal": 27,
        "start_seconds": 52.0,
        "end_seconds": 53.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-028",
        "ordinal": 28,
        "start_seconds": 54.0,
        "end_seconds": 55.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-029",
        "ordinal": 29,
        "start_seconds": 56.0,
        "end_seconds": 57.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-030",
        "ordinal": 30,
        "start_seconds": 58.0,
        "end_seconds": 59.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-031",
        "ordinal": 31,
        "start_seconds": 60.0,
        "end_seconds": 61.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-032",
        "ordinal": 32,
        "start_seconds": 62.0,
        "end_seconds": 63.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-033",
        "ordinal": 33,
        "start_seconds": 64.0,
        "end_seconds": 65.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-034",
        "ordinal": 34,
        "start_seconds": 66.0,
        "end_seconds": 67.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-035",
        "ordinal": 35,
        "start_seconds": 68.0,
        "end_seconds": 69.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-036",
        "ordinal": 36,
        "start_seconds": 70.0,
        "end_seconds": 71.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-037",
        "ordinal": 37,
        "start_seconds": 72.0,
        "end_seconds": 73.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-038",
        "ordinal": 38,
        "start_seconds": 74.0,
        "end_seconds": 75.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-039",
        "ordinal": 39,
        "start_seconds": 76.0,
        "end_seconds": 77.0,
        "duration_seconds": 1.0,
    },
    {
        "clip_id": "clip-040",
        "ordinal": 40,
        "start_seconds": 78.0,
        "end_seconds": 79.0,
        "duration_seconds": 1.0,
    },
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_directory_link(link: Path, target: Path) -> str:
    try:
        link.symlink_to(target, target_is_directory=True)
        return "symlink"
    except OSError as symlink_error:
        if os.name != "nt":
            raise
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise OSError(
                "Could not create a directory symlink or junction: "
                f"{completed.stderr or completed.stdout}"
            ) from symlink_error
        return "junction"


def make_valid_selection_payload(source, clip_count=40):
    return {
        "format_version": 1,
        "selection_algorithm_version": "active-learning-selection-v1",
        "batch_id": "rangitoto-active-learning-round-01",
        "round_id": "round-01",
        "round_number": 1,
        "source": copy.deepcopy(source["source"]),
        "video": copy.deepcopy(source["video"]),
        "settings": {"clip_duration_ms": 1000},
        "previous_selections": [],
        "quota_summary": [],
        "coverage": {"start_seconds": 0.0, "end_seconds": 79.0},
        "clips": copy.deepcopy(list(LITERAL_CLIPS[:clip_count])),
    }


def make_compact_merged_payload(root: Path, video_path: Path, checkpoint_path: Path):
    video_sha256 = sha256_file(video_path)
    checkpoint_sha256 = sha256_file(checkpoint_path)
    audit = {
        "source_file": "outputs/inference/events.json",
        "source_file_sha256": "1" * 64,
        "normalized_payload_sha256": "2" * 64,
    }
    settings = {
        "checkpoint": "runs/model/best.pt",
        "checkpoint_sha256": checkpoint_sha256,
        "video_sha256": video_sha256,
    }
    return {
        "format_version": 2,
        "merge_format_version": 2,
        "video": {
            "path": "data/video.mp4",
            "fps": 25.0,
            "frame_count": 2500,
            "width": 1920,
            "height": 1080,
            "duration_seconds": 100.0,
        },
        "model_version": "rangitoto-test-v1",
        "settings": {
            "input_runs": {
                "far": copy.deepcopy(audit),
                "near": {
                    **copy.deepcopy(audit),
                    "source_file": "outputs/inference-near/events.json",
                    "source_file_sha256": "3" * 64,
                    "normalized_payload_sha256": "4" * 64,
                },
            }
        },
        "input_runs": {
            "far": {"settings": {**settings, "crop": [0, 0, 1920, 645]}},
            "near": {"settings": {**settings, "crop": [0, 255, 1920, 1080]}},
        },
    }


class SelectionContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.video_path = self.root / "data" / "video.mp4"
        self.video_path.parent.mkdir(parents=True)
        self.video_path.write_bytes(b"video")
        self.checkpoint_path = self.root / "runs" / "model" / "best.pt"
        self.checkpoint_path.parent.mkdir(parents=True)
        self.checkpoint_path.write_bytes(b"checkpoint")
        self.merged_json = self.root / "outputs" / "review" / "merged.json"
        self.merged_json.parent.mkdir(parents=True)
        self.merged_json.write_text(
            json.dumps(
                make_compact_merged_payload(
                    self.root, self.video_path, self.checkpoint_path
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self.output_json = self.root / "selections" / "round-01.json"
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            self.source = validate_merged_review_source(
                self.merged_json,
                repo_root=self.root,
            )
        self.selection_payload = make_valid_selection_payload(self.source)

    def _write_selection(self, payload=None):
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            return write_review_selection(
                self.selection_payload if payload is None else payload,
                self.output_json,
                repo_root=self.root,
            )

    def _load_selection(self):
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            return load_review_selection(self.output_json, repo_root=self.root)

    def _write_raw_selection(self, payload):
        self.output_json.parent.mkdir(parents=True, exist_ok=True)
        self.output_json.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def test_writes_source_and_video_hashes_without_a_generated_timestamp(self):
        source = self.source
        payload = make_valid_selection_payload(source, clip_count=40)
        self._write_selection(payload)
        self.assertEqual(payload["format_version"], 1)
        self.assertEqual(
            payload["selection_algorithm_version"],
            "active-learning-selection-v1",
        )
        self.assertEqual(
            payload["source"]["merged_json_sha256"], sha256_file(self.merged_json)
        )
        self.assertEqual(
            payload["source"]["checkpoint_sha256"],
            source["merged"]["input_runs"]["far"]["settings"]["checkpoint_sha256"],
        )
        self.assertIs(source["verification"]["checkpoint_file_checked"], True)
        self.assertNotIn("checkpoint_file_checked", payload["source"])
        self.assertEqual(payload["video"]["sha256"], sha256_file(self.video_path))
        self.assertNotIn("generated_at", payload)
        self.assertEqual(self._load_selection(), payload)

    def test_rejects_an_existing_output_without_changing_its_bytes(self):
        self.output_json.parent.mkdir(parents=True)
        self.output_json.write_bytes(b"keep")
        with self.assertRaisesRegex(ActiveLearningError, "already exists"):
            self._write_selection()
        self.assertEqual(self.output_json.read_bytes(), b"keep")

    def test_losing_output_race_preserves_the_winner_and_removes_temporary(self):
        real_link = os.link

        def publish_competitor_first(source, destination):
            Path(destination).write_bytes(b"winner")
            return real_link(source, destination)

        with (
            patch(
                "spiketrace.active_learning_selection.os.link",
                side_effect=publish_competitor_first,
            ),
            self.assertRaisesRegex(ActiveLearningError, "already exists"),
        ):
            self._write_selection()
        self.assertEqual(self.output_json.read_bytes(), b"winner")
        self.assertEqual(
            list(self.output_json.parent.glob(f".{self.output_json.name}.*.tmp")), []
        )

    def test_fsync_failure_leaves_no_output_or_temporary_sibling(self):
        with (
            patch(
                "spiketrace.active_learning_selection.os.fsync",
                side_effect=OSError("sync failed"),
            ),
            self.assertRaisesRegex(OSError, "sync failed"),
        ):
            self._write_selection()
        self.assertFalse(self.output_json.exists())
        self.assertEqual(
            list(self.output_json.parent.glob(f".{self.output_json.name}.*.tmp")), []
        )

    def test_rejects_duplicate_json_keys(self):
        self._write_selection()
        text = self.output_json.read_text(encoding="utf-8")
        duplicate = text.replace(
            '  "format_version": 1,',
            '  "format_version": 1,\n  "format_version": 1,',
            1,
        )
        self.output_json.write_text(duplicate, encoding="utf-8")
        with self.assertRaisesRegex(ActiveLearningError, "duplicate key"):
            self._load_selection()

    def test_rejects_non_finite_numbers(self):
        self._write_selection()
        text = self.output_json.read_text(encoding="utf-8")
        non_finite = text.replace(
            '    "clip_duration_ms": 1000',
            '    "clip_duration_ms": NaN',
            1,
        )
        self.output_json.write_text(non_finite, encoding="utf-8")
        with self.assertRaisesRegex(ActiveLearningError, "non-finite"):
            self._load_selection()

    def test_rejects_merged_and_video_hash_tampering(self):
        for target, replacement in (
            ("merged", b" \n"),
            ("video", b"tampered-video"),
        ):
            with self.subTest(target=target):
                self.output_json.unlink(missing_ok=True)
                self.merged_json.write_text(
                    json.dumps(
                        make_compact_merged_payload(
                            self.root, self.video_path, self.checkpoint_path
                        ),
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self.video_path.write_bytes(b"video")
                with patch(
                    "spiketrace.active_learning_selection.verify_dual_crop_review",
                    return_value={"verified": True},
                ):
                    source = validate_merged_review_source(
                        self.merged_json, repo_root=self.root
                    )
                payload = make_valid_selection_payload(source)
                self._write_selection(payload)
                if target == "merged":
                    self.merged_json.write_bytes(
                        self.merged_json.read_bytes() + replacement
                    )
                else:
                    self.video_path.write_bytes(replacement)
                with self.assertRaisesRegex(ActiveLearningError, "SHA-256"):
                    self._load_selection()

    def test_rejects_path_escape_and_invalid_format(self):
        escaped = copy.deepcopy(self.selection_payload)
        escaped["source"]["merged_json"] = "../outside.json"
        self._write_raw_selection(escaped)
        with self.assertRaisesRegex(ActiveLearningError, "escapes repository root"):
            self._load_selection()

        self.output_json.unlink()
        invalid_format = copy.deepcopy(self.selection_payload)
        invalid_format["format_version"] = 2
        with self.assertRaisesRegex(ActiveLearningError, "format version 1"):
            self._write_selection(invalid_format)

    def test_rejects_boolean_format_and_ordinal_values(self):
        for field in ("format", "ordinal"):
            with self.subTest(field=field):
                self.output_json.unlink(missing_ok=True)
                payload = copy.deepcopy(self.selection_payload)
                if field == "format":
                    payload["format_version"] = True
                else:
                    payload["clips"][0]["ordinal"] = True
                with self.assertRaises(ActiveLearningError):
                    self._write_selection(payload)

    def test_reports_a_missing_pinned_merged_file_as_active_learning_error(self):
        missing = copy.deepcopy(self.selection_payload)
        missing["source"]["merged_json"] = "outputs/review/missing.json"
        self._write_raw_selection(missing)
        with self.assertRaisesRegex(ActiveLearningError, "Cannot hash"):
            self._load_selection()

    def test_rejects_extra_root_source_and_video_fields(self):
        for section, field in (
            (None, "generated_at"),
            ("source", "checkpoint_file_checked"),
            ("video", "local_path"),
        ):
            with self.subTest(section=section, field=field):
                payload = copy.deepcopy(self.selection_payload)
                target = payload if section is None else payload[section]
                target[field] = True
                with self.assertRaisesRegex(
                    ActiveLearningError, "fields must be exactly"
                ):
                    self._write_selection(payload)

    def test_requires_exactly_40_unique_ordered_non_overlapping_clips(self):
        mutations = {
            "39 clips": lambda clips: clips.pop(),
            "duplicate ID": lambda clips: clips[1].update(clip_id="clip-001"),
            "wrong ordinal": lambda clips: clips[1].update(ordinal=3),
            "overlap": lambda clips: clips[1].update(start_seconds=0.5),
            "outside video": lambda clips: clips[-1].update(end_seconds=101.0),
            "wrong duration": lambda clips: clips[0].update(duration_seconds=2.0),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(self.selection_payload)
                mutate(payload["clips"])
                with self.assertRaises(ActiveLearningError):
                    self._write_selection(payload)

    def test_allows_absent_checkpoint_but_rejects_mismatched_local_checkpoint(self):
        self.checkpoint_path.unlink()
        with patch(
            "spiketrace.active_learning_selection.verify_dual_crop_review",
            return_value={"verified": True},
        ):
            result = validate_merged_review_source(
                self.merged_json, repo_root=self.root
            )
        self.assertIs(result["verification"]["checkpoint_file_checked"], False)
        self.assertEqual(result["source"], self.source["source"])

        self.checkpoint_path.write_bytes(b"wrong checkpoint")
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "Checkpoint SHA-256"),
        ):
            validate_merged_review_source(self.merged_json, repo_root=self.root)

    def test_rejects_far_and_near_checkpoint_pin_mismatch(self):
        merged = json.loads(self.merged_json.read_text(encoding="utf-8"))
        merged["input_runs"]["near"]["settings"]["checkpoint"] = "runs/other/best.pt"
        self.merged_json.write_text(
            json.dumps(merged, indent=2) + "\n", encoding="utf-8"
        )
        with (
            patch(
                "spiketrace.active_learning_selection.verify_dual_crop_review",
                return_value={"verified": True},
            ),
            self.assertRaisesRegex(ActiveLearningError, "same checkpoint"),
        ):
            validate_merged_review_source(self.merged_json, repo_root=self.root)

    def test_rejects_windows_drive_paths_under_posix_host_semantics(self):
        for value in ("C:secret.json", "C:/outside.json"):
            with (
                self.subTest(value=value),
                patch("spiketrace.active_learning_selection.Path", PurePosixPath),
                self.assertRaisesRegex(ActiveLearningError, "relative POSIX path"),
            ):
                _relative_posix_path(value, "source_file")

    def test_rejects_inference_audit_link_escape_during_validate_and_load(self):
        self._write_selection()
        with tempfile.TemporaryDirectory() as outside_temporary:
            outside = Path(outside_temporary).resolve()
            (outside / "events.json").write_text("{}\n", encoding="utf-8")
            inference_link = self.root / "outputs" / "inference"
            link_kind = create_directory_link(inference_link, outside)
            self.assertIn(link_kind, ("symlink", "junction"))

            with (
                patch(
                    "spiketrace.active_learning_selection.verify_dual_crop_review",
                    return_value={"verified": True},
                ),
                self.assertRaisesRegex(ActiveLearningError, "escapes repository root"),
            ):
                validate_merged_review_source(self.merged_json, repo_root=self.root)
            with self.assertRaisesRegex(
                ActiveLearningError, "escapes repository root"
            ):
                self._load_selection()

    def test_rejects_selection_pins_that_differ_from_the_merged_source(self):
        mutations = {
            "checkpoint hash": lambda payload: payload["source"].update(
                checkpoint_sha256="f" * 64
            ),
            "inference hash": lambda payload: payload["source"]["inference_runs"][
                "near"
            ].update(normalized_payload_sha256="e" * 64),
            "crop": lambda payload: payload["video"]["crops"].update(
                near=[0, 0, 1920, 1080]
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(self.selection_payload)
                mutate(payload)
                self._write_raw_selection(payload)
                with self.assertRaisesRegex(
                    ActiveLearningError, "do not match the verified merged JSON"
                ):
                    self._load_selection()
                self.output_json.unlink()

    def test_requires_extensible_container_types_and_preserves_clip_extensions(self):
        container_mutations = {
            "settings": [],
            "previous_selections": {},
            "quota_summary": {},
            "coverage": [],
        }
        for field, wrong_value in container_mutations.items():
            with self.subTest(field=field):
                payload = copy.deepcopy(self.selection_payload)
                payload[field] = wrong_value
                with self.assertRaisesRegex(ActiveLearningError, field):
                    self._write_selection(payload)

        extensible = copy.deepcopy(self.selection_payload)
        extensible["clips"][0]["future_evidence"] = {
            "score": 0.75,
            "labels": ["serve"],
        }
        self._write_selection(extensible)
        self.assertEqual(self._load_selection(), extensible)


class SelectionVerifierIntegrationTests(unittest.TestCase):
    def test_real_dual_crop_verifier_accepts_source_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            directory = Path(temporary)
            output_dir = directory / "review"
            build_dual_crop_review(
                DUAL_CROP_FIXTURES / "far.json",
                DUAL_CROP_FIXTURES / "near.json",
                output_dir,
                repo_root=ROOT,
            )
            merged_path = output_dir / "merged_candidates.json"
            result = validate_merged_review_source(
                merged_path,
                repo_root=ROOT,
                require_video=False,
            )
            self.assertEqual(result["source"]["format_version"], 2)
            self.assertIs(result["verification"]["checkpoint_file_checked"], False)

            tampered = json.loads(merged_path.read_text(encoding="utf-8"))
            tampered["events"][0]["confidence"] = 0.1
            merged_path.write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ActiveLearningError, "independent recomputation"
            ):
                validate_merged_review_source(
                    merged_path,
                    repo_root=ROOT,
                    require_video=False,
                )


if __name__ == "__main__":
    unittest.main()
